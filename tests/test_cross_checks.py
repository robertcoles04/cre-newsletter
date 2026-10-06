"""Two sources per number: REIT prices (Alpha Vantage + Stooq/Tiingo), Treasury yields
(FRED + Treasury.gov), Fed odds (Polymarket + Kalshi), provenance and the Data checks line.

Fixtures from real responses fetched 2026-10-06: kalshi_events.json (KXFEDDECISION events,
trimmed to two meetings) and stooq_blocked.html (Stooq's JavaScript bot check, which is why
Stooq is off by default). The Stooq CSV and Tiingo JSON bodies below follow the documented
formats (Stooq served no CSV to compare against)."""

import json
from datetime import date, datetime
from pathlib import Path

import httpx
import pytest
import respx

from src import main
from src.collect import kalshi, prices, reits
from src.config import ET
from src.deliver import deliver
from src.factsheet import (bucket_odds, build_factsheet, data_checks_line, fed_bucket,
                           kalshi_check)
from src.models import FedOdds, RatePoint, ReitQuote
from src.rates import cross_check_treasury
from src.render_html import data_room, market_summary
from src.store import connect, get_quotes, get_rates

FIXTURES = Path(__file__).parent / "fixtures"
AV_URL = "https://www.alphavantage.co/query"
TUE = date(2026, 10, 6)
MON = date(2026, 10, 5)
FRI = date(2026, 10, 2)
OCT28 = date(2026, 10, 28)
TICKERS = ["PLD", "AVB", "UDR", "NNN", "O"]
SOURCES = {"reit_etf": "VNQ", "reit_tickers": TICKERS, "reit_stooq": True}
CLOSES = {"VNQ": 89.15, "PLD": 110.0, "AVB": 200.0, "UDR": 40.0, "NNN": 42.0, "O": 57.0}


def _at(d: date, hour: int) -> datetime:
    return datetime(d.year, d.month, d.day, hour, tzinfo=ET)


def stooq_csv(close: float, prev: float = None, days=(FRI, MON)) -> str:
    prev = prev if prev is not None else close / 1.01
    rows = ["Date,Open,High,Low,Close,Volume"]
    for d, c in zip(days, (prev, close)):
        rows.append(f"{d.isoformat()},{c:.2f},{c:.2f},{c:.2f},{c:.2f},1000")
    return "\n".join(rows) + "\n"


def av_json(ticker: str, close: float, day: date = MON, chg: float = 1.0) -> dict:
    return {"Global Quote": {"01. symbol": ticker, "05. price": f"{close:.4f}",
                             "07. latest trading day": day.isoformat(),
                             "10. change percent": f"{chg:.4f}%"}}


@pytest.fixture
def world(monkeypatch):
    """Mocks Stooq, Alpha Vantage (+ ETF_PROFILE) and Tiingo; returns knobs and counters."""
    w = {"stooq": {t: stooq_csv(c) for t, c in CLOSES.items()},
         "av": {t: av_json(t, c) for t, c in CLOSES.items()},
         "tiingo": {}, "keys": {"ALPHA_VANTAGE_API_KEY": "k"},
         "av_calls": [], "stooq_calls": 0, "tiingo_calls": 0}
    monkeypatch.setattr(main, "env", lambda name, required=True: w["keys"].get(name))
    monkeypatch.setattr(main, "sleep", lambda s: None)
    real = reits.fetch_quotes
    monkeypatch.setattr(main.reits, "fetch_quotes",
                        lambda t, k, c: real(t, k, c, sleep=lambda s: None))

    def stooq(request):
        w["stooq_calls"] += 1
        sym = request.url.params["s"].split(".")[0].upper()
        body = w["stooq"].get(sym)
        return httpx.Response(200, text=body) if body else httpx.Response(200, text="No data")

    def av(request):
        fn, sym = request.url.params["function"], request.url.params["symbol"]
        w["av_calls"].append((fn, sym))
        if fn == "ETF_PROFILE":
            return httpx.Response(200, json={"dividend_yield": "0.036"})
        body = w["av"].get(sym)
        return httpx.Response(200, json=body if body else {"Information": "limit"})

    def tiingo(request):
        w["tiingo_calls"] += 1
        sym = request.url.path.split("/")[3].upper()
        assert request.headers["Authorization"] == "Token tk"
        assert "token" not in str(request.url)  # never the key in the URL
        return httpx.Response(200, json=w["tiingo"].get(sym, []))

    with respx.mock(assert_all_called=False) as mock:
        mock.get(prices.STOOQ_URL).mock(side_effect=stooq)
        mock.get(AV_URL).mock(side_effect=av)
        mock.get(url__startswith="https://api.tiingo.com/").mock(side_effect=tiingo)
        yield w


def collect(sources=SOURCES, now=_at(TUE, 6)):
    conn, problems, prov = connect(":memory:"), [], {}
    with httpx.Client() as client:
        quotes, vnq_yield = main._collect_reits(conn, sources, TUE, client, problems, now,
                                                provenance=prov)
    return conn, quotes, vnq_yield, problems, prov


# --- parsing --------------------------------------------------------------------------

def test_stooq_bot_check_page_is_blocked_never_parsed():
    with pytest.raises(prices.Blocked):
        prices.parse_stooq((FIXTURES / "stooq_blocked.html").read_text(encoding="utf-8"))


def test_stooq_csv_and_no_data():
    bars = prices.parse_stooq(stooq_csv(89.15, 88.0))
    assert bars == [(FRI, 88.0), (MON, 89.15)]
    with pytest.raises(ValueError):
        prices.parse_stooq("No data")


def test_tiingo_parse_uses_unadjusted_close():
    data = [{"date": "2026-10-02T00:00:00.000Z", "close": 88.0, "adjClose": 87.0},
            {"date": "2026-10-05T00:00:00.000Z", "close": 89.15, "adjClose": 88.1}]
    assert prices.parse_tiingo(data) == [(FRI, 88.0), (MON, 89.15)]


def test_quote_on_computes_change_vs_previous_bar():
    q = prices.quote_on("VNQ", [(FRI, 88.0), (MON, 89.76)], MON, "Stooq")
    assert (q.close, q.change_pct, q.source) == (89.76, 2.0, "Stooq")
    assert prices.quote_on("VNQ", [(MON, 89.76)], MON, "Stooq") is None  # nothing before it
    assert prices.quote_on("VNQ", [(FRI, 88.0)], MON, "Stooq") is None


# --- the cross-check rule ---------------------------------------------------------------

def _rq(close, src):
    return ReitQuote("VNQ", MON, close, 1.0, src)


def test_cross_check_agree_disagree_tiebreak_single():
    q, prov, note = prices.cross_check("VNQ", {"AV": _rq(89.15, "AV"),
                                               "Stooq": _rq(89.50, "Stooq")})
    assert prov == "AV+Stooq" and note is None and q.close == 89.50  # Stooq is primary
    q, prov, note = prices.cross_check("VNQ", {"AV": _rq(95.0, "AV"),
                                               "Stooq": _rq(89.15, "Stooq")})
    assert q is None and note == "reits: VNQ sources disagree (AV 95.00, Stooq 89.15)"
    q, prov, note = prices.cross_check("VNQ", {"AV": _rq(95.0, "AV"),
                                               "Stooq": _rq(89.15, "Stooq"),
                                               "Tiingo": _rq(89.20, "Tiingo")})
    assert q.close == 89.15 and prov == "Stooq+Tiingo" and note is None  # majority wins
    q, prov, note = prices.cross_check("VNQ", {"AV": _rq(95.0, "AV"),
                                               "Stooq": _rq(89.15, "Stooq"),
                                               "Tiingo": _rq(80.0, "Tiingo")})
    assert q is None and "AV 95.00, Stooq 89.15, Tiingo 80.00" in note
    q, prov, note = prices.cross_check("VNQ", {"AV": _rq(95.0, "AV")})
    assert q.close == 95.0 and prov == "AV" and note is None
    assert prices.cross_check("VNQ", {}) == (None, "", None)


def test_confirm_set_rotates_three_reits_plus_the_etf():
    a = reits.confirm_set("VNQ", TICKERS, TUE)
    b = reits.confirm_set("VNQ", TICKERS, MON)
    assert a[0] == "VNQ" and len(a) == 4 and len(set(a)) == 4 and a != b


# --- collect: REIT prices from two sources ---------------------------------------------

def test_stooq_covers_all_so_alpha_vantage_stays_within_quota(world):
    conn, quotes, vnq_yield, problems, prov = collect()
    assert len(world["av_calls"]) <= 5
    assert [fn for fn, _ in world["av_calls"]].count("GLOBAL_QUOTE") == 4
    assert ("ETF_PROFILE", "VNQ") in world["av_calls"] and vnq_yield == 0.036
    confirmed = {s for fn, s in world["av_calls"] if fn == "GLOBAL_QUOTE"}
    assert "VNQ" in confirmed
    assert {q.ticker for q in quotes} == set(CLOSES)
    assert all(prov[t] == ("AV+Stooq" if t in confirmed else "Stooq") for t in CLOSES)
    assert problems == []
    stored = {q.ticker: q.source for q in get_quotes(conn, MON)}
    assert stored["VNQ"] == "AV+Stooq"  # kept for a same-day rerun


def test_disagreement_without_tiebreaker_drops_vnq_to_na(world):
    world["av"]["VNQ"] = av_json("VNQ", 95.0)
    conn, quotes, vnq_yield, problems, prov = collect()
    assert "VNQ" not in {q.ticker for q in quotes} and "VNQ" not in prov
    assert "reits: VNQ sources disagree (AV 95.00, Stooq 89.15)" in problems
    v = build_factsheet(conn, TUE, None, quotes, [], vnq_yield)["values"]
    assert v["VNQ"] == "n/a" and v["VNQ_CHG"] == "n/a"


def test_tiingo_breaks_the_tie(world):
    world["keys"]["TIINGO_API_KEY"] = "tk"
    world["av"]["VNQ"] = av_json("VNQ", 95.0)
    world["tiingo"] = {t: [{"date": "2026-10-02T00:00:00.000Z", "close": c / 1.01},
                           {"date": "2026-10-05T00:00:00.000Z", "close": c}]
                       for t, c in CLOSES.items()}
    _, quotes, _, problems, prov = collect()
    vnq = next(q for q in quotes if q.ticker == "VNQ")
    assert vnq.close == 89.15 and prov["VNQ"] == "Stooq+Tiingo"
    assert not any("disagree" in p for p in problems)
    assert world["tiingo_calls"] == len(CLOSES)


def test_single_source_is_published_with_a_note(world):
    world["av"].pop("VNQ")  # Alpha Vantage limit answer for VNQ: only Stooq has it
    _, quotes, _, problems, prov = collect()
    assert prov["VNQ"] == "Stooq" and "VNQ" in {q.ticker for q in quotes}
    assert "reits: VNQ single source (Stooq)" in problems
    assert not any(p.startswith("reits: failed") for p in problems)


def test_ticker_stooq_lacks_falls_back_to_alpha_vantage(world):
    world["stooq"].pop("NNN")
    _, quotes, _, problems, prov = collect()
    assert ("GLOBAL_QUOTE", "NNN") in world["av_calls"]
    assert prov["NNN"] == "AV" and "reits: NNN single source (AV)" in problems


def test_no_valid_source_fails_as_before(world):
    world["stooq"].pop("NNN")
    world["av"]["NNN"] = av_json("NNN", 42.0, day=date(2026, 8, 17))
    _, quotes, _, problems, _ = collect()
    assert "NNN" not in {q.ticker for q in quotes}
    assert "reits: NNN quote is stale (2026-08-17)" in problems


def test_stooq_bot_check_stops_stooq_and_alpha_vantage_covers_everything(world):
    blocked = (FIXTURES / "stooq_blocked.html").read_text(encoding="utf-8")
    world["stooq"] = {t: blocked for t in CLOSES}
    _, quotes, _, problems, prov = collect()
    assert world["stooq_calls"] == 1  # never hammers a bot check
    assert "reits: stooq unavailable (blocked by a bot check)" in problems
    assert any(p.startswith("reits: prices from Alpha Vantage only") for p in problems)
    assert {q.ticker for q in quotes} == set(CLOSES) and set(prov.values()) == {"AV"}
    assert not any("single source" in p for p in problems)


def test_stooq_off_by_default_and_no_tiingo_without_key(world):
    _, quotes, _, _, _ = collect({**SOURCES, "reit_stooq": False})
    assert world["stooq_calls"] == 0 and world["tiingo_calls"] == 0
    assert len(quotes) == len(CLOSES)


def test_after_close_free_source_sets_the_day(world):
    world["stooq"] = {t: stooq_csv(c, days=(MON, TUE)) for t, c in CLOSES.items()}
    world["av"] = {t: av_json(t, c, day=TUE) for t, c in CLOSES.items()}
    _, quotes, _, problems, _ = collect(now=_at(TUE, 17))
    assert {q.date for q in quotes} == {TUE} and problems == []


# --- rates: FRED vs Treasury.gov ---------------------------------------------------------

def _pts(sid, pairs):
    return [RatePoint(sid, d, v) for d, v in pairs]


def test_treasury_cross_check_agree():
    conn = connect(":memory:")
    fred = _pts("DGS10", [(FRI, 4.10), (MON, 4.20)])
    r = cross_check_treasury(conn, "DGS10", fred, _pts("DGS10", [(FRI, 4.10), (MON, 4.21)]))
    assert r.match is True and r.sources == "FRED+Treasury" and r.note == ""


def test_treasury_cross_check_disagree_prefers_treasury():
    conn = connect(":memory:")
    from src.store import save_rates
    fred = _pts("DGS10", [(FRI, 4.10), (MON, 4.20)])
    save_rates(conn, fred)
    r = cross_check_treasury(conn, "DGS10", fred, _pts("DGS10", [(FRI, 4.10), (MON, 4.25)]))
    assert r.match is False and r.sources == "Treasury"
    assert r.note == "rates: DGS10 FRED 4.20 vs Treasury 4.25"
    assert get_rates(conn, "DGS10", FRI)[-1].value == 4.25


def test_treasury_newer_day_is_saved_and_named():
    conn = connect(":memory:")
    r = cross_check_treasury(conn, "DGS2", _pts("DGS2", [(FRI, 3.5)]),
                             _pts("DGS2", [(FRI, 3.5), (MON, 3.6)]))
    assert r.match is True and r.sources == "Treasury"
    assert [p.date for p in get_rates(conn, "DGS2", FRI)] == [MON]
    r = cross_check_treasury(conn, "DGS5", _pts("DGS5", [(MON, 3.9)]), [])
    assert r.match is None and r.sources == "FRED"


def test_collect_turns_rate_notes_into_problems(monkeypatch):
    from src.models import SourceResult
    monkeypatch.setattr(main, "env", lambda name, required=True: None)
    monkeypatch.setattr(main.polymarket, "fetch_fed_odds", lambda c, t: None)
    monkeypatch.setattr(main.kalshi, "fetch_fed_odds", lambda c, t: None)
    monkeypatch.setattr(main.trepp, "cmbs_values", lambda c, d: None)
    monkeypatch.setattr(main, "collect_rates", lambda *a: [
        SourceResult("DGS10", True, sources="Treasury", match=False,
                     note="rates: DGS10 FRED 4.20 vs Treasury 4.25"),
        SourceResult("SOFR", True, sources="FRED")])
    problems = []
    out = main._collect(connect(":memory:"), {"feeds": [], "google_news": [],
                                              "fred_series": []}, TUE, None, problems)
    assert "rates: DGS10 FRED 4.20 vs Treasury 4.25" in problems
    assert out[3]["sources"] == {"DGS10": "Treasury", "SOFR": "FRED"}
    assert out[3]["rate_checks"] == {"DGS10": False}


# --- Fed odds: Kalshi --------------------------------------------------------------------

def _kalshi_data():
    return json.loads((FIXTURES / "kalshi_events.json").read_text(encoding="utf-8"))


def test_kalshi_parse_real_events_for_the_meeting():
    odds = kalshi.parse_events(_kalshi_data(), OCT28)
    assert odds.end_date == OCT28 and odds.outcomes[0][0] == "Fed maintains rate"
    assert bucket_odds(odds)["hold"] == pytest.approx(81.5, abs=0.1)
    assert kalshi.parse_events(_kalshi_data(), date(2026, 11, 4)) is None
    assert fed_bucket("Fed maintains rate") == "hold"
    assert fed_bucket("Cut >25bps") == "cut" and fed_bucket("Hike 25bps") == "hike"


@respx.mock
def test_kalshi_fetch_uses_series_and_raises_when_down():
    route = respx.get(kalshi.EVENTS_URL).mock(return_value=httpx.Response(200, json=_kalshi_data()))
    with httpx.Client() as client:
        assert kalshi.fetch_fed_odds(client, OCT28) is not None
        assert route.calls[0].request.url.params["series_ticker"] == "KXFEDDECISION"
        route.mock(return_value=httpx.Response(503))
        with pytest.raises(httpx.HTTPStatusError):
            kalshi.fetch_fed_odds(client, OCT28)


def test_kalshi_unreachable_is_one_note(monkeypatch):
    monkeypatch.setattr(main, "env", lambda name, required=True: None)
    monkeypatch.setattr(main.polymarket, "fetch_fed_odds", lambda c, t: None)
    monkeypatch.setattr(main.trepp, "cmbs_values", lambda c, d: None)

    def down(c, t):
        raise httpx.ConnectError("down")
    monkeypatch.setattr(main.kalshi, "fetch_fed_odds", down)
    problems = []
    out = main._collect(connect(":memory:"), {"feeds": [], "google_news": [],
                                              "fred_series": []}, TUE, None, problems)
    assert "kalshi: unavailable" in problems and out[3]["kalshi"] is None
    assert not any(p.startswith("kalshi") and p != "kalshi: unavailable" for p in problems)


def _poly(hold):
    return FedOdds("Fed Decision in October?", OCT28,
                   [("No change", hold), ("25 bps decrease", 1 - hold)])


def test_kalshi_check_agree_and_disagree():
    k = kalshi.parse_events(_kalshi_data(), OCT28)
    values = {"FED_HOLD": "85.0%"}
    assert kalshi_check(values, k) == ("Polymarket+Kalshi", None)
    assert values["KALSHI_HOLD"] == "81.5%"
    values = {"FED_HOLD": "60.0%"}
    prov, note = kalshi_check(values, k)
    assert prov == "Polymarket (Kalshi differs)"
    assert note == "fed: Polymarket hold 60.0% vs Kalshi hold 81.5%"
    assert kalshi_check({"FED_HOLD": "85.0%"}, None) == ("Polymarket", None)


def test_factsheet_sources_kalshi_line_and_data_checks():
    conn = connect(":memory:")
    quotes = [ReitQuote("VNQ", MON, 89.15, -0.4, "AV+Stooq"),
              ReitQuote("PLD", MON, 110.0, 1.0, "Stooq")]
    problems = []
    sheet = build_factsheet(conn, TUE, _poly(0.6), quotes, problems, None,
                            kalshi=kalshi.parse_events(_kalshi_data(), OCT28),
                            provenance={"VNQ": "AV+Stooq", "DGS10": "FRED+Treasury"},
                            rate_checks={"DGS10": True, "DGS2": True})
    assert sheet["sources"] == {"VNQ": "AV+Stooq", "DGS10": "FRED+Treasury",
                                "FED": "Polymarket (Kalshi differs)"}
    assert "fed: Polymarket hold 60.0% vs Kalshi hold 81.5%" in problems
    v = sheet["values"]
    assert v["DATA_CHECKS"] == ("Prices confirmed by 2 sources for 1 of 2 REITs. "
                                "Treasury yields matched across FRED and Treasury.gov. "
                                "Fed odds differ between Polymarket and Kalshi; both shown.")
    room = data_room(v, TUE)
    assert '<p class="checks">Data checks: Prices confirmed by 2 sources' in room
    assert "Kalshi: hold 81.5%" in market_summary(v, {}, "")


def test_data_checks_line_names_a_treasury_mismatch_and_is_empty_when_unchecked():
    line = data_checks_line([], {"DGS10": True, "DGS2": False}, None)
    assert line == "Treasury.gov used for the 2-Year (FRED showed a different number)."
    assert data_checks_line([], {}, None) == ""
    assert data_checks_line([ReitQuote("VNQ", MON, 89.0, 0.1, "AV")], {}, None) == (
        "REIT prices came from a single source today.")
    assert data_room({}, TUE) == ""


def test_deliver_saves_sources_in_the_issue_json(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    sheet = {"date": "2026-10-06", "day_type": "weekday", "values": {},
             "sources": {"VNQ": "AV+Stooq", "FED": "Polymarket+Kalshi"}}
    deliver(conn, TUE, "# Hi\n", [], None, tmp_path, "weekday", None, dry_run=True,
            factsheet=sheet)
    data = json.loads((tmp_path / "issues" / "2026-10-06.json").read_text(encoding="utf-8"))
    assert data["sources"] == {"VNQ": "AV+Stooq", "FED": "Polymarket+Kalshi"}
