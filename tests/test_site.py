import json
import re
from datetime import date

import pytest

from src import site

VALUES = {"DGS10": "4.10%", "DGS10_CHG": "+3 bps"}

MD_05 = ("> **Review before publishing:**\r\n> - reits: failed EQR\r\n\r\n"
         "# Monday Brief\r\n\r\n## The Numbers\r\n\r\n- **10-Year Treasury:** 4.10%\r\n\r\n"
         "![Chart of the Day](img/2026-10-05-chart.png)\r\n\r\n"
         "## Top Stories\r\n\r\nRates rose on Monday. More text.\r\n\r\n"
         "For informational purposes only. Not investment advice.\r\n")
MD_06 = ("# Tuesday Brief\n\n## The Numbers\n\n- x\n\n## Top Stories\n\n"
         "Office leasing picked up. Second sentence.\n")


def _issue(root, day, md, chart=False):
    d = root / "issues"
    (d / "img").mkdir(parents=True, exist_ok=True)
    (d / f"{day}.md").write_text(md, encoding="utf-8", newline="")
    (d / f"{day}.json").write_text(json.dumps(
        {"date": day, "day_type": "weekday", "values": VALUES}), encoding="utf-8")
    if chart:
        (d / "img" / f"{day}-chart.png").write_bytes(b"\x89PNG fake")


def _published(root, days):
    (root / "issues").mkdir(parents=True, exist_ok=True)
    (root / "issues" / "published.json").write_text(json.dumps(days), encoding="utf-8")


@pytest.fixture
def built(tmp_path):
    root = tmp_path / "repo"
    _issue(root, "2026-10-05", MD_05, chart=True)
    _issue(root, "2026-10-06", MD_06)
    _published(root, ["2026-10-06", "2026-10-05"])
    out = tmp_path / "site"
    dates = site.build(root, out)
    return root, out, dates


def _read(p):
    return p.read_text(encoding="utf-8")


def test_pages_exist(built):
    _, out, dates = built
    assert dates == ["2026-10-06", "2026-10-05"]
    for rel in ["index.html", "archive/index.html", "about/index.html", "404.html",
                "issues/2026-10-05/index.html", "issues/2026-10-06/index.html"]:
        assert (out / rel).is_file(), rel


def test_home_shows_newest_date(built):
    _, out, _ = built
    home = _read(out / "index.html")
    assert "Tuesday, October 6, 2026" in home
    assert "Recent issues" not in home  # hidden until there are 3 published issues
    assert f'<link rel="canonical" href="{site.SITE_URL}issues/2026-10-06/">' in home


def test_home_newest_date_even_if_approved_first(tmp_path):
    root = tmp_path / "repo"
    _issue(root, "2026-10-05", MD_05)
    _issue(root, "2026-10-06", MD_06)
    _published(root, ["2026-10-05", "2026-10-06", "2026-10-05"])  # dup + out of order
    out = tmp_path / "site"
    assert site.build(root, out) == ["2026-10-06", "2026-10-05"]
    assert "Tuesday, October 6, 2026" in _read(out / "index.html")


def test_issue_page_clean(built):
    _, out, _ = built
    for d in ["2026-10-05", "2026-10-06"]:
        page = _read(out / "issues" / d / "index.html")
        assert "Editor notes" not in page
        assert "Review before publishing" not in page
        assert "{{" not in page
        assert ('<nav class="site-nav" aria-label="Site"><a href="../../">Today</a>'
                '<a href="../../issues/2026-10-06/#numbers">Markets</a>') in page
        assert 'property="og:type" content="article"' in page


def test_chart_copied_and_og_image(built):
    _, out, _ = built
    assert (out / "issues/2026-10-05/chart.png").read_bytes() == b"\x89PNG fake"
    page = _read(out / "issues/2026-10-05/index.html")
    assert (f'<meta property="og:image" content="{site.SITE_URL}issues/2026-10-05/chart.png">'
            in page)
    assert 'src="chart.png"' in page
    assert 'content="summary_large_image"' in page


def test_missing_chart_no_img_no_og_image(built):
    _, out, _ = built
    page = _read(out / "issues/2026-10-06/index.html")
    assert "<img" not in page
    assert "og:image" not in page
    assert 'name="twitter:card" content="summary"' in page
    assert not (out / "issues/2026-10-06/chart.png").exists()


def test_relative_links_resolve(built):
    _, out, _ = built
    pages = [p for p in out.rglob("*.html") if p.name != "404.html"]
    assert len(pages) == 6
    for page in pages:
        for href in re.findall(r'(?:href|src)="([^"]*)"', _read(page)):
            if re.match(r"[a-z]+:", href) or href.startswith("//"):
                continue
            path, _, frag = href.partition("#")
            target_page = (page.parent / path).resolve() if path else page
            if target_page.is_dir():
                target_page = target_page / "index.html"
            assert target_page.is_file(), (page, href)
            if frag:  # anchor: the id must exist on the target page
                assert f'id="{frag}"' in _read(target_page), (page, href)


def test_404_uses_absolute_links(built):
    _, out, _ = built
    page = _read(out / "404.html")
    assert "Page not found" in page
    assert f'href="{site.SITE_URL}archive/"' in page


def test_archive_and_about(built):
    _, out, _ = built
    archive = _read(out / "archive/index.html")
    assert "<h2>October 2026</h2>" in archive
    assert 'href="../issues/2026-10-06/"' in archive
    about = _read(out / "about/index.html")
    assert "students and early-career professionals" in about and "Robert Coles" in about
    assert "tailored towards" not in about
    assert about.count("For informational purposes only. Not investment advice.") == 1
    assert "Our standards" in about and "Claude" not in about and "script gathers" not in about
    assert "Corrections" in about
    assert 'href="mailto:robertjcoles@icloud.com"' in about
    assert "Data sources" not in about


def test_build_twice_idempotent(built):
    root, out, _ = built
    first = sorted(p.relative_to(out) for p in out.rglob("*"))
    (out / "stale.txt").write_text("x")
    site.build(root, out)
    assert sorted(p.relative_to(out) for p in out.rglob("*")) == first


def test_empty_published_builds_placeholder_home(tmp_path, capsys):
    out = tmp_path / "site"
    assert site.build(tmp_path, out) == []
    home = _read(out / "index.html")
    assert "coming soon" in home
    assert (out / "archive/index.html").is_file()
    assert (out / "about/index.html").is_file()
    assert (out / "404.html").is_file()


def test_blocked_and_missing_dates_skipped(tmp_path, capsys):
    root = tmp_path / "repo"
    _issue(root, "2026-10-05", MD_05)
    _issue(root, "2026-10-06", MD_06 + "\n[CHECK] verify this\n")
    (root / "issues" / "2026-10-07.md").write_text(MD_06, encoding="utf-8")  # no json
    _published(root, ["2026-10-05", "2026-10-06", "2026-10-07", "../evil"])
    out = tmp_path / "site"
    assert site.build(root, out) == ["2026-10-05"]
    printed = capsys.readouterr().out
    assert "2026-10-06" in printed and "[CHECK]" in printed
    assert "2026-10-07" in printed
    assert "../evil" in printed
    assert not (out / "issues/2026-10-06").exists()
    assert not (out / "issues/2026-10-07").exists()
    assert "2026-10-06" not in _read(out / "archive/index.html")


def test_head_values_escaped(tmp_path):
    root = tmp_path / "repo"
    _issue(root, "2026-10-05", '# T\n\n## Top Stories\n\nA "quoted" < 5 > & more.\n')
    _published(root, ["2026-10-05"])
    out = tmp_path / "site"
    site.build(root, out)
    page = _read(out / "issues/2026-10-05/index.html")
    assert 'content="A &quot;quoted&quot; &lt; 5 &gt; &amp; more."' in page


def test_describe():
    day = date(2026, 10, 5)
    md = ("# T\n\n## The Numbers\n\nSkip me.\n\n## Top Stories\n\n### Sub\n- item\n"
          "> quote\n![Chart of the Day](x.png)\n\n"
          "Rates [rose](https://x.com) **sharply** today. Second one.\n")
    assert site.describe(md, day) == "Rates rose sharply today."
    long = "## Top Stories\n\n" + " ".join(["word"] * 60) + "\n"
    d = site.describe(long, day)
    assert d.endswith("…") and len(d) <= 156 and "word…" in d
    only = "# T\n\n## The Numbers\n\nSome prose.\n"
    assert site.describe(only, day) == (
        "Daily commercial real estate briefing for October 5, 2026.")


def test_raw_html_date_skipped(tmp_path, capsys):
    root = tmp_path / "repo"
    _issue(root, "2026-10-05", MD_05)
    _issue(root, "2026-10-06", MD_06 + "\n<script>alert(1)</script>\n")
    _published(root, ["2026-10-05", "2026-10-06"])
    out = tmp_path / "site"
    assert site.build(root, out) == ["2026-10-05"]
    assert "2026-10-06" in capsys.readouterr().out
    assert not (out / "issues/2026-10-06").exists()


# --- review round: RSS, recent list, 404, footer ------------------------------

def test_recent_list_shows_from_three_issues(tmp_path):
    root = tmp_path / "repo"
    for d in ("2026-10-05", "2026-10-06", "2026-10-07"):
        _issue(root, d, MD_06)
    _published(root, ["2026-10-05", "2026-10-06", "2026-10-07"])
    out = tmp_path / "site"
    site.build(root, out)
    home = _read(out / "index.html")
    assert "Recent issues" in home
    assert 'href="issues/2026-10-05/"' in home and "Monday, October 5, 2026" in home


def test_rss_feed_parses_and_is_linked(built):
    import xml.etree.ElementTree as ET_xml
    _, out, _ = built
    feed = ET_xml.parse(out / "feed.xml").getroot()
    assert feed.tag == "rss" and feed.get("version") == "2.0"
    items = feed.findall("./channel/item")
    assert [i.findtext("title") for i in items] == [
        "CRE Blurb, Tuesday, October 6, 2026", "CRE Blurb, Monday, October 5, 2026"]
    assert items[0].findtext("link") == f"{site.SITE_URL}issues/2026-10-06/"
    assert items[0].findtext("description") == "Office leasing picked up."
    assert items[0].findtext("pubDate").startswith("Tue, 06 Oct 2026")
    for rel in ("index.html", "archive/index.html", "about/index.html", "404.html",
                "issues/2026-10-05/index.html"):
        page = _read(out / rel)
        assert (f'<link rel="alternate" type="application/rss+xml" title="CRE Blurb" '
                f'href="{site.SITE_URL}feed.xml">') in page, rel
        assert ">RSS</a>" not in page, rel  # feed stays, but no visible nav button


def test_feed_caps_at_20_items_and_escapes():
    issues = [{"date": f"2026-09-{d:02d}", "day": date(2026, 9, d),
               "md": "## Top Stories\n\nA & B <deal> closed.\n"} for d in range(30, 0, -1)]
    xml = site.feed_xml(issues)
    assert xml.count("<item>") == 20
    assert "A &amp; B &lt;deal&gt; closed." in xml


def test_404_copy(built):
    _, out, _ = built
    page = _read(out / "404.html")
    assert "<p>That page doesn't exist. Today's issue is on the home page.</p>" in page
    assert page.split('<main id="content" class="wrap">')[1].count("Page not found") == 1  # heading only
    assert '<link rel="icon" type="image/svg+xml"' in page


def test_describe_uses_the_brief_first_bullet():
    md = ("## The Brief\n\n- Office rents hit a record in Manhattan. [CO](https://x.com/1)\n"
          "- Two\n\n## The Numbers\n\n- x\n\n## Top Stories\n\nOther text.\n")
    assert site.describe(md, date(2026, 10, 5)) == "Office rents hit a record in Manhattan."
