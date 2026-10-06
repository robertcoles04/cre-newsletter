"""Static website builder: turns published issues into a GitHub Pages site.

Usage: python -m src.site build [--root .] [--out site]
"""
import argparse
import json
import re
import shutil
import sys
from datetime import date, datetime, time, timezone
from email.utils import format_datetime
from html import escape
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from src.publish import _valid_date, check, load_published, strip_banner
from src.render_html import CHART_NAMES, DEFAULT_ALT, render_issue_html, render_page

SITE_URL = "https://robertcoles04.github.io/cre-newsletter/"
SITE_NAME = "CRE Blurb"
RECENT = 10
RECENT_MIN = 3  # the home page's "Recent issues" list shows once there are this many
FEED_ITEMS = 20
DESC_MAX = 155
CURRENT = ' aria-current="page"'  # marks the nav link for the page being shown
REPO_ISSUES = "https://github.com/robertcoles04/cre-newsletter/issues"

SITE_CSS = """<style>
.site-nav { display: flex; flex-wrap: wrap; gap: 4px 32px; padding: 16px 48px;
  border-bottom: 1px solid var(--hairline); font-size: 16px; line-height: 1.5; }
.site-nav a { color: var(--navy); text-decoration: none; }
.site-nav a:hover { text-decoration: underline; text-decoration-thickness: 1px; }
.site-nav a[aria-current="page"] { font-weight: 600; text-decoration: underline;
  text-decoration-color: var(--gold); text-decoration-thickness: 2px; text-underline-offset: 6px; }
@media (max-width: 600px) {
  .site-nav { padding: 0 16px; gap: 0 24px; }
  .site-nav a { display: inline-flex; align-items: center; min-height: 44px; }
}
</style>"""

# The disclaimer is not repeated here: every page's footer already carries it once.
ABOUT = (
    "<h1>About CRE Blurb</h1>"
    "<p>CRE Blurb is a free daily commercial real estate briefing built for "
    "students and young professionals in the industry, providing fresh market data "
    "and news about the daily moves in commercial real estate.</p>"
    "<h2>How each issue is made</h2>"
    "<p>Each morning, a script gathers the latest commercial real estate news and public "
    "market data. Claude, an AI model, drafts the issue in plain English from those linked "
    "sources. Every number (rates, prices, odds) is filled in automatically from public "
    "data and is never typed by the AI. Robert reviews every issue before anything is "
    "published.</p>"
    "<h2>Corrections</h2>"
    f'<p>Spot an error? Open an issue at <a href="{REPO_ISSUES}">'
    "github.com/robertcoles04/cre-newsletter/issues</a> and we'll fix it.</p>"
)

_SECTION = re.compile(r"^## +(.+?)\s*$", re.M)
_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_SENTENCE_END = re.compile(r"[.!?] ")


def _a(v: str) -> str:
    return escape(v, quote=True)


def _label(d: date) -> str:
    return f"{d:%A}, {d:%B} {d.day}, {d.year}"


def describe(md: str, day: date) -> str:
    """One-sentence summary for meta description / og:description. Uses the first
    bullet of The Brief when there is one, else the first prose line after The Numbers."""
    md = md.replace("\r\n", "\n")
    parts = _SECTION.split(md)  # [pre, heading, body, heading, body, ...]
    for heading, body in zip(parts[1::2], parts[2::2]):
        if heading == "The Numbers":
            continue
        brief = heading == "The Brief"
        for line in body.split("\n"):
            s = line.strip()
            if brief and s[:2] in ("- ", "* "):
                s = s[2:].strip()
            elif brief:
                continue
            if (not s or s[0] in "#-*>!" or s[0].isdigit() or s.startswith("<!--")):
                continue
            s = _LINK.sub(r"\1", s)
            s = re.sub(r"\*+", "", s)
            s = re.sub(r"(?<!\w)_+|_+(?!\w)", "", s)
            m = _SENTENCE_END.search(s + " ")
            s = s[:m.start() + 1] if m else s
            s = " ".join(s.split())
            if len(s) > DESC_MAX:
                s = s[:DESC_MAX - 1].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"
            return s
        break
    return f"Daily commercial real estate briefing for {day:%B} {day.day}, {day.year}."


def _nav(p: str, current: str | None = None) -> str:
    """Site nav. `current` (home, archive, about) gets aria-current="page"."""
    p = _a(p)
    links = [("home", "", "Home"), ("archive", "archive/", "Archive"),
             ("about", "about/", "About")]
    return ('<nav class="site-nav" aria-label="Site">' + "".join(
        f'<a href="{p}{href}"{CURRENT if key == current else ""}>{text}</a>'
        for key, href, text in links) + "</nav>")


def png_size(path: Path) -> tuple[int, int] | None:
    """(width, height) in pixels from a PNG header, or None if it is not a real PNG."""
    try:
        head = Path(path).read_bytes()[:24]
    except OSError:
        return None
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        return None
    return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")


def _head(title: str, desc: str, url: str, og_type: str, image: str | None = None,
          site_url: str = SITE_URL, image_alt: str | None = None,
          image_size: tuple[int, int] | None = None) -> str:
    tags = [f'<meta name="description" content="{_a(desc)}">',
            f'<link rel="canonical" href="{_a(url)}">',
            f'<link rel="alternate" type="application/rss+xml" title="{_a(SITE_NAME)}" '
            f'href="{_a(site_url)}feed.xml">',
            f'<meta property="og:title" content="{_a(title)}">',
            f'<meta property="og:description" content="{_a(desc)}">',
            f'<meta property="og:url" content="{_a(url)}">',
            f'<meta property="og:type" content="{_a(og_type)}">',
            f'<meta property="og:site_name" content="{_a(SITE_NAME)}">',
            f'<meta name="twitter:card" content="{"summary_large_image" if image else "summary"}">']
    if image:
        tags.append(f'<meta property="og:image" content="{_a(image)}">')
        if image_size:
            tags += [f'<meta property="og:image:width" content="{image_size[0]}">',
                     f'<meta property="og:image:height" content="{image_size[1]}">']
        if image_alt:
            tags.append(f'<meta property="og:image:alt" content="{_a(image_alt)}">')
    return "\n" + "\n".join(tags) + "\n" + SITE_CSS


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _load(root: Path) -> list[dict]:
    """Published issues that pass the gate, newest first; bad dates are skipped."""
    issues_dir = Path(root) / "issues"
    good = []
    for d in sorted(set(load_published(root)), reverse=True):
        if not isinstance(d, str) or not _valid_date(d):
            print(f"warning: skipping {d!r}: not a valid YYYY-MM-DD date")
            continue
        md_path, json_path = issues_dir / f"{d}.md", issues_dir / f"{d}.json"
        missing = [p.name for p in (md_path, json_path) if not p.exists()]
        if missing:
            print(f"warning: skipping {d}: missing {', '.join(missing)}")
            continue
        md = strip_banner(md_path.read_bytes().decode("utf-8-sig"))
        reasons = check(md)
        if reasons:
            print(f"warning: skipping {d}: blocked: {'; '.join(reasons)}")
            continue
        factsheet = json.loads(json_path.read_bytes().decode("utf-8-sig"))
        good.append({"date": d, "day": date.fromisoformat(d), "md": md,
                     "factsheet": factsheet, "charts": _charts(issues_dir, d, factsheet)})
    return good


def _charts(issues_dir: Path, d: str, factsheet: dict) -> dict:
    """{name: {"file", "alt", "width", "height"}} for each chart PNG that exists in
    issues/img/<date>-<name>.png. Alt text and size come from the JSON's "charts"
    (issues from before it existed only have the 10-Year chart, with a generic alt)."""
    meta = factsheet.get("charts") if isinstance(factsheet.get("charts"), dict) else {}
    out = {}
    for name in CHART_NAMES:
        f = issues_dir / "img" / f"{d}-{name}.png"
        if not f.is_file() or (name != "chart" and name not in meta):
            continue
        m = meta.get(name) or {}
        out[name] = {"file": f, "alt": m.get("alt") or DEFAULT_ALT.get(name, ""),
                     "width": m.get("width"), "height": m.get("height")}
        small = issues_dir / "img" / f"{d}-{name}-sm.png"
        if isinstance(m.get("sm"), dict) and small.is_file():  # phone variant
            out[name]["sm"] = {"file": small, "width": m["sm"].get("width"),
                               "height": m["sm"].get("height")}
    return out


def _issue_page(issue: dict, prefix: str, img_base: str | None, site_url: str,
                extra_body: str = "", current: str | None = None) -> str:
    """`img_base` is where this page finds the issue's chart PNGs ("" next to the issue
    page, "issues/<date>/" from the home page), or None for no charts."""
    d, day = issue["date"], issue["day"]
    url = f"{site_url}issues/{d}/"
    title = f"{SITE_NAME} | {day:%B} {day.day}, {day.year}"
    charts = {}
    for name, c in ({} if img_base is None else issue.get("charts", {})).items():
        charts[name] = {"src": f"{img_base}{name}.png", "alt": c["alt"],
                        "width": c["width"], "height": c["height"]}
        if c.get("sm"):
            charts[name]["sm"] = {"src": f"{img_base}{name}-sm.png",
                                  "width": c["sm"]["width"], "height": c["sm"]["height"]}
    # Link previews use the 10-Year chart: its 2:1 shape suits preview cards, and the
    # REIT scoreboard's height changes with the ticker count.
    og = issue.get("charts", {}).get("chart") if charts else None
    head = _head(title, describe(issue["md"], day), url, "article",
                 f"{url}chart.png" if og else None, site_url,
                 image_alt=og["alt"] if og else None,
                 image_size=png_size(og["file"]) if og else None)
    return render_issue_html(issue["md"], issue["factsheet"], [], None, charts=charts,
                             head_extra=head, nav=_nav(prefix, current),
                             extra_body=extra_body, title=title)


def issue_nav(issues: list[dict], idx: int, prefix: str) -> str:
    """Previous (older) / Next (newer) issue links when they exist, plus Back to top.
    `issues` is newest first."""
    links = []
    if idx + 1 < len(issues):
        older = issues[idx + 1]
        links.append(f'<a href="{_a(prefix)}issues/{older["date"]}/" rel="prev">'
                     f'Previous issue: {_label(older["day"])}</a>')
    if idx > 0:
        newer = issues[idx - 1]
        links.append(f'<a href="{_a(prefix)}issues/{newer["date"]}/" rel="next">'
                     f'Next issue: {_label(newer["day"])}</a>')
    links.append('<a class="top" href="#top">Back to top</a>')
    return f'<nav class="issue-nav" aria-label="Issues">{"".join(links)}</nav>'


def _link_list(issues: list[dict], prefix: str) -> str:
    items = "".join(f'<li><a href="{_a(prefix)}issues/{i["date"]}/">{_label(i["day"])}</a></li>'
                    for i in issues)
    return f'<ul class="issue-list">{items}</ul>'



def _archive(issues: list[dict], site_url: str) -> str:
    if not issues:
        body = "<h1>Archive</h1><p>No issues yet. The first issue is coming soon.</p>"
    else:
        months: dict[str, list[dict]] = {}
        for i in issues:
            months.setdefault(f"{i['day']:%B} {i['day'].year}", []).append(i)
        body = "<h1>Archive</h1>" + "".join(
            f"<h2>{m}</h2>{_link_list(items, '../')}" for m, items in months.items())
    head = _head(f"{SITE_NAME} | Archive", "Every published issue of CRE Blurb.",
                 f"{site_url}archive/", "website", site_url=site_url)
    return render_page(f"{SITE_NAME} | Archive", body, head_extra=head,
                       nav=_nav("../", "archive"))


def feed_xml(issues: list[dict], site_url: str = SITE_URL) -> str:
    """RSS 2.0 feed of the newest FEED_ITEMS published issues (issues: newest first)."""
    items = []
    for i in issues[:FEED_ITEMS]:
        link = f"{site_url}issues/{i['date']}/"
        pub = format_datetime(datetime.combine(i["day"], time(10, 0), tzinfo=timezone.utc))
        title = f"{SITE_NAME}, {_label(i['day'])}"
        items.append(
            "<item>"
            f"<title>{xml_escape(title)}</title>"
            f"<link>{xml_escape(link)}</link>"
            f'<guid isPermaLink="true">{xml_escape(link)}</guid>'
            f"<description>{xml_escape(describe(i['md'], i['day']))}</description>"
            f"<pubDate>{pub}</pubDate>"
            "</item>")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel>'
            f"<title>{xml_escape(SITE_NAME)}</title><link>{xml_escape(site_url)}</link>"
            "<description>A free daily commercial real estate briefing for students and "
            "young professionals.</description><language>en-us</language>"
            + "".join(items) + "</channel></rss>\n")


def build(root: Path, out: Path, site_url: str = SITE_URL) -> list[str]:
    root, out = Path(root), Path(out)
    r, o = root.resolve(), out.resolve()
    if o == r or o in r.parents:
        raise ValueError(f"refusing to delete {out}: it contains the repo root")
    issues = _load(root)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    for idx, i in enumerate(issues):
        dest = out / "issues" / i["date"]
        dest.mkdir(parents=True)
        for name, c in i["charts"].items():  # every chart sits next to its issue page
            shutil.copyfile(c["file"], dest / f"{name}.png")
            if c.get("sm"):
                shutil.copyfile(c["sm"]["file"], dest / f"{name}-sm.png")
        _write(dest / "index.html", _issue_page(
            i, "../../", "", site_url, extra_body=issue_nav(issues, idx, "../../")))

    if issues:
        newest = issues[0]
        recent = (f"<section><h2>Recent issues</h2>"
                  f"{_link_list(issues[:RECENT], '')}</section>"
                  if len(issues) >= RECENT_MIN else "")
        home = _issue_page(newest, "", f"issues/{newest['date']}/", site_url,
                           extra_body=recent + issue_nav(issues, 0, ""), current="home")
    else:
        print("no dates published yet: building the placeholder home page")
        desc = "A free daily commercial real estate briefing."
        home = render_page(SITE_NAME, "<p>The first issue of CRE Blurb is coming soon.</p>",
                           head_extra=_head(SITE_NAME, desc, site_url, "website",
                                            site_url=site_url),
                           nav=_nav("", "home"))
    _write(out / "index.html", home)
    _write(out / "feed.xml", feed_xml(issues, site_url))
    _write(out / "archive" / "index.html", _archive(issues, site_url))
    _write(out / "about" / "index.html", render_page(
        f"{SITE_NAME} | About", ABOUT, nav=_nav("../", "about"),
        head_extra=_head(f"{SITE_NAME} | About", "What CRE Blurb is and how each issue is made.",
                         f"{site_url}about/", "website", site_url=site_url)))
    _write(out / "404.html", render_page(
        f"{SITE_NAME} | Page not found",
        f"<h1>Page not found</h1><p>Page not found. Today's issue is on the home page.</p>"
        f'<p><a href="{_a(site_url)}">Home</a> &middot; '
        f'<a href="{_a(site_url)}archive/">Archive</a></p>',
        nav=_nav(site_url),
        head_extra=_head(f"{SITE_NAME} | Page not found", "Page not found.",
                         f"{site_url}404.html", "website", site_url=site_url)))
    return [i["date"] for i in issues]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.site")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--root", default=".")
    b.add_argument("--out", default="site")
    args = ap.parse_args(argv)
    dates = build(Path(args.root), Path(args.out))
    print(f"built {len(dates)} issue(s) into {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
