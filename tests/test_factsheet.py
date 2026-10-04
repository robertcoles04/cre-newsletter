from datetime import date, datetime, timedelta, timezone

import yaml

from src.chart import rate_chart
from src.config import ET
from src.factsheet import build_factsheet, day_type
from src.models import FedOdds, RatePoint, ReitQuote
from src.store import connect, save_quotes, save_rates

TUE = datetime(2026, 10, 6, 5, 0, tzinfo=ET)
MON = datetime(2026, 10, 5, 5, 0, tzinfo=ET)


def add_item(conn, n, section="top", importance=5, priority=50, hours_old=2,
             anchor=TUE, also="[]"):
    pub = (anchor - timedelta(hours=hours_old)).astimezone(timezone.utc).isoformat(
        timespec="seconds")
    cur = conn.execute(
        "INSERT INTO items (source, url, canonical_url, title, published_at, summary,"
        " priority, also_covered, section, importance) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("Src", f"https://x.com/{n}", f"x.com/{n}", f"Title {n}", pub, "sum",
         priority, also, section, importance))
    conn.execute("UPDATE items SET cluster_id = id WHERE id = ?", (cur.lastrowid,))
    conn.commit()
    return cur.lastrowid


def fs(conn, d, odds=None, quotes=(), vnq_yield=None):
    return build_factsheet(conn, d, odds, list(quotes), [], vnq_yield)


def test_day_type():
    assert day_type(date(2026, 10, 5)) == "weekday"
    assert day_type(date(2026, 10, 9)) == "friday"
    assert day_type(date(2026, 10, 10)) == "saturday"
    assert day_type(date(2026, 10, 11)) == "sunday"


def test_friday_has_3_top_and_weekday_5():
    conn = connect(":memory:")
    for n in range(12):
        add_item(conn, n, importance=n, anchor=datetime(2026, 10, 9, 5, tzinfo=ET))
    fri = fs(conn, date(2026, 10, 9))
    assert len(fri["top"]) == 3
    assert fri["quick_hits"] == []
    conn2 = connect(":memory:")
    for n in range(14):
        add_item(conn2, n, importance=n)
    wk = fs(conn2, date(2026, 10, 6))
    assert len(wk["top"]) == 5
    assert len(wk["quick_hits"]) == 8
    assert wk["top"][0]["title"] == "Title 13"
    assert set(wk["top"][0]) == {"id", "title", "source", "url", "summary", "also_covered"}


def test_priority_breaks_importance_ties_and_also_covered_is_list():
    conn = connect(":memory:")
    add_item(conn, 1, importance=8, priority=10)
    add_item(conn, 2, importance=8, priority=90, also='["Bisnow"]')
    top = fs(conn, date(2026, 10, 6))["top"]
    assert [t["title"] for t in top] == ["Title 2", "Title 1"]
    assert top[0]["also_covered"] == ["Bisnow"]


def test_monday_uses_78h_lookback():
    conn = connect(":memory:")
    add_item(conn, 1, hours_old=60, anchor=MON)
    assert len(fs(conn, date(2026, 10, 5))["top"]) == 1
    conn2 = connect(":memory:")
    add_item(conn2, 1, hours_old=60, anchor=TUE)
    assert fs(conn2, date(2026, 10, 6))["top"] == []


def test_missing_rates_render_na():
    conn = connect(":memory:")
    v = fs(conn, date(2026, 10, 6))["values"]
    for k in ("DGS10", "DGS10_CHG", "DGS5", "SOFR", "DFF", "RATES_ASOF",
              "FED_TOP", "VNQ", "VNQ_CHG", "REIT_UP", "REIT_DOWN", "VNQ_YIELD", "SPREAD_10Y"):
        assert v[k] == "n/a", k


def test_rates_and_market_formats():
    conn = connect(":memory:")
    save_rates(conn, [RatePoint("DGS10", date(2026, 10, 1), 4.60),
                      RatePoint("DGS10", date(2026, 10, 2), 4.62)])
    odds = FedOdds("Oct", date(2026, 10, 29), [("No change", 0.825), ("Cut 25", 0.175)])
    quotes = [ReitQuote("VNQ", date(2026, 10, 5), 85.12, 0.8),
              ReitQuote("O", date(2026, 10, 5), 60.0, 2.1),
              ReitQuote("PLD", date(2026, 10, 5), 110.0, -1.2)]
    v = fs(conn, date(2026, 10, 6), odds, quotes, 0.041)["values"]
    assert v["DGS10"] == "4.62%" and v["DGS10_CHG"] == "+2 bps"
    assert v["RATES_ASOF"] == "Oct 2"
    assert v["FED_MEETING"] == "Oct 28" and v["FED_TOP"] == "No change 82.5%"
    assert v["VNQ"] == "$85.12" and v["VNQ_CHG"] == "+0.8%"
    assert v["REIT_UP"] == "O +2.1%" and v["REIT_DOWN"] == "PLD -1.2%"
    assert v["VNQ_YIELD"] == "4.10%"


def test_spread_to_10y():
    conn = connect(":memory:")
    save_rates(conn, [RatePoint("DGS10", date(2026, 10, 2), 4.62)])
    v = fs(conn, date(2026, 10, 6), vnq_yield=0.041)["values"]
    assert v["SPREAD_10Y"] == "-52 bps"


def test_term_rotates_skipping_used():
    conn = connect(":memory:")
    t0 = fs(conn, date(2026, 10, 6))["term"]
    assert t0["term"] == "NNN lease" and t0["definition_hint"]
    for i, term in enumerate(["NNN lease", "Absolute NNN"]):
        conn.execute("INSERT INTO issues (date, term) VALUES (?,?)", (f"2026-10-0{i + 1}", term))
    conn.commit()
    assert fs(conn, date(2026, 10, 6))["term"]["term"] == "CAM"
    # a rerun on a day that already has a term keeps that day's term
    conn.execute("INSERT INTO issues (date, term) VALUES ('2026-10-06', 'CAM')")
    conn.commit()
    assert fs(conn, date(2026, 10, 6))["term"]["term"] == "CAM"


def test_term_wraps_when_all_used():
    conn = connect(":memory:")
    with open("config/terms.yaml", encoding="utf8") as f:
        names = [e["term"] for e in yaml.safe_load(f)]
    assert len(names) == 30
    for i, term in enumerate(names):
        conn.execute("INSERT INTO issues (date, term) VALUES (?,?)",
                     ((date(2026, 8, 1) + timedelta(days=i)).isoformat(), term))
    conn.commit()
    assert fs(conn, date(2026, 10, 6))["term"]["term"] == names[0]


def test_ai_requires_importance_7():
    conn = connect(":memory:")
    add_item(conn, 1, section="ai", importance=6)
    assert fs(conn, date(2026, 10, 6))["ai"] == []
    add_item(conn, 2, section="ai", importance=7)
    assert [a["title"] for a in fs(conn, date(2026, 10, 6))["ai"]] == ["Title 2"]


def test_debt_capped_at_4():
    conn = connect(":memory:")
    for n in range(6):
        add_item(conn, n, section="debt")
    assert len(fs(conn, date(2026, 10, 6))["debt"]) == 4


def test_saturday_sections():
    conn = connect(":memory:")
    anchor = datetime(2026, 10, 10, 5, tzinfo=ET)
    for n in range(7):
        add_item(conn, n, importance=n, hours_old=24 * 3, anchor=anchor)
    for n in range(10, 15):
        add_item(conn, n, section="ai", importance=5, hours_old=24 * 2, anchor=anchor)
    sat = fs(conn, date(2026, 10, 10))
    assert len(sat["week_top"]) == 5 and sat["week_top"][0]["title"] == "Title 6"
    assert len(sat["ai_week"]) == 3
    assert "week_ahead" not in sat and "reit_week" not in sat


def test_sunday_week_ahead_and_reit_week():
    conn = connect(":memory:")
    # Prior Friday (Oct 2) close is the base, so Monday's move counts.
    save_quotes(conn, [
        ReitQuote(t, date(2026, 10, d), c, 0.0)
        for t, a, b in [("O", 100, 103.4), ("PLD", 100, 98.0), ("SPG", 100, 101.0),
                        ("AMT", 100, 99.0), ("EQIX", 100, 105.0), ("VNQ", 100, 150.0)]
        for d, c in [(2, a), (9, b)]])
    odds = FedOdds("Oct", date(2026, 10, 28), [("No change", 0.9), ("Cut 25", 0.1)])
    sun = fs(conn, date(2026, 10, 11), odds)
    assert sun["week_ahead"] == {"fomc_dates": [], "FED_TOP": "FED_TOP"}
    assert sun["values"]["FED_TOP"] == "No change 90.0%"
    v = sun["values"]
    assert sun["reit_week"] == {"best": ["REITW_BEST_1", "REITW_BEST_2", "REITW_BEST_3"],
                                "worst": ["REITW_WORST_1", "REITW_WORST_2"]}
    assert (v["REITW_BEST_1"], v["REITW_BEST_2"], v["REITW_BEST_3"]) == (
        "EQIX +5.0%", "O +3.4%", "SPG +1.0%")
    assert (v["REITW_WORST_1"], v["REITW_WORST_2"]) == ("PLD -2.0%", "AMT -1.0%")
    assert v["REITW_WORST_3"] == "n/a"


def test_reit_week_fri_to_fri_includes_monday_move():
    conn = connect(":memory:")
    save_quotes(conn, [ReitQuote("O", date(2026, 10, 2), 100.0, 0.0),
                       ReitQuote("O", date(2026, 10, 5), 110.0, 10.0),
                       ReitQuote("O", date(2026, 10, 9), 105.0, 0.0)])
    v = fs(conn, date(2026, 10, 11))["values"]
    assert v["REITW_BEST_1"] == "O +5.0%"


def test_reit_week_more_than_six_tickers_no_overlap():
    conn = connect(":memory:")
    save_quotes(conn, [ReitQuote(f"T{i}", date(2026, 10, d), c, 0.0)
                       for i in range(8) for d, c in [(5, 100.0), (9, 100.0 + i)]])
    sun = fs(conn, date(2026, 10, 11))
    names = sun["reit_week"]
    assert len(names["best"]) == 3 and len(names["worst"]) == 3
    v = sun["values"]
    tickers = [v[k].split()[0] for k in names["best"] + names["worst"]]
    assert tickers == ["T7", "T6", "T5", "T0", "T1", "T2"]


def test_week_ahead_lists_fomc_within_7_days():
    conn = connect(":memory:")
    assert fs(conn, date(2026, 10, 25))["week_ahead"]["fomc_dates"] == ["Oct 28"]


def test_reit_week_empty_with_one_day_and_no_fomc_in_range():
    conn = connect(":memory:")
    save_quotes(conn, [ReitQuote("O", date(2026, 10, 9), 100.0, 1.0)])
    sun = fs(conn, date(2026, 10, 11))
    assert sun["reit_week"] == {"best": [], "worst": []}
    assert sun["values"]["REITW_BEST_1"] == "n/a"
    assert sun["week_ahead"]["fomc_dates"] == []
    assert sun["week_ahead"]["FED_TOP"] == "FED_TOP"


def test_fed_meeting_from_calendar_without_odds():
    conn = connect(":memory:")
    v = fs(conn, date(2026, 10, 6))["values"]
    assert v["FED_MEETING"] == "Oct 28" and v["FED_TOP"] == "n/a"
    assert fs(conn, date(2026, 10, 28))["values"]["FED_MEETING"] == "Oct 28"


def test_single_non_vnq_quote_leaves_reit_down_na():
    conn = connect(":memory:")
    v = fs(conn, date(2026, 10, 6), quotes=[ReitQuote("O", date(2026, 10, 5), 60.0, 2.1)])["values"]
    assert v["REIT_UP"] == "O +2.1%" and v["REIT_DOWN"] == "n/a"


def test_chart_writes_png(tmp_path):
    pts = [RatePoint("DGS10", date(2026, 9, 1) + timedelta(days=i), 4.5 + i * 0.01)
           for i in range(40)]
    out = rate_chart(pts, tmp_path / "c.png", "10Y Treasury")
    assert out.exists() and out.stat().st_size > 1000
