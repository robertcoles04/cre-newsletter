"""Broadsheet redesign: ticker, masthead, front-page layout, empty-section collapse,
glossary page."""
import json
import re

from src import site
from src.checks import FOOTER
from src.render_html import (_pair, render_issue_html, render_page, split_term, ticker,
                             ticker_items)

VALUES = {
    "RATES_ASOF": "Oct 5", "DGS10": "5.31%", "DGS10_CHG": "+3 bps", "DGS5": "5.06%",
    "DGS5_CHG": "0 bps", "DGS2": "4.83%", "DGS2_CHG": "+5 bps", "SOFR": "3.89%",
    "SOFR_CHG": "+1 bps", "DFF": "3.75% to 4.00%", "DFF_CHG": "0 bps", "MORTGAGE30US": "7.28%",
    "MORTGAGE30US_CHG": "+25 bps", "VNQ": "$89.15", "VNQ_CHG": "-0.4%", "REIT_UP": "UDR +1.7%",
    "REIT_DOWN": "NNN -1.4%", "FED_HOLD": "81.5%", "HY_OAS": "3.10%", "CMBS_DQ": "8.02%",
}
FS = {"date": "2026-10-06", "day_type": "weekday", "values": VALUES}

ISSUE = f"""## The Brief

- Rates are breaking deals. [WSJ](https://a.com/1)
- Office loans go unpaid. [Wolf Street](https://a.com/2)

## Debt Markets

Office CMBS delinquencies rose past the crisis peak as more landlords fail to refinance. [Wolf Street](https://a.com/2)

## Top Stories

### Rising rates are breaking CRE deals

Buyers cut offers while sellers refuse to budge.
**Why it matters:** for a buyer using a loan, higher rates mean less debt. [WSJ](https://a.com/1)

### L&L buys Midtown East office

L&L closed a purchase, [The Real Deal](https://a.com/3) reports.

**Why it matters:** a fresh comp for New York offices.

**Coffee chat talking points:**
- Point one 8.7 billion. [CRE Daily](https://a.com/4)
- Point two. [The Real Deal](https://a.com/3)
- Point three. [WSJ](https://a.com/1)

## Market Watch

### Sun Belt

Blackstone sold warehouses. [The Real Deal](https://a.com/5)
**Why it matters:** a big seller trims.

## Quick Hits

- Seattle tower sold cheap. [BJ](https://a.com/6) A low comp.

## Term of the Day

**Extend and pretend:** when a lender pushes back a loan's due date instead of foreclosing,
hoping the building recovers before the new deadline. It avoids a loss today.

{FOOTER}
"""


def html_(md=ISSUE, fs=FS, **kw):
    return render_issue_html(md, fs, [], None, **kw)


# ----------------------------------------------------------------- ticker

def test_ticker_items_in_order_from_values():
    words = [w for _, w in ticker_items(VALUES)]
    assert words == [
        "10-Year 5.31% up 3 bps", "5-Year 5.06% unchanged", "2-Year 4.83% up 5 bps",
        "SOFR 3.89% up 1 bp", "Fed funds 3.75% to 4.00%", "30-Year mortgage 7.28% up 25 bps",
        "VNQ $89.15 down 0.4%", "UDR up 1.7%", "NNN down 1.4%", "Fed hold odds 81.5%",
        "high-yield spread 3.10%", "CMBS delinquency 8.02%"]


def test_ticker_skips_missing_and_na_values():
    vals = {**VALUES, "DGS5": "n/a", "SOFR": "", "HY_OAS": "N/A", "CMBS_DQ": None}
    vals.pop("DGS2")
    words = " | ".join(w for _, w in ticker_items(vals))
    for gone in ("5-Year", "2-Year", "SOFR", "high-yield", "CMBS"):
        assert gone not in words
    assert "n/a" not in ticker(vals).lower()
    assert ticker({}) == "" and ticker({"DGS10": "n/a"}) == ""


def test_ticker_markup_aria_and_three_copies():
    t = ticker(VALUES)
    assert t.startswith('<div class="ticker" role="img" aria-label="Markets as of Oct 5 close: '
                        '10-Year 5.31% up 3 bps, 5-Year 5.06% unchanged,')
    assert 'class="ticker-track" aria-hidden="true"' in t
    assert t.count('<span class="tk tk-date">OCT 5 CLOSE</span>') == 3
    assert t.count("10-Yr <b>5.31%</b>") == 3
    assert 'style="--n: 13"' in t  # date tag + 12 items: duration scales with the count
    label = re.search(r'aria-label="([^"]*)"', t).group(1)
    assert label.count("5.31%") == 1  # every value once in words


def test_ticker_colors_prices_only():
    t = ticker(VALUES)
    assert '<span class="tk-rate">' in t  # 10-Yr +3 bps: neutral navy tint
    assert '<span class="tk-flat">0 bps</span>' in t  # no arrow on 0 bps
    assert 'REITs (VNQ) <b>$89.15</b> <span class="tk-down">' in t
    assert 'UDR <b><span class="tk-up">' in t and 'NNN <b><span class="tk-down">' in t
    assert "Fed funds <b>3.75% to 4.00%</b></span>" in t  # target range: no change shown


def test_ticker_css_is_pure_css_with_reduced_motion():
    h = html_()
    assert "<script" not in h
    assert "@keyframes cb-ticker" in h and "translateX(-33.3333%)" in h
    assert "animation-play-state: paused" in h
    assert "calc(var(--n, 12) * 4.6s)" in h and "calc(var(--n, 12) * 2.5s)" in h
    rm = h.split("@media (prefers-reduced-motion: reduce)")[1]
    assert ".ticker { overflow-x: auto; }" in rm and ".ticker-track { animation: none; }" in rm


# ----------------------------------------------------------------- masthead

def test_masthead_utility_row_and_nav_slot():
    h = html_(nav='<nav class="site-nav" aria-label="Site">N</nav>')
    head = h.split("</header>")[0]
    assert '<time datetime="2026-10-06">Tuesday, October 6, 2026</time>' in head
    assert re.search(r'<span class="edition">Daily Edition · \d min read</span>', head)
    assert "Free. New issue every weekday morning, lighter on weekends." in head
    assert 'class="double-rule"' in head and 'class="site-nav"' in head
    assert head.index('class="utility"') < head.index('class="ticker"') < head.index('class="name"')
    assert '<p class="dateline-m">Tuesday, October 6, 2026 · Daily Edition</p>' in head
    weekend = html_(fs={**FS, "date": "2026-10-10", "day_type": "saturday"})
    assert "Weekend Edition" in weekend


def test_no_dashes_in_new_code_text():
    h = html_()
    assert "—" not in h and "–" not in h


# ----------------------------------------------------------------- front page

def test_lead_story_beside_the_brief_then_snapshot():
    h = html_()
    front = h.split('<div class="front">')[1]
    assert front.index('<article class="lead"') < front.index('<section class="brief">') \
        < front.index('<section class="snapshot"')
    lead = h.split('<article class="lead"')[1].split("</article>")[0]
    assert '<p class="kicker">Top Story</p>' in lead
    assert ('<h2 class="hl" id="lead-h"><a href="https://a.com/1">Rising rates are breaking '
            'CRE deals</a></h2>') in lead
    assert '<div class="deck"><p>Buyers cut offers while sellers refuse to budge.</p></div>' in lead
    assert '<p class="source">Source: <a href="https://a.com/1">WSJ</a></p>' in lead
    assert ('<div class="why-panel"><p><strong class="why-label">Why it matters</strong> '
            'For a buyer using a loan') in lead
    assert "Top Story ·" not in h  # no invented kicker


def test_grid_story_keeps_inline_source_and_why():
    h = html_()
    sec = h.split('<section class="sec top-stories"')[1].split("</section>")[0]
    assert '<div class="stories c1">' in sec
    assert '<h3 class="hl"><a href="https://a.com/3">L&amp;L buys Midtown East office</a></h3>' in sec
    assert '<a href="https://a.com/3">The Real Deal</a> reports.' in sec
    assert '<p class="why"><strong>Why it matters:</strong> a fresh comp' in sec
    assert 'class="source"' not in sec  # the source is already linked in the summary


def test_story_grid_columns_follow_the_count():
    def stories(n):
        md = "## Top Stories\n\n" + "".join(f"### S{i}\n\nText {i}. [A](https://a.com/{i})\n\n"
                                            for i in range(n + 1))
        return re.search(r'<div class="stories (c\d)">', html_(md)).group(1)
    assert [stories(n) for n in (1, 2, 3, 4, 5, 6)] == ["c1", "c2", "c3", "c2", "c3", "c3"]


def test_coffee_band_numbered_points():
    h = html_()
    band = h.split('<section class="coffee"')[1].split("</section>")[0]
    assert '<ol class="points c3">' in band and band.count("<li>") == 3
    assert "**Coffee chat" not in h and "Coffee chat talking points:" not in h


def test_section_classes_and_pairs():
    h = html_()
    pair = h.split('<div class="pair">')[1]
    assert pair.index('<div class="pa"><section class="sec debt">') < \
        pair.index('<div class="pb"><section class="sec watch">')
    assert '<section class="sec quick-hits">' in h
    assert '<div class="pair back"><div class="pa"><section class="term"' in h
    assert '<p class="term-name">Extend and pretend</p>' in h
    assert "<p>When a lender pushes back" in h  # capitalized definition


# ----------------------------------------------------------------- empty sections collapse

def test_missing_sections_collapse_to_full_width():
    no_mw = ISSUE.split("## Market Watch")[0] + "## Quick Hits" + ISSUE.split("## Quick Hits")[1]
    h = html_(no_mw)
    assert '<div class="pair"><div class="pa"><section class="sec debt">' in h
    assert '<div class="pb"><section class="sec watch">' not in h
    assert ".pair > :only-child { grid-column: 1 / -1;" in h
    no_fs = html_(fs=None)
    assert '<div class="pair back"><div class="pa"><section class="term"' in no_fs
    assert '<div class="pb">' not in no_fs.split('<div class="pair back">')[1]


def test_weekend_without_top_stories_has_no_lead():
    md = ("## The Brief\n\n- One. [A](https://a.com/1)\n\n## Week in Review\n\nA week. "
          "[A](https://a.com/1)\n\n## Term of the Day\n\n**NOI:** income.\n")
    h = html_(md, {**FS, "day_type": "saturday"})
    assert '<div class="front no-lead">' in h and '<article class="lead"' not in h
    assert '<section class="sec wide"><h2 id="week-in-review">Week in Review</h2>' \
           '<div class="sec-body">' in h
    no_brief = html_("## Top Stories\n\n### Only story\n\nText. [A](https://a.com/1)\n")
    assert '<div class="front no-brief">' in no_brief


def test_unbalanced_pair_stacks_full_width():
    short = '<section class="sec debt"><h2>D</h2><p>One line.</p></section>'
    long = '<section class="sec watch"><h2>M</h2><p>' + "word " * 80 + "</p></section>"
    out = _pair(short, long)
    assert '<div class="pair' not in out
    assert '<section class="sec debt solo">' in out and '<section class="sec watch solo">' in out
    even = _pair(short, '<section class="sec watch"><h2>M</h2><p>Two words.</p></section>')
    assert even.startswith('<div class="pair">')
    assert _pair("", "") == ""


def test_split_term():
    assert split_term("**Cap rate:** net income over price.") == ("Cap rate",
                                                                  "Net income over price.")
    assert split_term("**NNN lease**: tenant pays.") == ("NNN lease", "Tenant pays.")
    assert split_term("No bold term here.") == (None, "No bold term here.")


def test_render_page_uses_latest_ticker():
    out = render_page("About", "<h1>About</h1>", factsheet=FS)
    assert 'class="ticker"' in out and "Tuesday, October 6, 2026" in out
    assert '<div class="page"><h1>About</h1></div>' in out


# ----------------------------------------------------------------- site

def _issue(root, day, md, values=VALUES):
    d = root / "issues"
    (d / "img").mkdir(parents=True, exist_ok=True)
    (d / f"{day}.md").write_text(md, encoding="utf-8")
    (d / f"{day}.json").write_text(json.dumps(
        {"date": day, "day_type": "weekday", "values": values}), encoding="utf-8")


def _build(tmp_path):
    root = tmp_path / "repo"
    _issue(root, "2026-10-05", "## Top Stories\n\nText.\n\n## Term of the Day\n\n"
           "**NNN lease:** a triple-net lease.\n\n" + FOOTER, {"DGS10": "4.00%"})
    _issue(root, "2026-10-06", ISSUE)
    (root / "issues" / "published.json").write_text(json.dumps(["2026-10-05", "2026-10-06"]),
                                                    encoding="utf-8")
    out = tmp_path / "site"
    site.build(root, out)
    return out


def test_glossary_page_alphabetical_with_dates(tmp_path):
    out = _build(tmp_path)
    g = (out / "glossary" / "index.html").read_text(encoding="utf-8")
    assert '<a href="../glossary/" aria-current="page">Glossary</a>' in g
    assert g.index("Extend and pretend") < g.index("NNN lease")
    assert '<h2 class="term-name">Extend and pretend</h2>' in g
    assert '<a href="../issues/2026-10-06/">Tuesday, October 6, 2026</a>' in g
    assert '<a href="../issues/2026-10-05/">Monday, October 5, 2026</a>' in g
    assert FOOTER in g and g.count(FOOTER) == 1
    assert "<loc>https://creblurb.org/glossary/</loc>" in (out / "sitemap.xml").read_text()


def test_glossary_merges_repeated_terms():
    from datetime import date
    issues = [{"date": d, "day": date.fromisoformat(d),
               "md": f"## Term of the Day\n\n**Cap rate:** def {d}.\n"}
              for d in ("2026-10-06", "2026-10-02")]
    entries = site.glossary_entries(issues)
    assert len(entries) == 1 and entries[0]["md"] == "Def 2026-10-06."
    assert [i["date"] for i in entries[0]["issues"]] == ["2026-10-06", "2026-10-02"]


def test_every_page_has_the_masthead_and_latest_ticker(tmp_path):
    out = _build(tmp_path)
    for rel in ("index.html", "archive/index.html", "about/index.html", "glossary/index.html",
                "404.html", "issues/2026-10-05/index.html"):
        page = (out / rel).read_text(encoding="utf-8")
        assert '<header class="site-head" id="top">' in page, rel
        assert 'class="double-rule"' in page and 'class="site-nav"' in page, rel
        assert ">Today</a>" in page and ">Markets</a>" in page and ">Glossary</a>" in page, rel
    # Non-issue pages carry the latest issue's ticker; an issue page carries its own.
    assert "10-Yr <b>5.31%</b>" in (out / "about/index.html").read_text(encoding="utf-8")
    old = (out / "issues/2026-10-05/index.html").read_text(encoding="utf-8")
    assert "10-Yr <b>4.00%</b>" in old
    assert 'href="../../issues/2026-10-06/#numbers">Markets</a>' in old
    home = (out / "index.html").read_text(encoding="utf-8")
    assert '<a href="#numbers">Markets</a>' in home and 'id="numbers"' in home
    assert 'href="https://creblurb.org/issues/2026-10-06/#numbers">Markets</a>' in \
        (out / "404.html").read_text(encoding="utf-8")


def test_ticker_pause_control_markup_and_css():
    from src.render_html import ticker_band
    band = ticker_band(VALUES)
    assert band.startswith('<div class="ticker-band"><input type="checkbox" id="ticker-pause" '
                           'class="visually-hidden-but-focusable"><label for="ticker-pause" '
                           'class="tk-pause">')
    assert ">Pause</span>" in band and ">Play</span>" in band
    # the control sits outside the aria-hidden track, before the role="img" band
    assert band.index('<label for="ticker-pause"') < band.index('<div class="ticker" role="img"')
    track = band[band.index('class="ticker-track"'):]
    assert "ticker-pause" not in track and "tk-pause" not in track
    assert ticker_band({}) == ""
    h = html_()
    assert band in h and "<script" not in h
    css = h[h.index("<style>"):h.index("</style>")]
    assert "#ticker-pause:checked ~ .ticker .ticker-track { animation-play-state: paused; }" in css
    assert "#ticker-pause:checked ~ .tk-pause .tk-on { display: inline; }" in css
    assert "#ticker-pause:focus-visible ~ .tk-pause { outline:" in css
    assert ".ticker:hover .ticker-track { animation-play-state: paused; }" in css
    assert ".tk-pause { min-height: 44px;" in css  # phones
    rm = css[css.index("@media (prefers-reduced-motion: reduce)"):]
    assert ".ticker-track { animation: none; }" in rm
    assert ".visually-hidden, .visually-hidden-but-focusable { position: absolute;" in css
    assert ".tk-pause, #ticker-pause { display: none; }" in rm  # no motion, no control
