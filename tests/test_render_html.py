from datetime import date

from src.checks import FOOTER
from src.render_html import render_issue_html

VALUES = {
    "DGS10": "4.28%", "DGS10_CHG": "+4 bps", "RATES_ASOF": "Oct 2",
    "DGS5": "3.91%", "DGS5_CHG": "-2 bps",
    "SOFR": "3.87%", "SOFR_CHG": "unch",
    "DFF": "n/a", "DFF_CHG": "n/a",
    "FED_MEETING": "Oct 28", "FED_TOP": "No change 82.5%",
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
    assert "Market Summary" in html
    for label in ("10-Year Treasury", "5-Year Treasury", "SOFR", "Fed Funds", "Next FOMC",
                  "Market odds", "VNQ REIT ETF", "Top REIT", "Bottom REIT", "VNQ yield",
                  "Spread to 10Y"):
        assert label in html
    assert "4.28%" in html and "No change 82.5%" in html and "Oct 28" in html
    assert 'class="chg up"' in html and 'class="chg down"' in html
    assert 'class="chg unch"' in html
    assert "<svg" in html
    assert "Rates as of Oct 2 close" in html


def test_na_becomes_ghost_dash():
    html = render()
    assert 'title="Data unavailable today"' in html
    assert ">n/a<" not in html


def test_missing_values_are_skipped():
    fs = {**FACTSHEET, "values": {"DGS10": "4.28%", "DGS10_CHG": "+4 bps"}}
    html = render(factsheet=fs)
    assert "10-Year Treasury" in html and "Top REIT" not in html


def test_numbers_section_replaced_and_prose_kept():
    html = render()
    assert "The Numbers" not in html
    assert "10Y Treasury:" not in html  # model's list lines dropped
    assert "Floating-rate borrowers keep paying up for longer." in html
    assert html.count("2026-10-06-chart.png") == 1


def test_footer_present_no_braces_no_comments():
    html = render()
    assert FOOTER in html
    assert "Drafted by pipeline, reviewed by Robert" in html
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
    assert "Weekday Edition" in html


def test_stub_without_factsheet_skips_summary():
    md = f"# Pipeline error: fact sheet unavailable\n\n- factsheet: KeyError\n\n{FOOTER}\n"
    html = render_issue_html(md, None, ["factsheet: KeyError"], None)
    assert "Market Summary" not in html and FOOTER in html
    assert "<img" not in html


def test_no_chart_no_img():
    assert "<img" not in render(chart=None)
