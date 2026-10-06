"""Data-freshness fixes: REIT quote dates, stored-quote reuse, Fed market matching,
chart as-of captions, and the extra cron times."""

import json
from datetime import date, datetime
from pathlib import Path

import httpx
import pytest
import respx
import yaml

from src import main
from src.chart import reit_alt
from src.collect import reits
from src.config import ET
from src.factsheet import build_factsheet
from src.models import FedOdds, RatePoint, ReitQuote
from src.render_html import chart_caption, hint_for
from src.store import connect, get_quotes, get_rates, save_quotes, save_rates

FIXTURES = Path(__file__).parent / "fixtures"
AV_URL = "https://www.alphavantage.co/query"
TUE = date(2026, 10, 6)
MON = date(2026, 10, 5)
FRI = date(2026, 10, 2)
SOURCES = {"feeds": [], "google_news": [], "fred_series": [], "reit_etf": "VNQ",
           "reit_tickers": ["PLD", "AVB", "UDR", "NNN"]}


def _at(d: date, hour: int, minute: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=ET)


def _q(ticker, d, chg=1.0, close=50.0):
    return ReitQuote(ticker, d, close, chg)


# --- expected trading day ----------------------------------------------------------

def test_accepted_days_before_and_after_the_close():
    assert reits.accepted_days(TUE, _at(TUE, 6)) == [MON]
    assert reits.accepted_days(TUE, _at(TUE, 16, 29)) == [MON]
    assert reits.accepted_days(TUE, _at(TUE, 16, 30)) == [TUE, MON]
    assert reits.accepted_days(TUE) == [MON]  # no start time: morning rules
    # Monday expects Friday's close; a weekend run never accepts its own date.
    assert reits.accepted_days(MON, _at(MON, 6)) == [FRI]
    assert reits.accepted_days(date(2026, 10, 4), _at(date(2026, 10, 4), 17)) == [FRI]


def test_day_after_a_market_holiday_expects_the_close_before_it():
    # Thanksgiving 2026-11-26: the Friday run expects Wednesday's close.
    assert reits.accepted_days(date(2026, 11, 27), _at(date(2026, 11, 27), 6)) == [
        date(2026, 11, 25)]


def test_split_stale_drops_old_quotes():
    quotes = [_q("VNQ", MON), _q("UDR", MON, 1.7), _q("AVB", date(2026, 8, 17), 0.0)]
    fresh, stale, day = reits.split_stale(quotes, [MON])
    assert [q.ticker for q in fresh] == ["VNQ", "UDR"]
    assert [q.ticker for q in stale] == ["AVB"]
    assert day == MON
    # The Oct 5 issue's case seen from a Tuesday run: Friday closes are all stale.
    fresh, stale, day = reits.split_stale([_q("VNQ", FRI), _q("NNN", FRI)], [MON])
    assert (fresh, day) == ([], None) and len(stale) == 2


def test_split_stale_never_mixes_two_closes():
    # After the close some tickers already show today, others still yesterday: one day wins.
    quotes = [_q("VNQ", TUE), _q("PLD", TUE), _q("UDR", MON)]
    fresh, stale, day = reits.split_stale(quotes, [TUE, MON])
    assert day == TUE and [q.ticker for q in stale] == ["UDR"]


@respx.mock
def test_stale_quote_is_never_retried():
    # av_quote.json is dated 2026-10-02: a successful but stale answer, not a limit.
    route = respx.get(AV_URL).mock(return_value=httpx.Response(
        200, text=(FIXTURES / "av_quote.json").read_text(encoding="utf-8")))
    naps = []
    with httpx.Client() as client:
        quotes, failed = reits.fetch_quotes(["VNQ", "AVB"], "k", client, sleep=naps.append)
    assert failed == [] and route.call_count == 2  # one call per ticker, no retry
    assert reits.RETRY_WAIT_SECONDS not in naps


# --- collect: stale handling and reuse ----------------------------------------------

@pytest.fixture
def collect_env(monkeypatch):
    monkeypatch.setattr(main, "env", lambda name, required=True: "key")
    monkeypatch.setattr(main, "sleep", lambda s: None)
    monkeypatch.setattr(main.reits, "fetch_etf_yield", lambda e, k, c: 0.036)
    calls = []

    def fake_quotes(tickers, key, client):
        calls.append(list(tickers))
        return FAKE_QUOTES[:], []
    monkeypatch.setattr(main.reits, "fetch_quotes", fake_quotes)
    return calls


FAKE_QUOTES = []


def test_collect_reits_drops_stale_quotes_and_reports_them(collect_env):
    FAKE_QUOTES[:] = [_q("VNQ", MON, -0.4, 89.15), _q("PLD", MON, -0.6),
                      _q("AVB", date(2026, 8, 17), 0.0), _q("UDR", MON, 1.7),
                      _q("NNN", MON, -1.4)]
    conn, problems = connect(":memory:"), []
    quotes, vnq_yield = main._collect_reits(conn, SOURCES, TUE, None, problems, _at(TUE, 6))
    assert "AVB" not in {q.ticker for q in quotes}
    assert "reits: AVB quote is stale (2026-08-17)" in problems
    assert vnq_yield == 0.036
    # Only fresh quotes are stored; the yield is kept for a same-day rerun.
    assert {q.ticker for q in get_quotes(conn, date(2026, 1, 1))} == {"VNQ", "PLD", "UDR", "NNN"}
    assert [p.value for p in get_rates(conn, main.VNQ_YIELD_SERIES, MON)] == [0.036]
    sheet = build_factsheet(conn, TUE, None, quotes, [], vnq_yield)
    v = sheet["values"]
    assert v["REIT_ASOF"] == "Oct 5"
    assert v["REIT_UP"] == "UDR +1.7%" and v["REIT_DOWN"] == "NNN -1.4%"
    assert "AVB" not in {m["ticker"] for m in sheet["reit_moves"]}


def test_stale_vnq_becomes_na(collect_env):
    FAKE_QUOTES[:] = [_q("VNQ", FRI, 0.4, 89.50), _q("PLD", MON), _q("UDR", MON),
                      _q("NNN", MON), _q("AVB", MON)]
    conn, problems = connect(":memory:"), []
    quotes, _ = main._collect_reits(conn, SOURCES, TUE, None, problems, _at(TUE, 6))
    assert "reits: VNQ quote is stale (2026-10-02)" in problems
    v = build_factsheet(conn, TUE, None, quotes, [], None)["values"]
    assert v["VNQ"] == "n/a" and v["VNQ_CHG"] == "n/a"
    assert v["REIT_ASOF"] == "Oct 5"


def test_rerun_reuses_stored_quotes_without_calling_alpha_vantage(collect_env, capsys):
    conn = connect(":memory:")
    save_quotes(conn, [_q("VNQ", MON, -0.4, 89.15), _q("PLD", MON), _q("UDR", MON, 1.7),
                       _q("NNN", MON, -1.4), _q("AVB", date(2026, 8, 17), 0.0)])
    save_rates(conn, [RatePoint(main.VNQ_YIELD_SERIES, MON, 0.036)])
    problems = []
    quotes, vnq_yield = main._collect_reits(conn, SOURCES, TUE, None, problems, _at(TUE, 8))
    assert collect_env == []  # no Alpha Vantage call
    assert [q.ticker for q in quotes] == ["VNQ", "PLD", "UDR", "NNN"]
    assert vnq_yield == 0.036
    assert "reits: reused stored quotes for 2026-10-05" in capsys.readouterr().out
    assert problems == ["reits: no stored quote for AVB"]


def test_no_reuse_when_only_a_few_tickers_are_stored(collect_env):
    FAKE_QUOTES[:] = [_q(t, MON) for t in ("VNQ", "PLD", "AVB", "UDR", "NNN")]
    conn = connect(":memory:")
    save_quotes(conn, [_q("VNQ", MON), _q("PLD", MON)])  # 2 of 5: not "most"
    main._collect_reits(conn, SOURCES, TUE, None, [], _at(TUE, 8))
    assert len(collect_env) == 1
    # Stored rows from an older day never count as this run's data.
    conn2 = connect(":memory:")
    save_quotes(conn2, [_q(t, FRI) for t in ("VNQ", "PLD", "AVB", "UDR", "NNN")])
    main._collect_reits(conn2, SOURCES, TUE, None, [], _at(TUE, 8))
    assert len(collect_env) == 2


# --- Fed odds: meeting passed in, problem text, as-of ---------------------------------

class _Offline:
    def get(self, url, **kw):
        raise httpx.ConnectError("offline")


def test_collect_passes_the_fomc_meeting_and_notes_a_missing_market(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(main, "env", lambda name, required=True: None)
    monkeypatch.setattr(main.polymarket, "fetch_fed_odds",
                        lambda c, meeting: seen.append(meeting))
    problems = []
    # Oct 29: the Oct 28 meeting is over, so the Dec 9 meeting is asked for.
    main._collect(connect(str(tmp_path / "t.db")), SOURCES, date(2026, 10, 29), _Offline(),
                  problems, now=_at(date(2026, 10, 29), 6))
    assert seen == [date(2026, 12, 9)]
    assert "polymarket: no market for Dec 9" in problems


def test_fed_odds_carry_the_run_date_et(monkeypatch, tmp_path):
    odds = FedOdds("Fed Decision in October?", date(2026, 10, 28), [("No change", 0.8),
                                                                    ("25 bps decrease", 0.2)])
    monkeypatch.setattr(main, "env", lambda name, required=True: None)
    monkeypatch.setattr(main.polymarket, "fetch_fed_odds", lambda c, m: odds)
    got = main._collect(connect(str(tmp_path / "t.db")), SOURCES, TUE, _Offline(), [],
                        now=_at(TUE, 9))[0]
    assert got.as_of == TUE
    v = build_factsheet(connect(":memory:"), TUE, got, [], [], None)["values"]
    assert v["FED_ASOF"] == "Oct 6"


# --- page: as-of in hints, captions and alt -----------------------------------------

def test_reit_rows_name_their_own_day_when_it_differs_from_rates():
    values = {"RATES_ASOF": "Oct 5", "REIT_ASOF": "Oct 2", "REIT_UP_TYPE": "Owns apartments"}
    assert hint_for("VNQ", values) == "A fund holding about 150 REITs (as of Oct 2)"
    assert hint_for("REIT_UP", values) == "Owns apartments (as of Oct 2)"
    assert "as of" not in hint_for("REIT_UP", {**values, "REIT_ASOF": "Oct 5"})
    assert "as of" not in hint_for("VNQ", {"RATES_ASOF": "Oct 5"})


def test_chart_captions_carry_their_as_of():
    values = {"FED_ASOF": "Oct 6", "REIT_ASOF": "Oct 5"}
    assert chart_caption("fed", values).endswith("Odds as of Oct 6.")
    assert chart_caption("reits", values).startswith("Daily move, Oct 5 close")
    assert "as of" not in chart_caption("fed", {})
    assert "today" not in chart_caption("curve", {}).lower()
    assert reit_alt([{"ticker": "UDR", "chg_pct": 1.7}], "Oct 5").startswith(
        "Bar chart of moves for the Oct 5 close")


def test_published_oct5_issue_names_its_reit_day():
    values = json.loads(Path("issues/2026-10-05.json").read_text(encoding="utf-8"))["values"]
    assert values["REIT_ASOF"] == "Oct 2"
    assert values["VNQ"] == "$89.50" and values["REIT_UP"] == "NNN +1.9%"


# --- workflow ------------------------------------------------------------------------

def test_daily_cron_has_extra_odd_minute_times():
    wf = yaml.safe_load(Path(".github/workflows/daily.yml").read_text(encoding="utf-8"))
    on = wf.get("on", wf.get(True))
    crons = {c["cron"] for c in on["schedule"]}
    for hh, mm in ((9, 13), (9, 43), (10, 13), (10, 43), (11, 13), (11, 43)):
        assert f"{mm} {hh} * * *" in crons
    assert {"7 9 * * *", "7 10 * * *"} <= crons
