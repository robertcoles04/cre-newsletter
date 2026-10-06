"""Fixes from the 2026-10-06 creblurb.org design critique (items 1 to 11)."""
import json
import re
from datetime import date, datetime, timedelta, timezone

from src import chart, site
from src.checks import check_issue, coffee_points
from src.config import ET, display_source
from src.factsheet import (_pct, _reit_names, big_movers, build_factsheet, pick_distress,
                           same_date_curve)
from src.models import RatePoint
from src.render_html import (_change, data_room, market_snapshot, read_minutes,
                             render_issue_html)
from src.store import connect, save_rates

D = date(2026, 10, 6)
FOOTER = "For informational purposes only. Not investment advice."


def kinds(problems, kind):
    return [p["detail"] for p in problems if p["kind"] == kind]


# ---------------------------------------------------------------- 1. same-date curve

def _days(sid, values, end):
    """One point per day ending at `end` (business days are not needed for the math)."""
    return [RatePoint(sid, end - timedelta(days=len(values) - 1 - i), v)
            for i, v in enumerate(values)]


def test_curve_value_change_and_asof(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    save_rates(conn, _days("DGS10", [5.20, 5.28, 5.31], date(2026, 10, 5)))
    save_rates(conn, _days("DGS2", [4.70, 4.78], date(2026, 10, 4)))
    d, bps, chg = same_date_curve(conn, D)
    assert d == date(2026, 10, 4)
    assert bps == 50  # 5.28 - 4.78 on Oct 4
    assert chg == 0   # Oct 3: 5.20 - 4.70 = 50
    sheet = build_factsheet(conn, D, None, [], [], None)
    v = sheet["values"]
    assert v["T10Y2Y"] == "50 bps" and v["T10Y2Y_CHG"] == "0 bps"
    assert v["T10Y2Y_ASOF"] == "Oct 4"  # differs from RATES_ASOF (Oct 5): labeled
    assert v["RATES_ASOF"] == "Oct 5"


def test_curve_falls_back_to_fred_when_no_common_date(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    save_rates(conn, _days("DGS10", [5.31], date(2026, 10, 5)))
    save_rates(conn, _days("DGS2", [4.83], date(2026, 10, 2)))
    save_rates(conn, _days("T10Y2Y", [0.46, 0.47], date(2026, 10, 2)))
    assert same_date_curve(conn, D) is None
    v = build_factsheet(conn, D, None, [], [], None)["values"]
    assert v["T10Y2Y"] == "47 bps"


def test_curve_consistency_check():
    base = {"DGS10_DATE": "2026-10-05", "DGS2_DATE": "2026-10-05", "T10Y2Y_DATE": "2026-10-05",
            "DGS10_CHG": "+3 bps", "DGS2_CHG": "+5 bps"}
    ok = {"day_type": "saturday", "values": {**base, "T10Y2Y_CHG": "-2 bps"}}
    bad = {"day_type": "saturday", "values": {**base, "T10Y2Y_CHG": "+2 bps"}}
    other_day = {"day_type": "saturday",
                 "values": {**base, "DGS2_DATE": "2026-10-02", "T10Y2Y_CHG": "+2 bps"}}
    md = "## Week in Review\n\nText.\n\n" + FOOTER
    assert not kinds(check_issue(md, ok, []), "curve_mismatch")
    assert kinds(check_issue(md, bad, []), "curve_mismatch") == [
        "10Y-2Y change +2 bps vs 10Y +3 minus 2Y +5"]
    assert not kinds(check_issue(md, other_day, []), "curve_mismatch")


# ---------------------------------------------------------------- 2. honest labels

def test_old_rows_show_their_own_asof():
    vals = {"RATES_ASOF": "Oct 5", "DGS2": "4.83%", "DGS2_CHG": "+5 bps", "DGS2_ASOF": "Oct 2",
            "HY_OAS": "3.10%", "HY_OAS_CHG": "-14 bps", "HY_OAS_ASOF": "Oct 2"}
    html = render_issue_html("## Top Stories\n\nText.\n\n" + FOOTER,
                             {"date": "2026-10-06", "day_type": "weekday", "values": vals}, [])
    assert "Tracks where the Fed is expected to set rates (as of Oct 2)" in html
    assert "High-yield spread (Oct 2)" in html


def test_yield_curve_key_is_never_today(tmp_path, monkeypatch):
    words = []
    real = chart.plt.Axes.text
    monkeypatch.setattr(chart.plt.Axes, "text",
                        lambda self, x, y, s, *a, **k: words.append(s) or real(self, x, y, s, *a, **k))
    curve = [("2Y", 4.8, 4.4), ("5Y", 5.0, 4.5), ("10Y", 5.3, 4.8)]
    chart.yield_curve(curve, tmp_path / "c.png", date(2026, 10, 5))
    assert "Oct 5" in words and "Today" not in words


# ---------------------------------------------------------------- 3. no repeats

URL_A, URL_B, URL_C = "https://a.com/1", "https://b.com/2", "https://c.com/3"


def test_repeat_in_issue_flags_three_links_and_debt_plus_distress():
    md = (f"## The Brief\n\n- One. [A]({URL_A})\n\n"
          f"## Debt Markets\n\nLoan news. [A]({URL_A})\n\nMore. [B]({URL_B})\n\n"
          f"**Distress Watch:** Same loan. [A]({URL_A})\n\n"
          f"## Top Stories\n\nStory. [C]({URL_C})\n\n" + FOOTER)
    notes = kinds(check_issue(md, {"day_type": "saturday"}, []), "repeat_in_issue")
    assert f"linked 3 times: {URL_A}" in notes
    assert f"Debt Markets and Distress Watch cite the same story: {URL_A}" in notes
    clean = (f"## The Brief\n\n- One. [A]({URL_A})\n\n## Debt Markets\n\nLoan. [A]({URL_A})\n\n"
             f"**Distress Watch:** Other. [B]({URL_B})\n\n" + FOOTER)
    assert not kinds(check_issue(clean, {"day_type": "saturday"}, []), "repeat_in_issue")


def _row(url, title, section="debt", summary=""):
    return {"url": url, "title": title, "summary": summary, "section": section}


def test_distress_pick_skips_debt_section_stories():
    window = [_row(URL_A, "Office loan defaults hit record"),
              _row(URL_B, "Lender refinances tower"),
              _row(URL_C, "Mall heads to foreclosure", section="top")]
    assert pick_distress(window, {URL_A, URL_B})["url"] == URL_C
    assert pick_distress(window, {URL_A, URL_C}) is None


def _add_item(conn, n, section, title, importance=8):
    pub = (datetime(2026, 10, 6, 3, 0, tzinfo=ET)).astimezone(timezone.utc).isoformat(
        timespec="seconds")
    cur = conn.execute(
        "INSERT INTO items (source, url, canonical_url, title, published_at, summary,"
        " priority, also_covered, section, importance) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("Src", f"https://x.com/{n}", f"x.com/{n}", title, pub, "sum", 50, "[]", section,
         importance))
    conn.execute("UPDATE items SET cluster_id = id WHERE id = ?", (cur.lastrowid,))
    conn.commit()


def test_factsheet_distress_is_not_a_debt_story(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    for n in range(4):
        _add_item(conn, n, "debt", f"Loan default number {n}", importance=9)
    _add_item(conn, 9, "debt", "Hotel loan sent to special servicing", importance=5)
    sheet = build_factsheet(conn, D, None, [], [], None)
    debt_urls = {s["url"] for s in sheet["debt"]}
    assert len(debt_urls) == 4
    assert sheet["distress"]["url"] == "https://x.com/9" not in debt_urls


# ---------------------------------------------------------------- 4. talking points

STORY = {"url": URL_A, "title": "Fund raises $500 million", "summary": "Vacancy hit 19.6%."}


def _coffee_md(points):
    return ("## Top Stories\n\n### Fund raises money\n\nText. [A](" + URL_A + ")\n\n"
            "**Coffee chat talking points:**\n\n" + "\n".join(f"- {p}" for p in points)
            + "\n\n## Quick Hits\n\n- x\n\n" + FOOTER)


def test_coffee_points_parse_and_pass():
    md = _coffee_md([f"Office vacancy at 19.6% means landlords cut deals. [A]({URL_A})",
                     f"A $500 million fund shows lenders are back. [A]({URL_A})"])
    assert len(coffee_points(md)) == 2
    sheet = {"day_type": "saturday", "top": [STORY]}
    assert not kinds(check_issue(md, sheet, []), "coffee_chat")


def test_coffee_points_flag_count_link_number_and_cliches():
    md = _coffee_md(["The smart money thinks the worst is behind us."])
    notes = kinds(check_issue(md, {"day_type": "saturday", "top": [STORY]}, []), "coffee_chat")
    assert any("1 talking points" in n for n in notes)
    assert any(n.startswith("no source link") for n in notes)
    assert any(n.startswith("no number") for n in notes)
    assert any("smart money" in n for n in notes) and any("worst is behind" in n for n in notes)


def test_coffee_point_number_must_be_sourced():
    md = _coffee_md([f"Vacancy is 42.5% now. [A]({URL_A})", f"Fund of $500 million. [A]({URL_A})"])
    probs = check_issue(md, {"day_type": "saturday", "top": [STORY]}, [])
    assert "Coffee chat talking points: 42.5" in kinds(probs, "unsourced_number")


def test_talking_points_render_as_pull_list_and_old_line_still_works():
    md = _coffee_md([f"Point one 19.6%. [A]({URL_A})", f"Point two. [A]({URL_A})"])
    html = render_issue_html(md, None, [])
    assert '<section class="coffee" aria-label="Coffee chat talking points">' in html
    assert '<h2 class="display">Coffee chat talking points</h2>' in html
    assert "Talking points you can use in networking conversations" in html
    assert '<ol class="points c2" role="list"><li>Point one 19.6%.' in html
    # No blank line after the label: still a list.
    tight = md.replace("points:**\n\n- Point one", "points:**\n- Point one")
    assert '<ol class="points c2" role="list">' in render_issue_html(tight, None, [])
    old = render_issue_html("## Top Stories\n\n**Coffee chat line:** A take. [A](" + URL_A
                            + ")\n\n" + FOOTER, None, [])
    assert '<h2 class="display">Coffee chat line</h2>' in old
    assert '<ol class="points c1" role="list"><li>A take.' in old


# ---------------------------------------------------------------- 5. small fixes

def test_mover_name_drops_a_repeated_ticker():
    v = {"REIT_UP": "UDR +1.7%", "REIT_DOWN": "NNN -1.4%"}
    _reit_names(v, {"UDR": {"name": "UDR"}, "NNN": {"name": "NNN REIT"}})
    assert v["REIT_UP_NAME"] == "UDR" and v["REIT_DOWN_NAME"] == "NNN REIT (NNN)"
    # Old fact sheets that still say "UDR (UDR)" render as "UDR", in rows and prose.
    html = render_issue_html("## The Numbers\n\n**Why UDR (UDR) moved:** note.\n\n" + FOOTER,
                             {"date": "2026-10-06", "day_type": "weekday",
                              "values": {"REIT_UP": "UDR +1.7%", "REIT_UP_NAME": "UDR (UDR)"}},
                             [])
    assert "UDR (UDR)" not in html and "Why UDR moved" in html


def test_rounded_zero_has_no_sign():
    assert _pct(0.04) == "0.0%" and _pct(-0.04) == "0.0%" and _pct(0.06) == "+0.1%"
    assert chart._pct(-0.01) == "0.0%" and chart._pct(1.66) == "+1.7%"
    html = render_issue_html("## Top Stories\n\nx\n\n" + FOOTER,
                             {"date": "2026-10-06", "day_type": "weekday",
                              "values": {"REIT_UP": "AVB 0.0%", "REIT_UP_NAME": "AvalonBay (AVB)"}},
                             [])
    assert '<span class="val">AvalonBay (AVB)</span><span class="chg unch">0.0%</span>' in html


def test_outlet_names_normalized():
    assert display_source("credaily") == "CRE Daily"
    assert display_source("credaily.com") == "CRE Daily"
    assert display_source("ocregister.com") == "Orange County Register"


def test_read_time_is_from_rendered_text_rounded_half_up():
    assert read_minutes("<p>" + "word " * 115 + "</p>") == 1  # 0.5 rounds up
    assert read_minutes("word " * 1035) == 5  # 4.5 rounds up (not to even)
    assert read_minutes("<span class='x'>" * 500 + "word") == 1  # tags are not words
    md = "## Top Stories\n\n" + "word " * 1275 + "\n\n" + FOOTER
    fs = {"date": "2026-10-06", "day_type": "weekday", "values": {}}
    a = render_issue_html(md, fs, [])
    b = render_issue_html(md, fs, [], extra_body="<section>Recent issues list</section>")
    assert re.search(r"(\d+) min read", a).group(1) == re.search(r"(\d+) min read", b).group(1)


def test_missing_fed_odds_show_as_na_when_meeting_known():
    html = render_issue_html("## Top Stories\n\nx\n\n" + FOOTER,
                             {"date": "2026-10-05", "day_type": "weekday",
                              "values": {"FED_MEETING": "Oct 28", "FED_TOP": "No change 78.5%"}},
                             [])
    for label in ("Odds of a cut", "Odds of a hold", "Odds of a hike"):
        assert label in html
    none = render_issue_html("## Top Stories\n\nx\n\n" + FOOTER,
                             {"date": "2026-10-05", "day_type": "weekday", "values": {}}, [])
    assert "Odds of a cut" not in none


def test_big_movers_and_calm_what_it_means():
    v = {"DGS10_CHG": "+3 bps", "MORTGAGE30US_CHG": "+25 bps", "SOFR_CHG": "-15 bps",
         "DGS5_CHG": "n/a"}
    assert big_movers(v) == ["SOFR", "30-Year Mortgage"]
    sheet = {"day_type": "saturday", "big_movers": ["30-Year Mortgage"]}
    calm = "## The Numbers\n\n**What it means:** Rates barely moved today.\n\n" + FOOTER
    named = ("## The Numbers\n\n**What it means:** Treasuries barely moved, but the mortgage "
             "rate jumped.\n\n" + FOOTER)
    assert kinds(check_issue(calm, sheet, []), "big_move_ignored")
    assert not kinds(check_issue(named, sheet, []), "big_move_ignored")
    assert not kinds(check_issue(calm, {"day_type": "saturday", "big_movers": []}, []),
                     "big_move_ignored")


# ---------------------------------------------------------------- 8. reading order

MD = (f"## The Brief\n\n- One. [A]({URL_A})\n\n## The Numbers\n\n- x\n\n"
      "**What it means:** Rates were little changed.\n\n"
      f"## Debt Markets\n\nLoan. [B]({URL_B})\n\n## Top Stories\n\n### Big deal\n\nText.\n\n"
      f"## Quick Hits\n\n- Hit. [C]({URL_C})\n\n## Term of the Day\n\n**Cap rate:** yield.\n\n"
      + FOOTER)
VALS = {"DGS10": "4.28%", "DGS10_CHG": "+4 bps", "SOFR": "4.30%", "SOFR_CHG": "-1 bps",
        "VNQ": "$91.20", "VNQ_CHG": "+0.8%", "RATES_ASOF": "Oct 5", "DGS5": "4.0%",
        "HY_OAS": "3.10%", "HY_OAS_CHG": "-14 bps", "HY_OAS_DATE": "2026-10-05"}


def test_snapshot_then_stories_then_full_summary():
    html = render_issue_html(MD, {"date": "2026-10-06", "day_type": "weekday", "values": VALS}, [])
    # The front (The Brief, the Snapshot strip, the lead story; desktop places them by
    # grid), then the stories, The Numbers, Term of the Day and the Data Room.
    order = [html.index(s) for s in (
        'id="the-brief"', 'id="snapshot-h"', 'id="top-stories"', '<nav class="toc"',
        'id="debt-markets"', 'id="quick-hits"', 'id="summary-h"', "Rates were little changed",
        'id="term-h"', 'id="dataroom-h"')]
    assert order == sorted(order)
    snap = html.split('<section class="snapshot"')[1].split("</section>")[0]
    assert snap.count('<div class="row cell">') == 3
    assert "10-Year Treasury" in snap and "SOFR" in snap and "Real estate stocks (VNQ)" in snap
    assert '<a href="#numbers">Full market data</a>' in snap
    assert '<dd class="hint">Benchmark for long-term property loans</dd>' in snap
    toc = html.split('<nav class="toc"')[1].split("</nav>")[0]
    assert "Market Snapshot" not in toc and toc.index("Quick Hits") < toc.index("The Numbers")


def test_snapshot_empty_without_data():
    assert market_snapshot({}) == ""


# ---------------------------------------------------------------- 9. neutral rate marks

def test_rates_marks_are_neutral_prices_keep_color():
    html = render_issue_html(MD, {"date": "2026-10-06", "day_type": "weekday", "values": VALS}, [])
    assert 'class="chg up rate"' in html  # 10Y +4 bps
    assert 'class="chg down rate"' in html  # SOFR -1 bps
    assert 'class="chg up"><svg' in html  # VNQ +0.8%: a price keeps green
    room = data_room(VALS, date(2026, 10, 6))
    assert 'class="chg down rate"' in room
    assert _change("+4 bps", neutral=True).startswith('<span class="chg up rate">')
    assert ".chg.rate { color: var(--navy); }" in html


def test_na_has_no_help_cursor_and_tag_is_13px():
    html = render_issue_html("x", None, [])
    assert "cursor: help" not in html
    assert ".tag { color: var(--muted); font-size: 13px;" in html


# ---------------------------------------------------------------- 6, 7, 10, 11. site

def _issue(root, day, md):
    d = root / "issues"
    (d / "img").mkdir(parents=True, exist_ok=True)
    (d / f"{day}.md").write_text(md, encoding="utf-8")
    (d / f"{day}.json").write_text(json.dumps(
        {"date": day, "day_type": "weekday", "values": VALS}), encoding="utf-8")


def _build(tmp_path):
    root = tmp_path / "repo"
    _issue(root, "2026-10-05", "## The Brief\n\n- Office rents rose. [A](https://a.com/1)\n\n"
           "## Top Stories\n\nNo headline here. Text.\n\n" + FOOTER)
    _issue(root, "2026-10-06", MD.replace("### Big deal", "### Rising rates are breaking CRE deals"))
    (root / "issues" / "published.json").write_text(json.dumps(["2026-10-05", "2026-10-06"]),
                                                    encoding="utf-8")
    out = tmp_path / "site"
    site.build(root, out)
    return out


def read(p):
    return p.read_text(encoding="utf-8")


def test_favicon_robots_sitemap(tmp_path):
    out = _build(tmp_path)
    svg = read(out / "favicon.svg")
    assert svg.startswith("<svg") and "#0E2A47" in svg and "#A9853A" in svg and ">CB<" in svg
    for page in ("index.html", "archive/index.html", "about/index.html", "404.html",
                 "glossary/index.html", "issues/2026-10-06/index.html"):
        assert ('<link rel="icon" type="image/svg+xml" href="https://creblurb.org/favicon.svg">'
                in read(out / page)), page
    robots = read(out / "robots.txt")
    assert "User-agent: *" in robots and "Allow: /" in robots
    assert "Sitemap: https://creblurb.org/sitemap.xml" in robots
    sm = read(out / "sitemap.xml")
    for url in ("https://creblurb.org/", "https://creblurb.org/archive/",
                "https://creblurb.org/about/", "https://creblurb.org/glossary/",
                "https://creblurb.org/issues/2026-10-05/",
                "https://creblurb.org/issues/2026-10-06/"):
        assert f"<loc>{url}</loc>" in sm


def test_return_line_and_home_note(tmp_path):
    out = _build(tmp_path)
    issue = read(out / "issues/2026-10-05/index.html")
    line = ('<p class="return">New issue every morning at '
            '<a href="https://creblurb.org/">creblurb.org</a>.</p>')
    assert line in issue and issue.index(line) < issue.index('<nav class="issue-nav"')
    home = read(out / "index.html")
    assert line in home
    # The "Free..." note now sits in every page's masthead utility row.
    note = "<span>Free. New issue every weekday morning, lighter on weekends.</span>"
    assert note in home and note in read(out / "archive/index.html")


def test_archive_shows_headlines(tmp_path):
    out = _build(tmp_path)
    arc = read(out / "archive/index.html")
    assert ('Tuesday, October 6, 2026</a><span class="headline">Rising rates are breaking '
            'CRE deals</span>') in arc
    assert ('Monday, October 5, 2026</a><span class="headline">Office rents rose</span>'
            in arc)  # no Top Stories headline: the describe() sentence
    assert site.headline("## Top Stories\n\n### A **bold** [link](https://x.com)\n", D) == \
        "A bold link"


def test_404_copy_once(tmp_path):
    out = _build(tmp_path)
    page = read(out / "404.html")
    main = page.split('<main id="content" class="wrap">')[1]
    assert main.count("Page not found") == 1
    assert "That page doesn't exist. Today's issue is on the home page." in main
