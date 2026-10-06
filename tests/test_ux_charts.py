"""Charts (REIT scoreboard, yield curve, Fed odds, mortgage trend), heading icons, jump
list, issue navigation and the other UX pass changes."""

import json
import re
from datetime import date, timedelta

import pytest

from src import chart, main, site
from src.deliver import deliver
from src.draft import _sheet_for_model
from src.factsheet import build_factsheet, reit_moves
from src.models import RatePoint, ReitQuote
from src.render_html import render_issue_html, tame_urls
from src.store import connect, save_rates

PNG = b"\x89PNG\r\n\x1a\n"
D = date(2026, 10, 6)
MOVES = [{"ticker": t, "name": t, "type": ty, "chg_pct": c} for t, ty, c in [
    ("NNN", "net-lease retail", 1.9), ("PLD", "warehouse", -0.4), ("EQIX", "data center", 1.2),
    ("UDR", "apartment", -1.2), ("VICI", "casino", 0.0)]]
CURVE = [("2Y", 3.95, 4.05), ("5Y", 4.02, 4.00), ("10Y", 4.28, 4.15), ("30Y", 4.81, 4.70)]
ODDS = {"cut": "15.0%", "hold": "82.5%", "hike": "2.5%"}


def weekly(n, start=date(2026, 4, 2)):
    return [RatePoint("MORTGAGE30US", start + timedelta(weeks=i), 6.9 - i * 0.02)
            for i in range(n)]


def is_png(p, min_bytes=5000):
    return p is not None and p.read_bytes()[:8] == PNG and p.stat().st_size > min_bytes


# ------------------------------------------------------------------ chart functions

def test_each_chart_writes_a_png(tmp_path):
    assert is_png(chart.reit_scoreboard(MOVES, tmp_path / "r.png"))
    assert is_png(chart.yield_curve(CURVE, tmp_path / "c.png"))
    assert is_png(chart.fed_odds_bar(ODDS, tmp_path / "f.png"))
    assert is_png(chart.mortgage_trend(weekly(30), tmp_path / "m.png"))
    pts = [RatePoint("DGS10", date(2026, 9, 1) + timedelta(days=i), 4.3 - i * 0.01)
           for i in range(30)]
    assert is_png(chart.rate_chart(pts, tmp_path / "t.png", "10Y"))


def test_charts_are_saved_at_2x(tmp_path):
    out = chart.fed_odds_bar(ODDS, tmp_path / "f.png")
    w, h = site.png_size(out)
    assert (w, h) == tuple(2 * v for v in chart.size_px(chart.FED_FIGSIZE))


def test_skip_conditions(tmp_path):
    assert chart.reit_scoreboard(MOVES[:2], tmp_path / "r.png") is None
    bad = MOVES[:2] + [{"ticker": "X", "chg_pct": None}, {"ticker": "", "chg_pct": 1.0}]
    assert chart.reit_scoreboard(bad, tmp_path / "r.png") is None
    assert chart.yield_curve([("2Y", 4.0, None), ("10Y", 4.2, None), ("30Y", None, None)],
                             tmp_path / "c.png") is None
    assert chart.fed_odds_bar({"cut": "n/a", "hold": "n/a", "hike": None},
                              tmp_path / "f.png") is None
    assert chart.mortgage_trend(weekly(5), tmp_path / "m.png") is None
    assert not list(tmp_path.iterdir())


def test_yield_curve_without_month_ago_still_draws(tmp_path):
    curve = [(lbl, now, None) for lbl, now, _ in CURVE]
    assert is_png(chart.yield_curve(curve, tmp_path / "c.png"))
    assert "month ago" not in chart.curve_alt(curve).lower()


def test_alt_text_carries_the_values():
    assert chart.reit_alt(MOVES) == ("Bar chart of today's move for 5 REITs, best to worst: "
                                     "NNN +1.9%, EQIX +1.2%, VICI 0.0%, PLD -0.4%, UDR -1.2%.")
    assert chart.curve_alt(CURVE, date(2026, 9, 4), date(2026, 10, 5)) == (
        "Line chart of Treasury yields by maturity. On Oct 5: 2Y 3.95%, 5Y 4.02%, 10Y 4.28%, "
        "30Y 4.81%. A month ago (Sep 4): 2Y 4.05%, 5Y 4.00%, 10Y 4.15%, 30Y 4.70%.")
    assert chart.fed_alt(ODDS, "Oct 28") == ("Stacked bar of prediction-market odds for the "
                                             "Oct 28 Fed meeting: cut 15.0%, hold 82.5%, "
                                             "hike 2.5%.")
    alt = chart.mortgage_alt(weekly(30))
    assert alt.startswith("Line chart of the 30-year mortgage rate over the last six months, "
                          "from ") and alt.endswith(".")


def test_reit_labels_use_a_middle_dot_not_a_dash():
    assert chart.reit_label({"ticker": "PLD", "type": "warehouse"}) == "PLD · warehouse"
    assert chart.reit_label({"ticker": "PLD", "type": ""}) == "PLD"


# ------------------------------------------------------------------ data plumbing

def test_factsheet_reit_moves_skip_vnq_and_stay_out_of_the_model_sheet(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    quotes = [ReitQuote("VNQ", D, 89.5, 0.4), ReitQuote("PLD", D, 120.0, -0.42),
              ReitQuote("NNN", D, 41.0, 1.94)]
    sheet = build_factsheet(conn, D, None, quotes, [], None)
    assert sheet["reit_moves"] == [
        {"ticker": "NNN", "name": "NNN REIT", "type": "net-lease retail", "chg_pct": 1.94},
        {"ticker": "PLD", "name": "Prologis", "type": "warehouse", "chg_pct": -0.42}]
    assert "reit_moves" not in _sheet_for_model(sheet)
    assert reit_moves([], {}) == []


def _rates(conn, sid, start, values, step=1):
    save_rates(conn, [RatePoint(sid, start + timedelta(days=i * step), v)
                      for i, v in enumerate(values)])


def test_curve_points_today_vs_a_month_ago(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    start = D - timedelta(days=40)
    for sid, base in (("DGS2", 3.9), ("DGS5", 4.0), ("DGS10", 4.2), ("DGS30", 4.7)):
        _rates(conn, sid, start, [base + i * 0.01 for i in range(41)])
    rows, ago_date, now_date = main.curve_points(conn, D)
    assert [r[0] for r in rows] == ["2Y", "5Y", "10Y", "30Y"]
    assert rows[2][1] == pytest.approx(4.6) and rows[2][2] == pytest.approx(4.3)
    assert ago_date == D - timedelta(days=30) and now_date == D


def test_curve_points_use_the_latest_common_date(tmp_path):
    """The 10Y has one more day than the 2Y: every maturity is read on the 2Y's last day,
    never today's 10Y next to yesterday's 2Y."""
    conn = connect(str(tmp_path / "t.db"))
    start = D - timedelta(days=40)
    for sid, base, n in (("DGS2", 3.9, 40), ("DGS5", 4.0, 41), ("DGS10", 4.2, 41)):
        _rates(conn, sid, start, [base + i * 0.01 for i in range(n)])
    rows, _, now_date = main.curve_points(conn, D)
    assert now_date == D - timedelta(days=1)
    assert [r[0] for r in rows] == ["2Y", "5Y", "10Y", "30Y"]
    assert rows[2][1] == pytest.approx(4.59)  # the 10Y on the common day, not D
    assert chart.curve_alt(rows, None, now_date).startswith(
        f"Line chart of Treasury yields by maturity. On {now_date:%b} {now_date.day}: 2Y")


def test_curve_points_label_a_maturity_on_another_day(tmp_path):
    """No shared date at all: each maturity keeps its own latest and an older one is
    labeled with its date."""
    conn = connect(str(tmp_path / "t.db"))
    _rates(conn, "DGS10", D - timedelta(days=10), [4.2, 4.3], step=10)
    _rates(conn, "DGS2", D - timedelta(days=5), [3.9])
    _rates(conn, "DGS5", D - timedelta(days=3), [4.0])
    rows, _, now_date = main.curve_points(conn, D)
    assert now_date == D
    labels = [r[0] for r in rows]
    assert labels[2] == "10Y" and labels[0].startswith("2Y (") and labels[1].startswith("5Y (")


def test_yield_curve_key_names_the_date(tmp_path, monkeypatch):
    words = []
    real = chart.plt.Axes.text

    def spy(self, x, y, s, *a, **k):
        words.append(s)
        return real(self, x, y, s, *a, **k)

    monkeypatch.setattr(chart.plt.Axes, "text", spy)
    assert is_png(chart.yield_curve(CURVE, tmp_path / "c.png", date(2026, 10, 5)))
    assert "Oct 5" in words and "Today" not in words


def _factsheet(values=None, moves=MOVES):
    return {"date": D.isoformat(), "day_type": "weekday", "reit_moves": moves,
            "values": values or {"FED_CUT": "15.0%", "FED_HOLD": "82.5%",
                                 "FED_HIKE": "2.5%", "FED_MEETING": "Oct 28"}}


def test_make_extra_charts_builds_what_it_has_data_for(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    save_rates(conn, weekly(27, D - timedelta(weeks=26)))
    problems = []
    out = main._make_extra_charts(conn, D, _factsheet(), tmp_path, problems)
    assert set(out) == {"mortgage", "fed", "reits"}  # no Treasury data: no yield curve
    assert problems == []
    for meta in out.values():
        assert is_png(meta["path"], 1000) and meta["alt"] and meta["width"] and meta["height"]
    assert main._make_extra_charts(conn, D, None, tmp_path, problems) == {}


def test_a_failing_chart_is_a_problem_note_not_a_crash(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("bad font")
    monkeypatch.setattr(main.charts_mod, "fed_odds_bar", boom)
    conn = connect(str(tmp_path / "t.db"))
    problems = []
    out = main._make_extra_charts(conn, D, _factsheet(), tmp_path, problems)
    assert "fed" not in out and "reits" in out
    assert problems == ["chart/fed: RuntimeError: bad font"]


def test_deliver_copies_every_chart_and_records_alt(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    charts = {}
    for name in ("chart", "reits", "fed"):
        p = tmp_path / f"{name}.png"
        p.write_bytes(PNG + name.encode())
        charts[name] = {"path": p, "alt": f"alt {name}", "width": 800, "height": 150}
    small = tmp_path / "fed-sm.png"
    small.write_bytes(PNG + b"sm")
    charts["fed"]["sm"] = {"path": small, "width": 400, "height": 150}
    deliver(conn, D, "# Hi", [], None, tmp_path, "weekday", None, dry_run=True,
            factsheet=_factsheet(), charts=charts)
    for name in charts:
        assert (tmp_path / "issues" / "img" / f"2026-10-06-{name}.png").read_bytes() == \
            PNG + name.encode()
    data = json.loads((tmp_path / "issues" / "2026-10-06.json").read_text(encoding="utf-8"))
    assert data["charts"]["fed"] == {"alt": "alt fed", "width": 800, "height": 150,
                                     "sm": {"width": 400, "height": 150}}
    assert (tmp_path / "issues" / "img" / "2026-10-06-fed-sm.png").read_bytes() == PNG + b"sm"
    assert "reit_moves" not in data


# ------------------------------------------------------------------ page rendering

VALUES = {"DGS10": "4.28%", "DGS10_CHG": "+4 bps", "RATES_ASOF": "Oct 5",
          "FED_MEETING": "Oct 28", "FED_CUT": "15.0%", "FED_HOLD": "82.5%",
          "FED_HIKE": "2.5%", "VNQ": "$89.50", "VNQ_CHG": "+0.4%",
          "HY_OAS": "2.90%", "HY_OAS_CHG": "+3 bps", "HY_OAS_DATE": "2026-10-05"}
CHARTS = {name: {"src": f"{name}.png", "alt": f"Alt for {name} 4.28%", "width": 800,
                 "height": 300} for name in ("chart", "curve", "mortgage", "fed", "reits")}
MD = """## The Brief

- Rates rose. [S](https://x.com/1)

## The Numbers

**What it means:** Borrowing got pricier.

## Top Stories

### A deal

Text. Read it at https://news.google.com/rss/articles/CBMiqAFBVV95cUxQdDBm?oc=5 today.
Also [https://news.google.com/rss/articles/XYZ](https://news.google.com/rss/articles/XYZ).

## Market Watch

### Sun Belt

- [Dallas deal](https://x.com/d) (Bisnow)

### West Coast

- [LA deal](https://x.com/l) (Bisnow)

### International

- [London deal](https://x.com/u) (Bisnow)

## Term of the Day

**Cap rate:** Income divided by price.
"""


def page(md=MD, charts=CHARTS):
    return render_issue_html(md, {"date": D.isoformat(), "day_type": "weekday",
                                  "values": VALUES}, [], None, charts=charts)


def test_charts_sit_with_their_groups():
    html = page()
    summary = html.split('<section class="summary"')[1].split("</section>")[0]
    # Left column: Rates, its caption, the 10-Year chart, the curve + mortgage pair.
    # Right column: Federal Reserve + Fed odds, REITs + scoreboard. Then the prose.
    order = [summary.index(s) for s in (
        '<div class="num-main">', ">Rates<", "Rates as of Oct 5 close.", 'src="chart.png"',
        '<div class="chart-pair">', 'src="curve.png"', 'src="mortgage.png"',
        '<div class="num-side">', ">Federal Reserve<", 'src="fed.png"', ">REITs<",
        'src="reits.png"', 'class="takeaway"')]
    assert order == sorted(order)
    for name in CHARTS:
        assert f'alt="Alt for {name} 4.28%" width="800" height="300"' in html
    assert "Daily move for the REITs we track. Shows which property types had a good day." in html
    assert "Upward slope = longer loans cost more." in html
    assert "What prediction markets expect at the next Fed meeting." in html
    assert "30-year mortgage rate, last six months (Freddie Mac)." in html


def test_only_the_first_image_is_eager():
    imgs = re.findall(r"<img [^>]*>", page())
    assert len(imgs) == 5
    assert 'loading="lazy"' not in imgs[0]
    assert all('loading="lazy"' in i for i in imgs[1:])


def test_missing_charts_render_cleanly():
    html = page(charts={"fed": CHARTS["fed"]})
    assert 'class="chart-pair"' not in html and html.count("<img") == 1
    assert "<img" not in page(charts=None)


def test_heading_icons_are_authored_svgs():
    html = page()
    for heading in ("Sun Belt", "West Coast", "International"):
        assert re.search(r'<h3><svg class="icon"[^>]*aria-hidden="true"[^>]*>.*?</svg>'
                         + heading + "</h3>", html, re.S), heading
    # Broadsheet section labels are plain small caps: no icons on h2s.
    assert '<h2 id="term-h">Term of the Day</h2>' in html
    assert '<h2 id="dataroom-h">Data Room</h2>' in html
    week = render_issue_html("## Week Ahead\n\n- CPI\n", None, [], None)
    assert '<h2 id="week-ahead">Week Ahead</h2>' in week
    assert 'width="18" height="18"' in html


def test_jump_list_after_the_snapshot_links_to_section_ids():
    html = page()
    toc = html.split('<nav class="toc"')[1].split("</nav>")[0]
    assert (html.index('id="the-brief"') < html.index('id="snapshot-h"')
            < html.index('<nav class="toc"') < html.index('id="summary-h"'))
    links = re.findall(r'href="#([^"]+)">([^<]+)<', toc)
    assert links == [("top-stories", "Top Stories"), ("market-watch", "Market Watch"),
                     ("summary-h", "The Numbers"), ("term-h", "Term of the Day"),
                     ("dataroom-h", "Data Room")]
    for anchor, _ in links:
        assert f'id="{anchor}"' in html


def test_no_jump_list_for_short_pages():
    html = render_issue_html("## The Brief\n\n- a\n\n## Top Stories\n\nText.\n", None, [], None)
    assert 'class="toc"' not in html


def test_long_urls_never_show_as_link_text():
    html = page()
    visible = re.sub(r"<[^>]+>", " ", html.split("<main")[1])
    assert "news.google.com" not in visible
    assert ">Google News</a>" in html
    assert tame_urls("See <https://www.bisnow.com/a/b>.") == \
        "See [bisnow.com](https://www.bisnow.com/a/b)."
    kept = "[Bisnow](https://www.bisnow.com/x) and ![Chart of the Day](img/a.png)"
    assert tame_urls(kept) == kept


def test_skip_link_landmarks_and_phone_rules():
    html = page()
    assert '<a class="skip" href="#content">Skip to content</a>' in html
    assert '<header class="site-head" id="top">' in html and '<main id="content" class="wrap">' in html
    assert "min-height: 44px" in html and "prefers-reduced-motion" in html
    assert "max-width: 100%" in html


# ------------------------------------------------------------------ website

def _issue(root, day, charts=(), meta=True):
    d = root / "issues"
    (d / "img").mkdir(parents=True, exist_ok=True)
    (d / f"{day}.md").write_text(MD, encoding="utf-8")
    data = {"date": day, "day_type": "weekday", "values": VALUES}
    if meta:
        data["charts"] = {n: {"alt": f"Alt {n} on {day}", "width": 800, "height": 300}
                          for n in charts}
    (d / f"{day}.json").write_text(json.dumps(data), encoding="utf-8")
    for n in charts:
        # A real PNG header (1600 x 800) so og:image:width/height can be read.
        ihdr = b"\x00\x00\x00\x0dIHDR" + (1600).to_bytes(4, "big") + (800).to_bytes(4, "big")
        (d / "img" / f"{day}-{n}.png").write_bytes(PNG + ihdr + b"rest")


@pytest.fixture
def built(tmp_path):
    root = tmp_path / "repo"
    _issue(root, "2026-10-05", charts=("chart", "reits", "curve", "mortgage", "fed"))
    _issue(root, "2026-10-06", charts=("chart",), meta=False)  # older JSON: no "charts"
    _issue(root, "2026-10-07")
    (root / "issues" / "published.json").write_text(
        json.dumps(["2026-10-05", "2026-10-06", "2026-10-07"]), encoding="utf-8")
    out = tmp_path / "site"
    site.build(root, out)
    return out


def _read(p):
    return p.read_text(encoding="utf-8")


def test_site_copies_every_chart_next_to_its_issue(built):
    day = built / "issues" / "2026-10-05"
    for n in ("chart", "reits", "curve", "mortgage", "fed"):
        assert (day / f"{n}.png").read_bytes()[:8] == PNG
        assert f'src="{n}.png" alt="Alt {n} on 2026-10-05"' in _read(day / "index.html")
    legacy = _read(built / "issues" / "2026-10-06" / "index.html")
    assert 'src="chart.png"' in legacy and "over the last 45 days" in legacy
    assert "<img" not in _read(built / "issues" / "2026-10-07" / "index.html")


def test_og_image_has_size_and_alt(built):
    p = _read(built / "issues" / "2026-10-05" / "index.html")
    assert f'<meta property="og:image" content="{site.SITE_URL}issues/2026-10-05/chart.png">' in p
    assert '<meta property="og:image:width" content="1600">' in p
    assert '<meta property="og:image:height" content="800">' in p
    assert '<meta property="og:image:alt" content="Alt chart on 2026-10-05">' in p


def test_previous_next_and_back_to_top(built):
    mid = _read(built / "issues" / "2026-10-06" / "index.html")
    assert ('href="../../issues/2026-10-05/" rel="prev">Previous issue: Monday, '
            'October 5, 2026</a>') in mid
    assert 'href="../../issues/2026-10-07/" rel="next">Next issue: Wednesday, October 7, 2026' in mid
    assert '<a class="top" href="#top">Back to top</a>' in mid
    oldest = _read(built / "issues" / "2026-10-05" / "index.html")
    assert "Previous issue" not in oldest and "Next issue" in oldest
    home = _read(built / "index.html")
    assert "Previous issue: Tuesday" in home and "Next issue" not in home
    assert 'src="issues/2026-10-07/' not in home  # newest issue has no charts


def test_nav_marks_the_current_page(built):
    assert '<a href="./" aria-current="page">Today</a>' in _read(built / "index.html")
    assert ('<a href="../glossary/" aria-current="page">Glossary</a>'
            in _read(built / "glossary" / "index.html"))
    assert ('<a href="../archive/" aria-current="page">Archive</a>'
            in _read(built / "archive" / "index.html"))
    assert ('<a href="../about/" aria-current="page">About</a>'
            in _read(built / "about" / "index.html"))
    assert 'aria-current="page">' not in _read(built / "issues" / "2026-10-06" / "index.html")
    assert 'class="issue-list"' in _read(built / "index.html")


def test_png_size_rejects_non_png(tmp_path):
    p = tmp_path / "x.png"
    p.write_bytes(b"\x89PNG fake")
    assert site.png_size(p) is None and site.png_size(tmp_path / "missing.png") is None


# ------------------------------------------------------------------ phone variants

def test_narrow_variants_are_narrower(tmp_path):
    for name, made in (
            ("reits", chart.reit_scoreboard(MOVES, tmp_path / "r.png", narrow=True)),
            ("fed", chart.fed_odds_bar(ODDS, tmp_path / "f.png", narrow=True)),
            ("chart", chart.rate_chart(weekly(30), tmp_path / "t.png", "10Y", narrow=True))):
        assert is_png(made, 1000), name
        assert site.png_size(made)[0] == 2 * round(chart.NARROW_WIDTH * chart.BASE_DPI)


def test_extra_charts_carry_phone_variants(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    out = main._make_extra_charts(conn, D, _factsheet(), tmp_path, [])
    for name in ("fed", "reits"):
        assert is_png(out[name]["sm"]["path"], 1000)
        assert out[name]["sm"]["width"] == 400


def test_picture_source_for_phones_and_site_copies_it(tmp_path):
    charts = {"fed": {**CHARTS["fed"], "sm": {"src": "fed-sm.png", "width": 400,
                                              "height": 150}}}
    html = page(charts=charts)
    assert ('<picture><source media="(max-width: 600px)" srcset="fed-sm.png" width="400" '
            'height="150"><img src="fed.png"') in html
    root = tmp_path / "repo"
    _issue(root, "2026-10-05", charts=("fed",))
    data = json.loads((root / "issues" / "2026-10-05.json").read_text(encoding="utf-8"))
    data["charts"]["fed"]["sm"] = {"width": 400, "height": 150}
    (root / "issues" / "2026-10-05.json").write_text(json.dumps(data), encoding="utf-8")
    (root / "issues" / "img" / "2026-10-05-fed-sm.png").write_bytes(PNG + b"small")
    (root / "issues" / "published.json").write_text('["2026-10-05"]', encoding="utf-8")
    site.build(root, tmp_path / "site")
    day = tmp_path / "site" / "issues" / "2026-10-05"
    assert (day / "fed-sm.png").read_bytes() == PNG + b"small"
    assert 'srcset="fed-sm.png"' in _read(day / "index.html")


def test_text_sizes_by_chart_width():
    assert chart.text_sizes(chart.RATE_FIGSIZE) == chart.WIDE_TEXT
    assert chart.text_sizes(chart.PAIR_FIGSIZE) == chart.PAIR_TEXT
    assert chart.text_sizes(chart.RATE_NARROW) == chart.NARROW_TEXT
    assert chart.WIDE_TEXT["tick"] >= 11  # about 14.5px at the ~764px desktop width
