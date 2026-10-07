from datetime import date
from pathlib import Path

from src.checks import FOOTER
from src.render_html import render_issue_html, render_page

VALUES = {
    "DGS10": "4.28%", "DGS10_CHG": "+4 bps", "RATES_ASOF": "Oct 2",
    "DGS5": "3.91%", "DGS5_CHG": "-2 bps",
    "SOFR": "3.87%", "SOFR_CHG": "unch",
    "DFF": "n/a", "DFF_CHG": "n/a",
    "FED_MEETING": "Oct 28", "FED_TOP": "No change 82.5%",
    "FED_CUT": "15.0%", "FED_HOLD": "82.5%", "FED_HIKE": "2.5%",
    "VNQ": "$89.50", "VNQ_CHG": "+0.4%",
    "REIT_UP": "NNN +1.9%", "REIT_DOWN": "MAA -1.0%",
    "VNQ_YIELD": "3.95%", "SPREAD_10Y": "-33 bps",
}
FACTSHEET = {"date": "2026-10-06", "day_type": "weekday", "values": VALUES}

MD = f"""## The Numbers

<!-- template note -->

*Rates as of Oct 2 close.*

- **10Y Treasury:** 4.28% (+4 bps)
- **SOFR:** 3.87% (unch)

**What it means:** Floating-rate borrowers keep paying up for longer.

![Chart of the Day](img/2026-10-06-chart.png)

## Top Stories

### Big deal closes

Two sentences here. [source](https://example.com/a)

## Term of the Day

**Cap rate:** Net operating income divided by price.

{FOOTER}
"""


def render(md=MD, factsheet=FACTSHEET, problems=(), chart="img/2026-10-06-chart.png"):
    return render_issue_html(md, factsheet, list(problems), chart, run_date=date(2026, 10, 6))


def test_summary_rows_render_from_values():
    html = render()
    assert '<h2 id="summary-h" class="display">The Numbers</h2>' in html
    for label in ("10-Year Treasury", "5-Year Treasury", "SOFR", "Fed Funds",
                  "FOMC: next Fed meeting", "Odds of a cut", "Odds of a hold",
                  "Odds of a hike",
                  "Real estate stocks (VNQ)", "Biggest gain today", "Biggest drop today",
                  "REIT dividend yield", "REIT yield vs. 10-Year Treasury"):
        assert label in html
    assert '<span class="hint">Benchmark for long-term property loans</span>' in html
    assert "Sources" not in html
    assert "4.28%" in html and "82.5%" in html and "Oct 28" in html
    assert html.count("Polymarket traders") == 1  # hint on the first odds row only
    assert 'class="chg up"' in html and 'class="chg down"' in html
    assert 'class="chg unch"' in html
    assert "<svg" in html
    assert "Rates as of Oct 2 close" in html


def test_na_renders_muted_na_text():
    html = render()
    assert ('<span class="na">n/a<span class="sr-only"> (data unavailable today)</span>'
            '</span>') in html
    assert 'aria-label="Data unavailable today"' not in html
    assert "&mdash;" not in html


def test_reit_rows_show_name_and_property_type():
    vals = {**VALUES, "REIT_UP_NAME": "NNN REIT (NNN)", "REIT_UP_TYPE": "Owns single-tenant retail",
            "REIT_DOWN_NAME": "n/a", "REIT_DOWN_TYPE": "n/a"}
    html = render(factsheet={**FACTSHEET, "values": vals})
    assert ">NNN REIT (NNN)</span>" in html and "Owns single-tenant retail" in html
    assert ">MAA</span>" in html  # no name: falls back to the ticker, no hint


def test_missing_values_are_skipped():
    fs = {**FACTSHEET, "values": {"DGS10": "4.28%", "DGS10_CHG": "+4 bps"}}
    html = render(factsheet=fs)
    assert "10-Year Treasury" in html and "Top REIT" not in html


def test_numbers_section_replaced_and_prose_kept():
    html = render()
    assert 'id="the-numbers"' not in html  # the Markdown section is replaced by ours
    assert "10Y Treasury:" not in html  # model's list lines dropped
    assert "Floating-rate borrowers keep paying up for longer." in html
    assert html.count("2026-10-06-chart.png") == 1


def test_footer_present_no_braces_no_comments():
    html = render()
    assert FOOTER in html
    assert "Every number is pulled automatically from public data. Reviewed by the editor." in html
    assert "{{" not in html and "<!--" not in html and "template note" not in html


def test_links_and_term_panel():
    html = render()
    assert '<a href="https://example.com/a">' in html
    assert 'class="term"' in html and "Cap rate" in html


def test_problems_box_only_when_problems():
    assert "Editor notes, not for publishing" not in render()
    html = render(problems=["rates/SOFR: HTTPError"])
    assert "Editor notes, not for publishing" in html and "rates/SOFR: HTTPError" in html


def test_cover_and_edition():
    html = render()
    assert "CRE Blurb" in html and "Tuesday, October 6, 2026" in html
    assert "Daily Edition" in html


def test_stub_without_factsheet_skips_summary():
    md = f"# Pipeline error: fact sheet unavailable\n\n- factsheet: KeyError\n\n{FOOTER}\n"
    html = render_issue_html(md, None, ["factsheet: KeyError"], None)
    assert 'id="numbers"' not in html and FOOTER in html
    assert "<img" not in html


def test_no_chart_no_img():
    assert "<img" not in render(chart=None)


GOLDEN_PATH = Path(__file__).parent / "fixtures" / "preview_golden.html"


def test_defaults_match_golden():
    golden = GOLDEN_PATH.read_bytes().decode("utf-8").replace("\r\n", "\n")
    assert render_issue_html(MD, FACTSHEET, ["p1"], "chart.png") == golden


def test_site_hooks_placed():
    out = render_issue_html(MD, FACTSHEET, [], None, head_extra='<meta name="x">',
                            nav='<nav class="site-nav">N</nav>', extra_body="<section>R</section>",
                            title="T")
    assert '<title>T</title>\n<meta name="x">' in out
    assert (out.index('class="double-rule"') < out.index('class="site-nav"')
            < out.index('<main id="content"'))
    assert out.index("<section>R</section>") < out.index("</main>")


def test_render_page_has_shell_no_notes():
    out = render_page("About | CRE Blurb", "<p>Hi</p>", nav="<nav>N</nav>")
    assert "<title>About | CRE Blurb</title>" in out and "<p>Hi</p>" in out
    assert "Editor notes" not in out and '<p class="name">CRE Blurb</p>' in out
    assert 'class="ticker"' not in out  # no fact sheet: no ticker


POLISH_MD = f"""## The Brief

- Lenders are back for apartments. [Bisnow](https://www.bisnow.com/a)

## Top Stories

### Big deal closes

Two sentences here. [source](https://news.site/a)

**Why it matters:** Owners can refinance. [source](https://news.site/a)

**Coffee chat line:** Insurers are lending on apartments again. [source](https://news.site/a)

## Market Watch

### Sun Belt

- [Phoenix park approved](https://news.site/b) (Phoenix Business Journal)

### West Coast

- SF leasing picked up. [source](https://news.site/c) **Why it matters:** Owners can refinance.

{FOOTER}
"""


def test_cover_tagline_and_brief_panel():
    html = render(md=POLISH_MD)
    assert ('<span class="tagline-long">The daily commercial real estate briefing for '
            'students and professionals</span>') in html
    assert '<span class="tagline-short">Daily CRE briefing for students</span>' in html
    assert '<section class="brief"><h2 id="the-brief">The Brief</h2>' in html


def test_coffee_chat_line_is_a_pull_quote():
    html = render(md=POLISH_MD)
    assert '<section class="coffee" aria-label="Coffee chat line">' in html
    assert '<h2 class="display">Coffee chat line</h2>' in html
    assert ('<ol class="points c1" role="list"><li>Insurers are lending on apartments again. '
            '<a href="https://news.site/a">source</a></li></ol>') in html
    assert "<strong>Coffee chat line:</strong>" not in html


def test_why_it_matters_gets_lead_in_class():
    html = render(md=POLISH_MD)
    assert '<p class="why"><strong>Why it matters:</strong> Owners can refinance.' in html


def test_market_watch_bullets_become_paragraphs():
    html = render(md=POLISH_MD)
    mw = html[html.index('id="market-watch"'):html.index("</section>", html.index('id="market-watch"'))]
    assert "<li>" not in mw and "<ul>" not in mw
    assert '<p><a href="https://news.site/b">Phoenix park approved</a> (Phoenix Business Journal)</p>' in mw
    assert '<p>SF leasing picked up. <a href="https://news.site/c">source</a></p>' in mw
    assert '<p class="why"><strong>Why it matters:</strong> Owners can refinance.</p>' in mw
    assert 'class="icon"' in mw  # region icons kept


def test_brief_bullets_stay_bullets():
    html = render(md=POLISH_MD)
    assert "<li>Lenders are back for apartments." in html


def test_reading_sizes_in_css():
    html = render()
    assert ".wrap { max-width: 1180px;" in html
    assert "repeat(12, minmax(0, 1fr))" in html
    assert "fonts.googleapis.com/css2?family=Libre+Caslon+Display" in html
    assert "Source+Serif+4" in html and "Public+Sans" in html
    assert "--ink: #121417" in html and "--gold: #A9853A" in html
