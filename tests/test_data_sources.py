"""New data sources: extra FRED series, Trepp CMBS delinquency, Week Ahead calendar,
Market Watch, and the no-dash style rules."""

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import respx

from src import draft, main, rates, trepp
from src.collect import calendar
from src.config import ET
from src.factsheet import build_factsheet, fmt_spread
from src.markets import pick, region_of
from src.models import Item, RatePoint
from src.render_html import market_summary, no_dashes, render_issue_html, summary_label
from src.store import connect, save_items, save_rates

FIX = Path("tests/fixtures")
FRED_URL = "https://api.stlouisfed.org/fred/series/observations"
TUE = date(2026, 10, 6)
SUN = date(2026, 10, 11)


def fs(conn, d=TUE, **kw):
    return build_factsheet(conn, d, None, [], [], None, **kw)


# --- FRED series ------------------------------------------------------------

def _save(conn, sid, pts):
    save_rates(conn, [RatePoint(sid, date.fromisoformat(d), v) for d, v in pts])


def test_new_daily_series_format_value_and_bps_change():
    conn = connect(":memory:")
    _save(conn, "DGS2", [("2026-10-01", 4.80), ("2026-10-02", 4.83)])
    _save(conn, "BAMLH0A0HYM2", [("2026-10-01", 3.10), ("2026-10-02", 3.10)])
    v = fs(conn)["values"]
    assert (v["DGS2"], v["DGS2_CHG"]) == ("4.83%", "+3 bps")
    assert (v["HY_OAS"], v["HY_OAS_CHG"]) == ("3.10%", "0 bps")


def test_curve_spread_shown_in_bps_including_negative():
    conn = connect(":memory:")
    _save(conn, "T10Y2Y", [("2026-10-01", -0.05), ("2026-10-02", -0.12)])
    v = fs(conn)["values"]
    assert (v["T10Y2Y"], v["T10Y2Y_CHG"]) == ("-12 bps", "-7 bps")
    assert fmt_spread(0.45) == "45 bps"
    assert fmt_spread(0.0) == "0 bps"


def test_weekly_mortgage_and_bank_loans_with_asof():
    conn = connect(":memory:")
    _save(conn, "MORTGAGE30US", [("2026-09-24", 7.30), ("2026-10-01", 7.28)])
    _save(conn, "CREACBW027SBOG", [("2026-09-16", 2978.0), ("2026-09-23", 2981.0)])
    v = fs(conn)["values"]
    assert (v["MORTGAGE30US"], v["MORTGAGE30US_CHG"], v["MORTGAGE30US_ASOF"]) == (
        "7.28%", "-2 bps", "Oct 1")
    assert (v["BANK_CRE_LOANS"], v["BANK_CRE_LOANS_CHG"]) == ("$2,981B", "+0.1%")
    assert v["BANK_CRE_LOANS_ASOF"] == "Sep 23"


def test_quarterly_delinquency_change_vs_prior_quarter():
    conn = connect(":memory:")
    _save(conn, "DRCRELEXFACBS", [("2025-10-01", 1.40), ("2026-01-01", 1.53)])
    v = fs(conn)["values"]
    assert (v["BANK_CRE_DQ"], v["BANK_CRE_DQ_CHG"], v["BANK_CRE_DQ_ASOF"]) == (
        "1.53%", "+13 bps", "Q1 2026")


def test_missing_series_are_na():
    v = fs(connect(":memory:"))["values"]
    assert v["DGS2"] == v["T10Y2Y_CHG"] == v["MORTGAGE30US_ASOF"] == "n/a"


@respx.mock
def test_collect_rates_uses_per_series_window(tmp_path):
    route = respx.get(FRED_URL).mock(return_value=httpx.Response(200, json={"observations": [
        {"date": "2025-10-01", "value": "1.40"}, {"date": "2026-01-01", "value": "1.53"}]}))
    conn = connect(str(tmp_path / "t.db"))
    with httpx.Client() as client:
        res = rates.collect_rates(conn, ["DRCRELEXFACBS"], "k", client, date(2026, 10, 5))
    assert res[0].ok
    start = route.calls[0].request.url.params["observation_start"]
    assert start == (date(2026, 10, 5) - timedelta(days=400)).isoformat()
    assert rates.lookback_days("DGS10") == 45


# --- Trepp --------------------------------------------------------------------

def test_trepp_title_rose_fell_unchanged():
    assert trepp.parse_title("CMBS Delinquency Rate Rose 17 Basis Points in September 2026") \
        == {"chg": "+17 bps", "month": "Sept 2026"}
    assert trepp.parse_title("CMBS Delinquency Rate Rose 17 bps in Sept 2026") \
        == {"chg": "+17 bps", "month": "Sept 2026"}
    assert trepp.parse_title("CMBS Delinquency Rate Fell 9 bps in March 2026") \
        == {"chg": "-9 bps", "month": "March 2026"}
    assert trepp.parse_title("CMBS Delinquency Rate Unchanged in July 2026") \
        == {"chg": "0 bps", "month": "July 2026"}
    assert trepp.parse_title("Hotel Delinquency Rate is Higher") is None
    assert trepp.parse_title("CMBS Delinquency Rate Rises in May 2026") is None  # no bps


def test_trepp_rate_from_summary():
    s = ("The Trepp CMBS delinquency rate rose 17 basis points to 8.02% in September 2026, "
         "its highest level since November 2020.")
    assert trepp.parse_rate(s) == "8.02%"
    assert trepp.parse_rate("No rate here.") is None


def _trepp_item(title, days_old, run=TUE, summary=""):
    pub = datetime(run.year, run.month, run.day, 5, tzinfo=ET) - timedelta(days=days_old)
    return Item("Trepp", f"https://www.trepp.com/x/{days_old}", title, pub, summary, 85)


def test_cmbs_values_from_stored_items_newest_first_and_stale_ignored():
    conn = connect(":memory:")
    save_items(conn, [
        _trepp_item("CMBS Delinquency Rate Fell 5 bps in August 2026", 34),
        _trepp_item("CMBS Delinquency Rate Rose 17 Basis Points in September 2026", 4,
                    summary="The Trepp CMBS delinquency rate rose 17 basis points to 8.02% in "
                            "September 2026."),
    ])
    v = trepp.cmbs_values(conn, TUE)
    assert v == {"CMBS_DQ_CHG": "+17 bps", "CMBS_DQ_MONTH": "Sept 2026",
                 "CMBS_DQ_URL": "https://www.trepp.com/x/4", "CMBS_DQ": "8.02%"}
    stale = connect(":memory:")
    save_items(stale, [_trepp_item("CMBS Delinquency Rate Rose 3 bps in June 2026", 60)])
    assert trepp.cmbs_values(stale, TUE) is None
    assert trepp.cmbs_values(connect(":memory:"), TUE) is None


def test_cmbs_values_in_factsheet():
    v = fs(connect(":memory:"), cmbs={"CMBS_DQ_CHG": "-4 bps", "CMBS_DQ_MONTH": "Oct 2026",
                                      "CMBS_DQ_URL": "https://t/x"})["values"]
    assert v["CMBS_DQ_CHG"] == "-4 bps" and "CMBS_DQ" not in v
    v = fs(connect(":memory:"))["values"]
    assert v["CMBS_DQ_CHG"] == v["CMBS_DQ_MONTH"] == "n/a"


# --- calendar -----------------------------------------------------------------

def _fed_events():
    return calendar.parse_fed((FIX / "fed_calendar.json").read_text(encoding="utf-8-sig"))


def test_parse_fed_keeps_fomc_beige_and_board_speeches_only():
    titles = {e["title"] for e in _fed_events()}
    assert "FOMC Meeting" in titles and "FOMC Minutes" in titles and "Beige Book" in titles
    assert "Speech: Governor Christopher J. Waller" in titles
    assert not any("Press Conference" in t or "G.19" in t or "Holiday" in t for t in titles)
    ev = next(e for e in _fed_events() if e["title"] == "Beige Book" and "10-14" in e["date"])
    assert ev["time"] == "2:00 PM ET" and ev["source"] == "Federal Reserve"
    assert ev["url"].startswith("https://www.federalreserve.gov/")


def test_week_window_next_seven_days_sorted():
    week = calendar.week_window(_fed_events(), date(2026, 10, 5))  # Mon -> Oct 6-12
    assert [e["date"] for e in week] == sorted(e["date"] for e in week)
    assert all("2026-10-06" <= e["date"] <= "2026-10-12" for e in week)
    assert [e["title"] for e in week][:2] == [
        "Speech: Vice Chair for Supervision Michelle W. Bowman", "FOMC Minutes"]
    assert "Beige Book" not in [e["title"] for e in week]  # Oct 14 is outside


def test_week_ahead_markdown_and_empty_message():
    md = calendar.week_ahead_markdown([{"date": "2026-10-13", "time": "8:30 AM ET",
                                        "title": "CPI (September)", "source": "BLS",
                                        "url": "https://www.bls.gov/cpi/"}])
    assert md == "- **Tue Oct 13, 8:30 AM ET:** CPI (September), [BLS](https://www.bls.gov/cpi/)"
    assert calendar.week_ahead_markdown([]) == "No major scheduled releases this week."
    no_time = calendar.week_ahead_markdown([{"date": "2026-10-14", "time": None,
                                             "title": "Beige Book [x]", "source": "Fed",
                                             "url": "javascript:x"}])
    assert no_time == "- **Wed Oct 14:** Beige Book \\[x\\]"


def test_sunday_factsheet_fills_week_ahead_list():
    events = calendar.week_window(_fed_events(), SUN)
    sheet = fs(connect(":memory:"), SUN, week_events=events)
    assert sheet["week_ahead"]["events"] == events
    assert sheet["values"]["WEEK_AHEAD"].startswith("- **Wed Oct 14, 2:00 PM ET:** Beige Book")
    empty = fs(connect(":memory:"), SUN, week_events=[])
    assert empty["values"]["WEEK_AHEAD"] == "No major scheduled releases this week."


def test_sunday_template_uses_week_ahead_placeholder():
    text = Path("templates/sunday.md").read_text(encoding="utf8")
    assert "\n{{WEEK_AHEAD}}\n" in text


# --- render -----------------------------------------------------------------

VALUES = {
    "DGS10": "4.28%", "DGS10_CHG": "+4 bps", "RATES_ASOF": "Oct 2",
    "DGS2": "4.83%", "DGS2_CHG": "-1 bps", "T10Y2Y": "45 bps", "T10Y2Y_CHG": "+5 bps",
    "MORTGAGE30US": "7.28%", "MORTGAGE30US_CHG": "-2 bps", "MORTGAGE30US_ASOF": "Oct 1",
    "HY_OAS": "3.10%", "HY_OAS_CHG": "0 bps",
    "BANK_CRE_LOANS": "$2,981B", "BANK_CRE_LOANS_CHG": "+0.1%", "BANK_CRE_LOANS_ASOF": "n/a",
    "BANK_CRE_DQ": "1.53%", "BANK_CRE_DQ_CHG": "+13 bps", "BANK_CRE_DQ_ASOF": "Q1 2026",
    "CMBS_DQ_CHG": "+17 bps", "CMBS_DQ_MONTH": "Sept 2026",
}


def test_summary_has_new_rows_labels_and_credit_group():
    html = market_summary(VALUES, None, "")
    for label in ("2-Year Treasury", "10Y-2Y curve", "30-Year Mortgage (Oct 1)",
                  "High-yield spread", "Bank CRE delinquency (Q1 2026)",
                  "CMBS delinquency (Sept 2026)", ">Credit<"):
        assert label in html, label
    assert "Bank CRE loans<" in html  # n/a as-of is dropped from the label
    assert "+17 bps" in html  # change-only row
    assert "Sources" not in html and "Rates as of Oct 2 close.</p>" in html


def test_summary_skips_missing_rows_and_caption_without_asof():
    html = market_summary({"DGS10": "4.28%"}, None, "")
    assert "Credit" not in html and "Mortgage" not in html and "caption" not in html


def test_summary_label_helper():
    assert summary_label("X ({A})", {"A": "Oct 1"}) == "X (Oct 1)"
    assert summary_label("X ({A})", {}) == "X"


def test_fallback_markdown_lists_change_only_row():
    md = main.fallback_markdown({"values": VALUES, "day_type": "weekday"})
    assert "- **CMBS delinquency (Sept 2026):** {{CMBS_DQ_CHG}}" in md
    assert "- **2-Year Treasury:** {{DGS2}} ({{DGS2_CHG}})" in md


# --- dashes -----------------------------------------------------------------

def test_no_dashes_helper():
    assert no_dashes("Rates rose — again.") == "Rates rose, again."
    assert no_dashes("Rates rose—again.") == "Rates rose, again."
    assert no_dashes("Rates rose – again.") == "Rates rose, again."
    assert no_dashes("2020–2021 and a well-known deal") == "2020-2021 and a well-known deal"


def test_render_strips_dashes_from_model_prose():
    md = "## Top Stories\n\nThe deal closed — finally.\n"
    html = render_issue_html(md, {"values": {}, "date": "2026-10-06"}, [], None)
    assert "—" not in html and "&mdash;" not in html and "closed, finally." in html


def test_prompts_ban_dashes_and_set_quick_hits_ai_and_market_watch_style():
    seen = []
    sheet = {"day_type": "weekday", "values": {"DGS10": "4.62%"}, "top": []}
    draft.write(sheet, run=lambda p, m: seen.append(p) or "x")
    draft.edit("x", sheet, run=lambda p, m: seen.append(p) or "x")
    for prompt in seen:
        assert "Never use em dashes or en dashes" in prompt
        assert "12-year-old" in prompt
    assert "AI tool or use case" in seen[0] and "Market Watch" in seen[0]
    assert "## Market Watch" in seen[0]  # weekday template


# --- Market Watch -------------------------------------------------------------

def test_region_tagging_keywords_then_query_tag():
    assert region_of("Dallas office tower trades") == "sun_belt"
    assert region_of("Big sale", "A Seattle lab building sold.") == "west_coast"
    assert region_of("London logistics deal") == "international"
    assert region_of("A big portfolio sale", "", "west_coast") == "west_coast"
    assert region_of("A big portfolio sale") is None
    assert region_of("Toronto investor buys Miami tower") == "international"  # first mention


def _row(n, title, region=None, summary=""):
    return {"url": f"https://x.com/{n}", "title": title, "summary": summary, "region": region}


def test_pick_one_per_region_skipping_used_stories():
    rows = [_row(1, "Dallas office sale"), _row(2, "Houston industrial deal"),
            _row(3, "Seattle lab lease"), _row(4, "Generic story")]
    out = pick(rows, taken_urls={"https://x.com/1"})
    assert out["sun_belt"]["url"] == "https://x.com/2"
    assert out["west_coast"]["url"] == "https://x.com/3"
    assert out["international"] is None


def _add(conn, n, title, section="top", importance=5, region=None):
    pub = (datetime(2026, 10, 6, 3, tzinfo=ET)).astimezone(timezone.utc).isoformat(
        timespec="seconds")
    cur = conn.execute(
        "INSERT INTO items (source, url, canonical_url, title, published_at, summary,"
        " priority, also_covered, section, importance, region)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("Src", f"https://x.com/{n}", f"x.com/{n}", title, pub, "", 50, "[]", section,
         importance, region))
    conn.execute("UPDATE items SET cluster_id = id WHERE id = ?", (cur.lastrowid,))
    conn.commit()


def test_factsheet_markets_dedupe_vs_top_and_quick_hits():
    conn = connect(":memory:")
    for n in range(13):  # 5 top + 8 quick hits, all Dallas stories with high importance
        _add(conn, n, f"Dallas story {n}", importance=9)
    _add(conn, 50, "Atlanta retail center sells", importance=2)
    _add(conn, 51, "Tokyo office deal", importance=3)
    _add(conn, 52, "Mansion sells in Bel Air", section="other", region="west_coast")
    sheet = fs(conn)
    used = {s["url"] for k in ("top", "quick_hits") for s in sheet[k]}
    assert sheet["markets"]["sun_belt"]["url"] == "https://x.com/50"
    assert sheet["markets"]["sun_belt"]["url"] not in used
    assert sheet["markets"]["international"]["title"] == "Tokyo office deal"
    assert sheet["markets"]["west_coast"] is None  # "other" section is skipped


def test_weekend_issues_have_no_market_watch():
    conn = connect(":memory:")
    assert "markets" not in fs(conn, date(2026, 10, 10))
    assert "markets" not in fs(conn, SUN)


def test_fallback_market_watch_omits_empty_regions():
    sheet = {"values": {}, "day_type": "weekday",
             "top": [{"title": "T", "url": "https://x.com/t", "source": "S"}],
             "markets": {"sun_belt": {"title": "Dallas", "url": "https://x.com/d",
                                      "source": "S"},
                         "west_coast": None, "international": None}}
    md = main.fallback_markdown(sheet)
    assert "## Market Watch" in md and "### Sun Belt" in md and "### West Coast" not in md
    sheet["markets"]["sun_belt"] = None
    assert "## Market Watch" not in main.fallback_markdown(sheet)


def test_regional_query_tag_saved_and_kept_on_duplicate():
    conn = connect(":memory:")
    pub = datetime(2026, 10, 6, 3, tzinfo=ET)
    save_items(conn, [Item("TRD", "https://t.com/a", "Deal closes", pub)])
    save_items(conn, [Item("TRD", "https://t.com/a", "Deal closes", pub, region="west_coast")])
    assert conn.execute("SELECT region FROM items").fetchone()["region"] == "west_coast"


def test_market_watch_in_checks_budget_and_links():
    from src.checks import BUDGETS, check_issue
    assert BUDGETS["Market Watch"] == 200
    sheet = {"day_type": "saturday", "values": {},
             "markets": {"sun_belt": {"title": "Dallas $50 million sale", "summary": "",
                                      "url": "https://x.com/d"}}}
    md = "## Market Watch\n\n### Sun Belt\n\nA $50 million sale.\n"
    kinds = {p["kind"] for p in check_issue(md, sheet, [])}
    assert "missing_link" in kinds and "unsourced_number" not in kinds


# --- main integration -----------------------------------------------------------

class _Client:
    def get(self, url, **kw):
        raise httpx.ConnectError("offline")

    def close(self):
        pass


def test_collect_records_trepp_and_calendar_problems_and_continues(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "env", lambda name, required=True: None)
    monkeypatch.setattr(main.polymarket, "fetch_fed_odds", lambda c, t: None)
    conn = connect(str(tmp_path / "t.db"))
    problems = []
    sources = {"feeds": [], "google_news": [], "fred_series": [], "reit_etf": "VNQ"}
    out = main._collect(conn, sources, SUN, _Client(), problems)
    assert out[3] == {"cmbs": None, "week_events": None}
    assert any(p.startswith("trepp: no CMBS") for p in problems)
    assert any(p.startswith("calendar: ConnectError") for p in problems)
    # Weekdays do not fetch the calendar.
    problems.clear()
    main._collect(conn, sources, TUE, _Client(), problems)
    assert not any(p.startswith("calendar") for p in problems)


def test_collect_passes_week_events_and_trepp_through(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "env", lambda name, required=True: None)
    monkeypatch.setattr(main.polymarket, "fetch_fed_odds", lambda c, t: None)
    monkeypatch.setattr(main.calendar, "fetch_week", lambda c, d: [{"date": "x"}])
    monkeypatch.setattr(main.trepp, "cmbs_values", lambda c, d: {"CMBS_DQ_CHG": "0 bps"})
    problems = []
    out = main._collect(connect(str(tmp_path / "t.db")),
                        {"feeds": [], "google_news": [], "fred_series": []},
                        SUN, _Client(), problems)
    assert out[3] == {"cmbs": {"CMBS_DQ_CHG": "0 bps"}, "week_events": [{"date": "x"}]}
    assert not any(p.startswith(("trepp", "calendar")) for p in problems)


@respx.mock
def test_fetch_week_sends_descriptive_user_agent():
    route = respx.get(calendar.FED_URL).mock(return_value=httpx.Response(
        200, text=(FIX / "fed_calendar.json").read_text(encoding="utf-8-sig")))
    with httpx.Client() as client:
        week = calendar.fetch_week(client, SUN)
    assert "CRE Blurb newsletter" in route.calls[0].request.headers["User-Agent"]
    assert [e["title"] for e in week] == ["Beige Book"]


def test_factsheet_reit_names_from_config():
    from src.models import ReitQuote
    quotes = [ReitQuote("NNN", TUE, 40.0, 1.9), ReitQuote("PLD", TUE, 110.0, -1.0)]
    v = build_factsheet(connect(":memory:"), TUE, None, quotes, [], None)["values"]
    assert v["REIT_UP_NAME"] == "NNN REIT (NNN)"
    assert v["REIT_UP_TYPE"] == "Owns single-tenant retail like convenience stores"
    assert v["REIT_DOWN_NAME"] == "Prologis (PLD)"
    assert v["REIT_DOWN_TYPE"] == "Owns warehouses and distribution centers"
    empty = build_factsheet(connect(":memory:"), TUE, None, [], [], None)["values"]
    assert empty["REIT_UP_NAME"] == empty["REIT_UP_TYPE"] == "n/a"


def test_fed_cut_hold_hike_odds_from_polymarket_outcomes():
    from src.models import FedOdds
    odds = FedOdds("Fed Decision in October?", date(2026, 10, 28), [
        ("No change", 0.785), ("25 bps decrease", 0.15), ("50+ bps decrease", 0.04),
        ("25+ bps increase", 0.025)])
    problems = []
    v = build_factsheet(connect(":memory:"), TUE, odds, [], problems, None)["values"]
    assert (v["FED_CUT"], v["FED_HOLD"], v["FED_HIKE"]) == ("19.0%", "78.5%", "2.5%")
    assert v["FED_TOP"] == "No change 78.5%" and problems == []
    odd = FedOdds("x", date(2026, 10, 28), [("No change", 0.9), ("Emergency meeting", 0.1)])
    problems = []
    build_factsheet(connect(":memory:"), TUE, odd, [], problems, None)
    assert problems == ["polymarket: unmapped outcome 'Emergency meeting'"]
    v = build_factsheet(connect(":memory:"), TUE, None, [], [], None)["values"]
    assert v["FED_CUT"] == v["FED_HOLD"] == v["FED_HIKE"] == "n/a"


def _quotes():
    from src.models import ReitQuote
    return [ReitQuote("NNN", TUE, 40.0, 1.9), ReitQuote("PLD", TUE, 110.0, -1.0)]


def test_mover_news_story_found_by_name_or_ticker():
    conn = connect(":memory:")
    _add(conn, 1, "Prologis signs huge warehouse lease in Ohio")
    _add(conn, 2, "Unrelated office sale")
    sheet = build_factsheet(conn, TUE, None, _quotes(), [], None)
    assert sheet["mover_news"]["down"]["url"] == "https://x.com/1"
    assert sheet["mover_news"]["up"] is None
    v = sheet["values"]
    assert v["MOVER_UP_NOTE"] == ("No company-specific news today; it may have moved with "
                                  "other net-lease retail REITs.")
    conn2 = connect(":memory:")
    _add(conn2, 3, "NNN raises guidance")  # ticker match (3+ letters, case-sensitive)
    assert build_factsheet(conn2, TUE, None, _quotes(), [], None)["mover_news"]["up"]["url"] \
        == "https://x.com/3"


def test_mover_news_absent_without_quotes_and_on_weekends():
    sheet = build_factsheet(connect(":memory:"), TUE, None, [], [], None)
    assert sheet["mover_news"] == {} and sheet["values"]["MOVER_UP_NOTE"] == "n/a"
    assert "mover_news" not in build_factsheet(connect(":memory:"), SUN, None, _quotes(), [], None)


def test_mover_rows_labels_and_fallback_lines():
    vals = {"REIT_UP": "NNN +1.9%", "REIT_UP_NAME": "NNN REIT (NNN)",
            "REIT_UP_TYPE": "Owns single-tenant retail like convenience stores",
            "MOVER_UP_NOTE": "No company-specific news today; it may have moved with other "
                             "net-lease retail REITs."}
    html = market_summary(vals, None, "")
    assert "Biggest gain today" in html and ">NNN REIT (NNN)</span>" in html
    assert 'class="chg up"' in html and "+1.9%" in html
    md = main.fallback_markdown({"values": vals, "day_type": "weekday",
                                 "mover_news": {"up": None}})
    assert "**Why {{REIT_UP_NAME}} moved:** {{MOVER_UP_NOTE}}" in md
    md = main.fallback_markdown({"values": vals, "day_type": "weekday", "mover_news": {
        "up": {"title": "NNN buys stores", "url": "https://x.com/n", "source": "S"}}})
    assert "**Why {{REIT_UP_NAME}} moved:** [NNN buys stores](https://x.com/n) (S)" in md


def test_mover_checks_code_note_ok_model_number_flagged():
    from src.checks import check_issue
    sheet = {"day_type": "saturday", "values": {},
             "mover_news": {"up": {"title": "NNN buys stores", "summary": "",
                                   "url": "https://x.com/n"}}}
    ok = "## The Numbers\n\n**Why {{REIT_UP_NAME}} moved:** {{MOVER_UP_NOTE}}\n"
    kinds = {p["kind"] for p in check_issue(ok, sheet, [])}
    assert "model_number" not in kinds and "missing_link" in kinds
    bad = ("## The Numbers\n\n**Why {{REIT_UP_NAME}} moved:** It bought 12% more stores "
           "([S](https://x.com/n)).\n")
    kinds = {p["kind"] for p in check_issue(bad, sheet, [])}
    assert "model_number" in kinds and "missing_link" not in kinds


def test_weekday_template_has_mover_lines():
    text = Path("templates/weekday.md").read_text(encoding="utf8")
    assert "**Why {{REIT_UP_NAME}} moved:**" in text and "{{MOVER_DOWN_NOTE}}" in text
