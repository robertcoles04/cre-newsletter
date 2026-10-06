"""Reader + advisor review round (2026-10-05): The Brief, coffee chat line, tone, stale
recaps and contradictions, move size, Data Room, Fed target range, per-row as-of,
Week Ahead data releases, source names, story ranking."""

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import respx

from src import draft, main
from src.checks import BUDGETS, check_issue
from src.collect import calendar
from src.config import ET, display_source
from src.factsheet import build_factsheet, move_size
from src.models import RatePoint, ReitQuote
from src.render_html import data_room, hint_for, market_summary, read_minutes, render_issue_html
from src.store import connect, save_rates

FIX = Path(__file__).parent / "fixtures"
TUE = date(2026, 10, 6)
SUN = date(2026, 10, 11)
FOOTER = "For informational purposes only. Not investment advice."


def _add(conn, n, title, *, section="top", importance=5, hours_ago=2.0, source="Src",
         summary=""):
    pub = (datetime(2026, 10, 6, 5, tzinfo=ET) - timedelta(hours=hours_ago)).astimezone(
        timezone.utc).isoformat(timespec="seconds")
    cur = conn.execute(
        "INSERT INTO items (source, url, canonical_url, title, published_at, summary,"
        " priority, also_covered, section, importance)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (source, f"https://x.com/{n}", f"x.com/{n}", title, pub, summary, 50, "[]", section,
         importance))
    conn.execute("UPDATE items SET cluster_id = id WHERE id = ?", (cur.lastrowid,))
    conn.commit()


def _fs(conn, d=TUE, quotes=(), **kw):
    return build_factsheet(conn, d, None, list(quotes), [], None, **kw)


# --- 1/2/3. Templates, prompts, budgets -------------------------------------------

def test_brief_budget_and_quick_hits_cap():
    assert BUDGETS["The Brief"] == 60
    assert BUDGETS["Quick Hits"] <= 220
    for name in ("AI Infrastructure", "Market Spotlight", "Careers Corner"):
        assert name in BUDGETS


def test_brief_passes_checks_without_numbers():
    sheet = {"day_type": "saturday", "values": {},
             "week_top": [{"title": "Deal", "summary": "", "url": "https://x.com/1"}]}
    md = ("## The Brief\n\n- A Dallas office tower found a buyer, a sign lenders are "
          "back. [Src](https://x.com/1)\n\n" + FOOTER + "\n")
    assert check_issue(md, sheet, []) == []


def test_draft_prompt_has_new_rules():
    seen = []
    sheet = {"day_type": "weekday", "values": {"DGS10": "4.62%", "HY_OAS_DATE": "2026-10-01"},
             "top": [], "move_size": "small"}
    draft.write(sheet, run=lambda p, m: seen.append(p) or "x")
    prompt = seen[0]
    for text in ("## The Brief", "Coffee chat talking points", "big_movers", "`distress`",
                     "smart money", "move_size", "AI Infrastructure",
                 "novelty-angle", "mechanism truly applies", "at most 6 bullets",
                 "never babyish or patronizing"):
        assert text in prompt, text
    assert "HY_OAS_DATE" not in prompt  # machine dates are not placeholders for the model


# --- 4. Stale recaps and contradictions --------------------------------------------

def test_stale_reit_recaps_dropped_fresh_kept():
    conn = connect(":memory:")
    _add(conn, 1, "Real Estate Stocks Slide as Mortgage Rates Climb", importance=9,
         hours_ago=28)
    _add(conn, 2, "REITs rally after jobs report", importance=8, hours_ago=3)
    _add(conn, 3, "Office tower sells in Dallas", importance=7, hours_ago=28)
    urls = [s["url"] for s in _fs(conn)["top"]]
    assert "https://x.com/1" not in urls
    assert urls[:2] == ["https://x.com/2", "https://x.com/3"]  # old non-recap stays


def _issue_with(sentence):
    return f"## Top Stories\n\n{sentence}\n\n{FOOTER}\n"


def test_contradiction_flagged_both_directions():
    up = {"day_type": "saturday", "values": {"VNQ_CHG": "+0.4%"}}
    down = {"day_type": "saturday", "values": {"VNQ_CHG": "-1.2%"}}
    fell = _issue_with("Real estate stocks fell as mortgage rates rose.")
    rallied = _issue_with("REITs rallied after the Fed spoke.")
    kinds = lambda md, sheet: [p["kind"] for p in check_issue(md, sheet, [])]
    assert "contradiction" in kinds(fell, up)
    assert "contradiction" not in kinds(fell, down)
    assert "contradiction" in kinds(rallied, down)
    assert "contradiction" not in kinds(rallied, up)
    flat = {"day_type": "saturday", "values": {"VNQ_CHG": "n/a"}}
    assert "contradiction" not in kinds(fell, flat)


# --- 5. Move size ----------------------------------------------------------------

def test_move_size_buckets():
    assert move_size(None) is None
    assert [move_size(n) for n in (0, 2, -2, 3, -9, 9, 10, -14)] == [
        "unchanged", "unchanged", "unchanged", "small", "small", "small", "notable", "notable"]


def test_factsheet_has_move_size():
    conn = connect(":memory:")
    save_rates(conn, [RatePoint("DGS10", date(2026, 10, 1), 4.20),
                      RatePoint("DGS10", date(2026, 10, 2), 4.32)])
    assert _fs(conn)["move_size"] == "notable"
    assert _fs(connect(":memory:"))["move_size"] is None


# --- 6. REIT mover name -------------------------------------------------------------

def test_reit_mover_renders_company_name():
    quotes = [ReitQuote("NNN", TUE, 40.0, 1.9), ReitQuote("UDR", TUE, 38.0, -1.2)]
    v = _fs(connect(":memory:"), quotes=quotes)["values"]
    html = market_summary(v, None, "")
    assert "NNN REIT (NNN)" in html and "+1.9%" in html


# --- 7. Footer and reading time ------------------------------------------------------

def test_read_minutes_and_dateline():
    assert read_minutes("word " * 10) == 1
    assert read_minutes("word " * 920) == 4
    assert read_minutes("[a](https://very/long/url/" + "x" * 900 + ")") == 1
    html = render_issue_html("## Top Stories\n\n" + "word " * 920, {"values": {},
                             "date": "2026-10-06", "day_type": "weekday"}, [], None)
    assert '<span class="edition">Daily Edition · 4 min read</span>' in html


# --- 8. Rates labels ---------------------------------------------------------------------

def test_fed_target_range_replaces_effective_rate():
    conn = connect(":memory:")
    save_rates(conn, [RatePoint("DFF", date(2026, 10, 2), 3.88),
                      RatePoint("DFEDTARL", date(2026, 10, 4), 3.75),
                      RatePoint("DFEDTARL", date(2026, 10, 5), 3.75),
                      RatePoint("DFEDTARU", date(2026, 10, 4), 4.00),
                      RatePoint("DFEDTARU", date(2026, 10, 5), 4.00)])
    v = _fs(conn)["values"]
    assert (v["DFF"], v["DFF_CHG"]) == ("3.75% to 4.00%", "0 bps")
    assert "DFF_ASOF" not in v
    html = market_summary(v, None, "")
    assert "3.75% to 4.00%" in html and "other rates key off it" in html


def test_fed_funds_falls_back_to_effective_rate():
    conn = connect(":memory:")
    save_rates(conn, [RatePoint("DFF", date(2026, 10, 1), 3.88),
                      RatePoint("DFF", date(2026, 10, 2), 3.88)])
    assert _fs(conn)["values"]["DFF"] == "3.88%"


def test_per_row_asof_in_hint_when_date_differs():
    conn = connect(":memory:")
    save_rates(conn, [RatePoint("DGS10", date(2026, 10, 2), 4.3),
                      RatePoint("DGS10", date(2026, 10, 5), 4.3),
                      RatePoint("DGS5", date(2026, 10, 2), 4.0),
                      RatePoint("DGS5", date(2026, 10, 5), 4.0),
                      RatePoint("SOFR", date(2026, 10, 1), 4.1),
                      RatePoint("SOFR", date(2026, 10, 2), 4.1)])
    v = _fs(conn)["values"]
    assert v["RATES_ASOF"] == "Oct 5" and v["SOFR_ASOF"] == "Oct 2"
    assert "DGS5_ASOF" not in v
    assert hint_for("SOFR", v) == "Base rate for floating-rate property loans (as of Oct 2)"
    assert "(as of" not in hint_for("DGS5", v)
    assert "Rates as of Oct 5 close." in market_summary(v, None, "")


def test_new_hints():
    assert hint_for("SPREAD_10Y", {}) == (
        "Below zero: Treasuries out-yield REIT dividends, so REITs look pricey vs. bonds")
    assert "Prediction-market odds from Polymarket traders" in hint_for("FED_CUT", {})
    assert hint_for("MORTGAGE30US", {}).endswith("a housing-demand gauge, not a CRE loan rate")


# --- 9. Data Room --------------------------------------------------------------------

ROOM_VALUES = {
    "DGS10": "4.30%", "DGS10_CHG": "+2 bps", "RATES_ASOF": "Oct 5",
    "HY_OAS": "3.10%", "HY_OAS_CHG": "-2 bps", "HY_OAS_DATE": "2026-10-05",
    "BANK_CRE_DQ": "1.53%", "BANK_CRE_DQ_CHG": "+13 bps", "BANK_CRE_DQ_ASOF": "Q1 2026",
    "BANK_CRE_DQ_DATE": "2026-01-01",
}


def test_data_room_tags_recent_rows_only():
    room = data_room(ROOM_VALUES, date(2026, 10, 6))
    assert room.startswith('<section class="data-room"') and ">Data Room</h2>" in room
    assert "Slower-moving credit data. Rows marked Updated changed since the last issue." in room
    hy, dq = room.split("High-yield spread")[1].split("Bank CRE delinquency")
    assert '<span class="tag">Updated</span>' in hy and 'class="chg down rate"' in hy
    assert "Updated" not in dq
    assert 'class="chg unch">+13 bps' in dq and "chg up" not in dq  # no implied move today
    assert data_room({"DGS10": "4.30%"}, date(2026, 10, 6)) == ""


def test_data_room_placed_after_term_of_the_day_not_in_summary():
    md = ("## The Brief\n\n- One. [S](https://x.com/1)\n\n## The Numbers\n\n- x\n\n"
          "## Top Stories\n\nText.\n\n## Term of the Day\n\n**Cap rate:** yield.\n\n"
          + FOOTER + "\n")
    html = render_issue_html(md, {"values": ROOM_VALUES, "date": "2026-10-06",
                                  "day_type": "weekday"}, [], None)
    summary = html.split('<section class="summary"')[1].split("</section>")[0]
    assert "High-yield" not in summary
    assert html.index('id="term-h"') < html.index('id="dataroom-h"')
    assert html.index("The Brief") < html.index('id="summary-h"')


def test_weekend_snapshot_after_the_brief_summary_after_the_stories():
    md = ("## The Brief\n\n- One. [S](https://x.com/1)\n\n## Week in Review\n\nText.\n\n"
          + FOOTER + "\n")
    html = render_issue_html(md, {"values": {**ROOM_VALUES, "DGS10": "4.30%"},
                                  "date": "2026-10-10", "day_type": "saturday"}, [], None)
    assert (html.index("The Brief") < html.index('id="snapshot-h"')
            < html.index('id="week-in-review"') < html.index('id="summary-h"'))
    assert 'id="dataroom-h"' in html  # no Term of the Day: Data Room goes last


# --- 10. Week Ahead data releases ------------------------------------------------------

def _fred_releases():
    return json.loads((FIX / "fred_release_dates.json").read_text(encoding="utf-8"))


def test_parse_fred_releases_keeps_major_only_and_dedupes():
    events = calendar.parse_fred_releases(_fred_releases())
    assert [(e["date"], e["title"]) for e in events] == [
        ("2026-10-14", "Consumer Price Index (CPI)"),
        ("2026-10-15", "Retail sales"),
        ("2026-10-15", "Producer Price Index (PPI)"),
        ("2026-10-16", "Housing starts (New Residential Construction)"),
    ]
    assert all(e["time"] is None and e["source"] == "FRED" for e in events)
    assert events[0]["url"] == "https://fred.stlouisfed.org/release?rid=10"


@respx.mock
def test_fetch_fred_releases_params_and_merge_with_fed():
    route = respx.get(calendar.FRED_RELEASES_URL).mock(
        return_value=httpx.Response(200, json=_fred_releases()))
    with httpx.Client() as client:
        events = calendar.fetch_fred_releases(client, "KEY", SUN)
    params = route.calls[0].request.url.params
    assert params["realtime_start"] == "2026-10-12" and params["realtime_end"] == "2026-10-18"
    assert params["include_release_dates_with_no_data"] == "true"
    assert params["sort_order"] == "asc"
    fed = [{"date": "2026-10-14", "time": "2:00 PM ET", "title": "Beige Book",
            "source": "Federal Reserve", "url": "https://www.federalreserve.gov/x"}]
    merged = calendar.week_window(fed + events + events, SUN)
    assert [e["title"] for e in merged][:2] == ["Beige Book", "Consumer Price Index (CPI)"]
    assert len(merged) == 5
    md = calendar.week_ahead_markdown(merged)
    assert "- **Wed Oct 14:** Consumer Price Index (CPI), [FRED](" in md


def test_week_events_survive_one_source_failing(monkeypatch):
    def boom(*a):
        raise httpx.ConnectError("offline")
    ev = {"date": "2026-10-14", "time": None, "title": "GDP", "source": "FRED",
          "url": "https://fred.stlouisfed.org/release?rid=53"}
    monkeypatch.setattr(main.calendar, "fetch_week", boom)
    monkeypatch.setattr(main.calendar, "fetch_fred_releases", lambda c, k, d: [ev])
    problems = []
    assert main._week_events(None, SUN, "KEY", problems) == [ev]
    assert problems == ["calendar: ConnectError: offline"]
    monkeypatch.setattr(main.calendar, "fetch_fred_releases", boom)
    assert main._week_events(None, SUN, "KEY", []) is None


# --- 11. Source names and story ranking --------------------------------------------------

def test_display_source_names():
    assert display_source("Globest") == "GlobeSt.com"
    assert display_source("globest") == "GlobeSt.com"
    assert display_source("citybiz") == "Citybiz"
    assert display_source("Commercial Observer") == "Commercial Observer"


def test_factsheet_stories_use_display_names():
    conn = connect(":memory:")
    _add(conn, 1, "Office tower sells", source="Globest")
    assert _fs(conn)["top"][0]["source"] == "GlobeSt.com"


def test_charged_government_deals_rank_below_market_deals():
    conn = connect(":memory:")
    _add(conn, 1, "U.S. Government Buys a $950M ICE Complex in the Inland Empire",
         importance=10)
    for n in range(2, 8):
        _add(conn, n, f"Office deal {n}", importance=6)
    sheet = _fs(conn)
    top = [s["url"] for s in sheet["top"]]
    assert "https://x.com/1" not in top and len(top) == 5
    assert sheet["quick_hits"][0]["url"] == "https://x.com/1"  # still eligible
    conn2 = connect(":memory:")
    _add(conn2, 1, "Prison portfolio sells to state", importance=10)
    assert _fs(conn2)["top"][0]["url"] == "https://x.com/1"  # nothing else to lead with


def test_ice_exchange_not_treated_as_charged():
    conn = connect(":memory:")
    _add(conn, 1, "ICE Mortgage Technology launches CRE data tool", importance=9)
    _add(conn, 2, "Office deal", importance=5)
    assert _fs(conn)["top"][0]["url"] == "https://x.com/1"
