"""Static website builder: turns published issues into a GitHub Pages site.

Usage: python -m src.site build [--root .] [--out site]
"""
import argparse
import json
import re
import shutil
import sys
from datetime import date
from html import escape
from pathlib import Path

from src.checks import FOOTER
from src.publish import _valid_date, check, load_published, strip_banner
from src.render_html import render_issue_html, render_page

SITE_URL = "https://robertcoles04.github.io/cre-newsletter/"
SITE_NAME = "CRE Blurb"
RECENT = 10
DESC_MAX = 155

SITE_CSS = """<style>
.site-nav { display: flex; flex-wrap: wrap; gap: 4px 24px; padding: 12px 40px;
  border-bottom: 1px solid var(--hairline); font-size: 14px; }
.site-nav a { color: var(--navy); }
@media (max-width: 600px) { .site-nav { padding: 12px 16px; } }
</style>"""

ABOUT = (
    "<h1>About CRE Blurb</h1>"
    "<p>CRE Blurb is a free daily commercial real estate briefing tailored towards "
    "students and young professionals in the industry, providing fresh market data "
    "and news about the daily moves in commercial real estate.</p>"
    "<h2>Data sources</h2><ul>"
    '<li><a href="https://fred.stlouisfed.org/">FRED</a> (Federal Reserve Bank of St. Louis)</li>'
    '<li><a href="https://home.treasury.gov/">U.S. Treasury</a></li>'
    '<li><a href="https://polymarket.com/">Polymarket</a></li>'
    '<li><a href="https://www.alphavantage.co/">Alpha Vantage</a></li>'
    "<li>The news outlets linked in each story</li></ul>"
    f"<p>{escape(FOOTER)}</p>"
)

_SECTION = re.compile(r"^## +(.+?)\s*$", re.M)
_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_SENTENCE_END = re.compile(r"[.!?] ")


def _a(v: str) -> str:
    return escape(v, quote=True)


def _label(d: date) -> str:
    return f"{d:%A}, {d:%B} {d.day}, {d.year}"


def describe(md: str, day: date) -> str:
    """One-sentence summary for meta description / og:description."""
    md = md.replace("\r\n", "\n")
    parts = _SECTION.split(md)  # [pre, heading, body, heading, body, ...]
    for heading, body in zip(parts[1::2], parts[2::2]):
        if heading == "The Numbers":
            continue
        for line in body.split("\n"):
            s = line.strip()
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


def _nav(p: str) -> str:
    p = _a(p)
    return (f'<nav class="site-nav" aria-label="Site"><a href="{p}">Home</a>'
            f'<a href="{p}archive/">Archive</a><a href="{p}about/">About</a></nav>')


def _head(title: str, desc: str, url: str, og_type: str, image: str | None = None) -> str:
    tags = [f'<meta name="description" content="{_a(desc)}">',
            f'<link rel="canonical" href="{_a(url)}">',
            f'<meta property="og:title" content="{_a(title)}">',
            f'<meta property="og:description" content="{_a(desc)}">',
            f'<meta property="og:url" content="{_a(url)}">',
            f'<meta property="og:type" content="{_a(og_type)}">',
            f'<meta property="og:site_name" content="{_a(SITE_NAME)}">',
            f'<meta name="twitter:card" content="{"summary_large_image" if image else "summary"}">']
    if image:
        tags.append(f'<meta property="og:image" content="{_a(image)}">')
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
                     "factsheet": factsheet,
                     "chart": issues_dir / "img" / f"{d}-chart.png"})
    return good


def _issue_page(issue: dict, prefix: str, chart_rel: str | None, site_url: str,
                extra_body: str = "") -> str:
    d, day = issue["date"], issue["day"]
    url = f"{site_url}issues/{d}/"
    title = f"{SITE_NAME} | {day:%B} {day.day}, {day.year}"
    image = f"{url}chart.png" if chart_rel else None
    head = _head(title, describe(issue["md"], day), url, "article", image)
    return render_issue_html(issue["md"], issue["factsheet"], [], chart_rel,
                             head_extra=head, nav=_nav(prefix), extra_body=extra_body,
                             title=title)


def _link_list(issues: list[dict], prefix: str) -> str:
    items = "".join(f'<li><a href="{_a(prefix)}issues/{i["date"]}/">{_label(i["day"])}</a></li>'
                    for i in issues)
    return f"<ul>{items}</ul>"


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
                 f"{site_url}archive/", "website")
    return render_page(f"{SITE_NAME} | Archive", body, head_extra=head, nav=_nav("../"))


def build(root: Path, out: Path, site_url: str = SITE_URL) -> list[str]:
    root, out = Path(root), Path(out)
    r, o = root.resolve(), out.resolve()
    if o == r or o in r.parents:
        raise ValueError(f"refusing to delete {out}: it contains the repo root")
    issues = _load(root)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    charts = {}
    for i in issues:
        dest = out / "issues" / i["date"]
        dest.mkdir(parents=True)
        if i["chart"].is_file():
            shutil.copyfile(i["chart"], dest / "chart.png")
            charts[i["date"]] = True
        rel = "chart.png" if charts.get(i["date"]) else None
        _write(dest / "index.html", _issue_page(i, "../../", rel, site_url))

    if issues:
        newest = issues[0]
        recent = (f"<section><h2>Recent issues</h2>"
                  f"{_link_list(issues[:RECENT], '')}</section>")
        rel = f"issues/{newest['date']}/chart.png" if charts.get(newest["date"]) else None
        home = _issue_page(newest, "", rel, site_url, extra_body=recent)
    else:
        print("no dates published yet: building the placeholder home page")
        desc = "A free daily commercial real estate briefing."
        home = render_page(SITE_NAME, "<p>The first issue of CRE Blurb is coming soon.</p>",
                           head_extra=_head(SITE_NAME, desc, site_url, "website"),
                           nav=_nav(""))
    _write(out / "index.html", home)
    _write(out / "archive" / "index.html", _archive(issues, site_url))
    _write(out / "about" / "index.html", render_page(
        f"{SITE_NAME} | About", ABOUT, nav=_nav("../"),
        head_extra=_head(f"{SITE_NAME} | About", "What CRE Blurb is and where its data comes from.",
                         f"{site_url}about/", "website")))
    _write(out / "404.html", render_page(
        f"{SITE_NAME} | Page not found",
        f'<h1>Page not found</h1><p><a href="{_a(site_url)}">Home</a> &middot; '
        f'<a href="{_a(site_url)}archive/">Archive</a></p>',
        nav=_nav(site_url),
        head_extra=_head(f"{SITE_NAME} | Page not found", "Page not found.",
                         f"{site_url}404.html", "website")))
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
