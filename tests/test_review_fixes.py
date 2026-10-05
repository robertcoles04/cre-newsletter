"""Fixes from the review of 6e29a4c: empty headings, Trepp parsing, mover matching,
dash replacement around links, Fed odds normalization, Week Ahead safety net,
region patterns, HY spread as-of."""

from datetime import date, datetime, timedelta, timezone

from src import main, trepp
from src.checks import check_issue
from src.cleanup import tidy
from src.config import ET
from src.factsheet import build_factsheet
from src.markets import region_of
from src.models import FedOdds, Item, RatePoint, ReitQuote
from src.render_html import hint_for, market_summary, no_dashes, render_issue_html
from src.store import connect, save_items, save_rates

TUE = date(2026, 10, 6)
SUN = date(2026, 10, 11)


# --- 1. empty headings ------------------------------------------------------

MW = """## Top Stories

A story.

## Market Watch

### Sun Belt

### West Coast

A Seattle story. [src](https://x.com/s)

### International

## Quick Hits

- Item
"""


def test_tidy_drops_empty_subheads_keeps_filled():
    out = tidy(MW)
    assert "### Sun Belt" not in out and "### International" not in out
    assert "### West Coast" in out and "## Market Watch" in out and "## Quick Hits" in out


def test_tidy_drops_section_left_empty():
    md = "## Market Watch\n\n### Sun Belt\n\n### International\n\n## Quick Hits\n\n- x\n"
    out = tidy(md)
    assert "Market Watch" not in out and "## Quick Hits" in out


def test_tidy_drops_empty_and_na_why_lines():
    md = ("## The Numbers\n\n**What it means:** Costs rise.\n\n**Why NNN REIT (NNN) moved:** \n\n"
          "**Why n/a moved:** No company-specific news today.\n\n"
          "**Why Prologis (PLD) moved:** n/a\n\n"
          "**Why Equinix (EQIX) moved:** A big lease. [S](https://x.com/e)\n")
    out = tidy(md)
    assert "NNN REIT" not in out and "Why n/a" not in out and "Prologis" not in out
    assert "Why Equinix (EQIX) moved:" in out and "**What it means:**" in out


def test_tidy_ignores_footer_and_comments_as_body():
    md = ("## Top Stories\n\nA story.\n\n## AI in Real Estate\n\n<!-- note -->\n\n"
          "For informational purposes only. Not investment advice.\n")
    out = tidy(md)
    assert "AI in Real Estate" not in out
    assert out.rstrip().endswith("For informational purposes only. Not investment advice.")


def test_render_applies_tidy_to_old_markdown():
    html = render_issue_html(MW, {"values": {}, "date": "2026-10-06"}, [], None)
    assert "Sun Belt" not in html and "West Coast" in html


def test_check_flags_empty_heading():
    sheet = {"day_type": "saturday", "values": {}}
    kinds = [p for p in check_issue(MW, sheet, []) if p["kind"] == "empty_heading"]
    assert {p["detail"] for p in kinds} == {"Sun Belt", "International"}


def test_main_tidies_after_fill(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(main.deliver_mod, "deliver",
                        lambda conn, d, md, *a, **k: seen.setdefault("md", md) or tmp_path)
    monkeypatch.setattr(main, "_collect", lambda *a, **k: (None, [], None, {}))
    monkeypatch.setattr(main.classify, "classify", lambda conn, run: 0)

    def claude(prompt, model):
        return MW + "\nFor informational purposes only. Not investment advice.\n"

    class Args:
        date, force, dry_run, db = "2026-10-06", True, True, str(tmp_path / "t.db")
    main.run(Args(), client=type("C", (), {"close": lambda s: None})(), claude=claude,
             gh=lambda a: "x/1", repo_root=tmp_path)
    assert "### Sun Belt" not in seen["md"] and "### West Coast" in seen["md"]


# --- 2. Trepp -----------------------------------------------------------------

def test_trepp_sector_titles_rejected():
    assert trepp.parse_title("Office CMBS Delinquency Rate Climbs 45 bps in September 2026") is None
    assert trepp.parse_title("Lodging CMBS Delinquency Rate Falls 20 bps in May 2026") is None


def test_trepp_bps_must_follow_first_direction_word():
    assert trepp.parse_title(
        "CMBS Delinquency Rate Rises in September 2026; Office Rate Falls 50 bps") is None
    assert trepp.parse_title("CMBS Delinquency Rate Rose 17 bps in Sept 2026")["chg"] == "+17 bps"
    assert trepp.parse_title("CMBS Delinquency Rate Fell by 9 Basis Points in March 2026")[
        "chg"] == "-9 bps"


def test_trepp_overall_rate_only():
    assert trepp.parse_rate("The overall delinquency rate rose to 7.29%.") == "7.29%"
    assert trepp.parse_rate("The Trepp CMBS delinquency rate rose 17 basis points to 8.02% in "
                            "September 2026.") == "8.02%"
    assert trepp.parse_rate("The office delinquency rate jumped to 11.66%.") is None
    assert trepp.parse_rate("The lodging rate climbed to 6.10% while the overall rate "
                            "held.") is None


def test_cmbs_values_only_from_trepp_source():
    conn = connect(":memory:")
    pub = datetime(2026, 10, 5, 12, tzinfo=ET)
    save_items(conn, [Item("Google News", "https://g.com/a",
                           "CMBS Delinquency Rate Rose 30 bps in September 2026", pub)])
    assert trepp.cmbs_values(conn, TUE) is None
    save_items(conn, [Item("Trepp", "https://trepp.com/a",
                           "CMBS Delinquency Rate Rose 17 bps in September 2026", pub)])
    assert trepp.cmbs_values(conn, TUE)["CMBS_DQ_CHG"] == "+17 bps"


# --- 3. movers: title only ------------------------------------------------------

def _add(conn, n, title, summary=""):
    pub = datetime(2026, 10, 6, 3, tzinfo=ET).astimezone(timezone.utc).isoformat(
        timespec="seconds")
    cur = conn.execute(
        "INSERT INTO items (source, url, canonical_url, title, published_at, summary,"
        " priority, also_covered, section, importance) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("Src", f"https://x.com/{n}", f"x.com/{n}", title, pub, summary, 50, "[]", "top", 5))
    conn.execute("UPDATE items SET cluster_id = id WHERE id = ?", (cur.lastrowid,))
    conn.commit()


def test_mover_news_matches_title_only():
    conn = connect(":memory:")
    _add(conn, 1, "Office sale closes", summary="Buyer also owns Prologis stock.")
    _add(conn, 2, "STAGNANT office market")  # not the STAG ticker
    quotes = [ReitQuote("STAG", TUE, 30.0, 1.9), ReitQuote("PLD", TUE, 110.0, -1.0)]
    sheet = build_factsheet(conn, TUE, None, quotes, [], None)
    assert sheet["mover_news"] == {"up": None, "down": None}


# --- 4. no_dashes leaves links alone ----------------------------------------------

def test_no_dashes_skips_link_targets_and_autolinks():
    text = ("Deal closed — finally [S](https://x.com/a—b) and "
            "<https://x.com/c–d> plus https://x.com/e—f end")
    out = no_dashes(text)
    assert "(https://x.com/a—b)" in out and "<https://x.com/c–d>" in out
    assert "https://x.com/e—f" in out and out.startswith("Deal closed, finally")


# --- 5. Fed odds --------------------------------------------------------------------

def _fed(outcomes):
    odds = FedOdds("Fed Decision in October?", date(2026, 10, 28), outcomes)
    return build_factsheet(connect(":memory:"), TUE, odds, [], [], None)["values"]


def test_fed_odds_normalized_to_100():
    v = _fed([("No change", 0.795), ("25 bps decrease", 0.005), ("25+ bps increase", 0.208)])
    vals = [float(v[k].rstrip("%")) for k in ("FED_CUT", "FED_HOLD", "FED_HIKE")]
    assert round(sum(vals), 1) == 100.0
    assert v["FED_HOLD"] == "78.9%"


def test_fed_odds_rounding_fixed_to_total_100():
    v = _fed([("No change", 1 / 3), ("25 bps decrease", 1 / 3), ("25 bps increase", 1 / 3)])
    vals = [float(v[k].rstrip("%")) for k in ("FED_CUT", "FED_HOLD", "FED_HIKE")]
    assert round(sum(vals), 1) == 100.0


def test_fed_bucket_without_market_is_na():
    v = _fed([("No change", 0.9), ("25 bps decrease", 0.1)])
    assert (v["FED_CUT"], v["FED_HOLD"], v["FED_HIKE"]) == ("10.0%", "90.0%", "n/a")


def test_fed_hints():
    assert hint_for("FED_HOLD", {}) == "Chance rates stay the same"
    assert hint_for("FED_HIKE", {}) == "Chance rates go up"
    assert hint_for("FED_CUT", {}).startswith("Chance rates go down")
    html = market_summary({"FED_CUT": "1.0%", "FED_HOLD": "99.0%", "FED_HIKE": "n/a"}, None, "")
    assert html.count("Polymarket traders") == 1


# --- 6. Week Ahead safety net -----------------------------------------------------

def _sunday_sheet():
    return {"day_type": "sunday", "values": {"WEEK_AHEAD": "- **Wed Oct 14:** Beige Book"}}


def test_check_requires_week_ahead_on_sunday():
    md = "## Week Ahead\n\nNo Fed meeting this week.\n"
    kinds = check_issue(md, _sunday_sheet(), [])
    assert {"kind": "missing_placeholder", "detail": "WEEK_AHEAD"} in kinds
    ok = check_issue(md + "\n{{WEEK_AHEAD}}\n", _sunday_sheet(), [])
    assert {"kind": "missing_placeholder", "detail": "WEEK_AHEAD"} not in ok


def test_ensure_week_ahead_appends_under_section():
    md = ("## REIT Weekly\n\nx\n\n## Week Ahead\n\nNo Fed meeting this week.\n\n"
          "## Term of the Day\n\nt\n")
    out = main.ensure_week_ahead(md, _sunday_sheet())
    week = out.split("## Week Ahead")[1].split("## Term of the Day")[0]
    assert "{{WEEK_AHEAD}}" in week


def test_ensure_week_ahead_adds_missing_section_before_term():
    md = "## REIT Weekly\n\nx\n\n## Term of the Day\n\nt\n"
    out = main.ensure_week_ahead(md, _sunday_sheet())
    assert out.index("## Week Ahead") < out.index("## Term of the Day")
    assert "{{WEEK_AHEAD}}" in out


def test_ensure_week_ahead_noop_when_present_or_weekday():
    md = "## Week Ahead\n\n{{WEEK_AHEAD}}\n"
    assert main.ensure_week_ahead(md, _sunday_sheet()) == md
    assert main.ensure_week_ahead("## Top Stories\n", {"day_type": "weekday",
                                                       "values": {}}) == "## Top Stories\n"


# --- 7. region patterns -------------------------------------------------------------

def test_region_patterns_fixed():
    assert region_of("L.A. office tower sells") == "west_coast"
    assert region_of("U.K. logistics deal") == "international"
    assert region_of("Portland, Maine hotel sells") is None
    assert region_of("Portland, Ore. office tower sells") == "west_coast"
    assert region_of("Tbilisi, Georgia office deal") is None
    assert region_of("Atlanta, Georgia office deal") == "sun_belt"


# --- 8. HY spread as-of -----------------------------------------------------------

def test_hy_spread_asof_only_when_different_from_rates():
    conn = connect(":memory:")
    save_rates(conn, [RatePoint("DGS10", date(2026, 10, 1), 4.2),
                      RatePoint("DGS10", date(2026, 10, 2), 4.3),
                      RatePoint("BAMLH0A0HYM2", date(2026, 9, 30), 3.2),
                      RatePoint("BAMLH0A0HYM2", date(2026, 10, 1), 3.1)])
    v = build_factsheet(conn, TUE, None, [], [], None)["values"]
    assert v["HY_OAS_ASOF"] == "Oct 1"
    assert "High-yield spread (Oct 1)" in market_summary(v, None, "")
    save_rates(conn, [RatePoint("BAMLH0A0HYM2", date(2026, 10, 2), 3.0)])
    v = build_factsheet(conn, TUE, None, [], [], None)["values"]
    assert "HY_OAS_ASOF" not in v
    assert "High-yield spread<" in market_summary(v, None, "")


def test_cleanup_footer_matches_checks():
    from src import checks, cleanup
    assert cleanup.FOOTER == checks.FOOTER
