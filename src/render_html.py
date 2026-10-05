"""Render an issue's Markdown + fact-sheet values as a self-contained HTML preview.

Design direction "Offering Memorandum" (see DESIGN.md). The Market Summary block is
built here from factsheet["values"], never from model text; the Markdown's own
"## The Numbers" section is replaced by it, keeping only its prose sentences.
"""

import html
import re
from datetime import date

import markdown

from src.checks import FOOTER

NA = "n/a"

# (label, value key, change key or None). Groups are rendered in this order.
SUMMARY_GROUPS: list[tuple[str, list[tuple[str, str, str | None]]]] = [
    ("Rates", [
        ("10-Year Treasury", "DGS10", "DGS10_CHG"),
        ("5-Year Treasury", "DGS5", "DGS5_CHG"),
        ("SOFR", "SOFR", "SOFR_CHG"),
        ("Fed Funds", "DFF", "DFF_CHG"),
    ]),
    ("Federal Reserve", [
        ("Next FOMC", "FED_MEETING", None),
        ("Market odds", "FED_TOP", None),
    ]),
    ("REITs", [
        ("VNQ REIT ETF", "VNQ", "VNQ_CHG"),
        ("Top REIT", "REIT_UP", None),
        ("Bottom REIT", "REIT_DOWN", None),
        ("VNQ yield", "VNQ_YIELD", None),
        ("Spread to 10Y", "SPREAD_10Y", None),
    ]),
]
SUMMARY_ROWS = [row for _, rows in SUMMARY_GROUPS for row in rows]
TICKER_MOVE = {"REIT_UP", "REIT_DOWN"}  # values like "NNN +1.9%": ticker, then a move

EDITIONS = {"weekday": "Weekday Edition", "friday": "Friday Edition",
            "saturday": "Saturday Edition", "sunday": "Sunday Edition"}
SOURCES = "Sources: FRED, U.S. Treasury, Polymarket, Alpha Vantage."

COMMENT = re.compile(r"<!--.*?-->", re.S)
CHART_IMG = re.compile(r"^[ \t]*!\[Chart of the Day\]\([^)\n]*\)[ \t]*$", re.M)
SECTION = re.compile(r"^## +(.+?)\s*$", re.M)
MOVE = re.compile(r"^(.*?)\s*([+-]\d[\d.,]*\s*(?:%|bps)|unch)$")

UP_SVG = ('<svg class="tri" viewBox="0 0 10 10" width="9" height="9" aria-hidden="true" '
          'focusable="false"><path d="M5 1.5 9.2 8.5H.8z" fill="currentColor"/></svg>')
DOWN_SVG = ('<svg class="tri" viewBox="0 0 10 10" width="9" height="9" aria-hidden="true" '
            'focusable="false"><path d="M.8 1.5h8.4L5 8.5z" fill="currentColor"/></svg>')
GHOST = '<span class="na" title="Data unavailable today" aria-label="Data unavailable today">&mdash;</span>'

FONTS = ("https://fonts.googleapis.com/css2?family=Libre+Caslon+Text:ital,wght@0,400;0,700;1,400"
         "&family=Public+Sans:ital,wght@0,400;0,600;0,700;1,400&display=swap")


def _esc(s: str) -> str:
    return html.escape(s, quote=True)


def _is_na(v: str | None) -> bool:
    return v is None or v.strip().lower() in ("", NA)


def _value(v: str) -> str:
    return GHOST if _is_na(v) else _esc(v)


def _change(chg: str | None) -> str:
    """A change like '+4 bps', '-1.0%' or 'unch' as a marked span. Empty when n/a."""
    if _is_na(chg):
        return ""
    c = chg.strip()
    if c.startswith("+") and re.search(r"[1-9]", c):
        return f'<span class="chg up">{UP_SVG}{_esc(c)}</span>'
    if c.startswith("-") and re.search(r"[1-9]", c):
        return f'<span class="chg down">{DOWN_SVG}{_esc(c)}</span>'
    return f'<span class="chg unch">{_esc(c)}</span>'


def _row(label: str, key: str, chg_key: str | None, values: dict) -> str:
    raw = values[key]
    val, chg = raw, values.get(chg_key) if chg_key else None
    if key in TICKER_MOVE and not _is_na(raw):
        m = MOVE.match(raw.strip())
        if m:
            val, chg = m.group(1), m.group(2)
    if _is_na(val):
        cell = GHOST
    else:
        cell = f'<span class="val">{_value(val)}</span>{_change(chg)}'
    return f'<div class="row"><dt>{_esc(label)}</dt><dd>{cell}</dd></div>'


def market_summary(values: dict, chart_rel: str | None, prose_html: str) -> str:
    groups = []
    for title, rows in SUMMARY_GROUPS:
        cells = [_row(lbl, k, ck, values) for lbl, k, ck in rows if k in values]
        if cells:
            groups.append(f'<div class="group"><p class="group-name">{_esc(title)}</p>'
                          f'<dl>{"".join(cells)}</dl></div>')
    asof = values.get("RATES_ASOF")
    when = f"Rates as of {_esc(asof)} close. " if not _is_na(asof) else ""
    parts = ['<section class="summary" aria-labelledby="summary-h">',
             '<h2 id="summary-h">Market Summary</h2>', *groups,
             f'<p class="caption">{when}{SOURCES}</p>']
    if prose_html:
        parts.append(f'<div class="takeaway">{prose_html}</div>')
    if chart_rel:
        parts.append(f'<figure class="chart"><img src="{_esc(chart_rel)}" '
                     'alt="Line chart of the 10-Year Treasury yield over the last 45 days" '
                     'width="800" height="400" loading="lazy">'
                     '<figcaption>10-Year Treasury yield, last 45 days.</figcaption></figure>')
    parts.append("</section>")
    return "\n".join(parts)


def _md(text: str) -> str:
    return markdown.markdown(text, extensions=["sane_lists"], output_format="html")


def _split(md: str) -> tuple[str, list[tuple[str, str]]]:
    """Return (preamble, [(heading, body), ...]) split on '## ' headings."""
    matches = list(SECTION.finditer(md))
    if not matches:
        return md, []
    pre = md[:matches[0].start()]
    out = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(md)
        out.append((m.group(1).strip(), md[m.end():end]))
    return pre, out


def _numbers_prose(body: str) -> str:
    keep = []
    for ln in body.splitlines():
        s = ln.strip()
        if s.startswith("**") or (s and s[0].isalnum() and not re.match(r"^\d+\.", s)):
            keep.append(s)  # prose; list lines, the italic as-of line and images are dropped
    return _md("\n\n".join(keep)) if keep else ""


def _term(body: str) -> str:
    return (f'<section class="term" aria-labelledby="term-h"><h2 id="term-h">Term of the Day</h2>'
            f'{_md(body)}</section>')


def _cover(run_date: date | None, day_type: str | None) -> str:
    bits = []
    if run_date:
        bits.append(f'<time datetime="{run_date.isoformat()}">'
                    f'{run_date:%A}, {run_date:%B} {run_date.day}, {run_date.year}</time>')
    if day_type in EDITIONS:
        bits.append(f'<span>{EDITIONS[day_type]}</span>')
    line = '<span class="sep" aria-hidden="true"></span>'.join(bits)
    return (f'<header class="cover"><h1>CRE Blurb</h1>'
            f'{f"<p class=dateline>{line}</p>" if line else ""}</header>'
            '<div class="cover-rule" role="presentation"></div>')


def _notes(problems: list[str]) -> str:
    if not problems:
        return ""
    items = "".join(f"<li>{_esc(p)}</li>" for p in problems)
    return (f'<aside class="notes" aria-labelledby="notes-h"><h2 id="notes-h">'
            f'Editor notes, not for publishing</h2><ul>{items}</ul></aside>')


def render_issue_html(md: str, factsheet: dict | None, problems: list[str],
                      chart_rel: str | None, run_date: date | None = None) -> str:
    md = COMMENT.sub("", md)
    md = CHART_IMG.sub("", md)
    md = "\n".join(ln for ln in md.splitlines() if ln.strip() != FOOTER)
    md = md.replace("{{", "").replace("}}", "")

    values = (factsheet or {}).get("values") or {}
    if run_date is None and factsheet and factsheet.get("date"):
        run_date = date.fromisoformat(factsheet["date"])
    day_type = (factsheet or {}).get("day_type")

    pre, sections = _split(md)
    body = [_md(pre)] if pre.strip() else []
    summary_done = factsheet is None
    for heading, text in sections:
        if heading == "The Numbers" and not summary_done:
            body.append(market_summary(values, chart_rel, _numbers_prose(text)))
            summary_done = True
        elif heading == "The Numbers":
            continue
        elif heading == "Term of the Day":
            body.append(_term(text))
        else:
            body.append(f'<section>{_md(f"## {heading}{text}")}</section>')
    if not summary_done:  # weekend issues have no Numbers section: lead with the summary
        body.insert(1 if pre.strip() else 0, market_summary(values, chart_rel, ""))

    title = "CRE Blurb" + (f" | {run_date:%B} {run_date.day}, {run_date.year}" if run_date else "")
    return PAGE.format(
        title=_esc(title), fonts=FONTS, css=CSS, cover=_cover(run_date, day_type),
        notes=_notes(problems), body="\n".join(body), footer=_esc(FOOTER))


CSS = """
:root {
  --ground: #EEF0F2; --sheet: #FFFFFF; --navy: #0E2A47; --navy-tint: #B9C8DA;
  --navy-wash: #F2F5F9; --navy-select: #CCD8E6; --gold: #B08D3C; --ink: #1B2430;
  --muted: #56616F; --hairline: #D9DDE3; --up: #1F7A4D; --down: #B23A3A; --ghost: #A3ABB5;
  --amber-bg: #FBF4E4; --amber-line: #E3C88A; --amber-ink: #6E4A0B;
  --serif: "Libre Caslon Text", Georgia, "Times New Roman", serif;
  --sans: "Public Sans", -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body { margin: 0; background: var(--ground); color: var(--ink); font-family: var(--sans);
  font-size: 17px; line-height: 1.6; padding: 32px 16px 48px; }
::selection { background: var(--navy-select); color: var(--ink); }
:focus-visible { outline: 2px solid var(--navy); outline-offset: 3px; border-radius: 2px; }
.sheet { max-width: 680px; margin: 0 auto; background: var(--sheet);
  box-shadow: 0 1px 2px rgba(14, 42, 71, .06), 0 8px 24px rgba(14, 42, 71, .07); }
.cover { background: var(--navy); color: #FFFFFF; padding: 44px 40px 28px; }
.cover-rule { height: 3px; background: var(--gold); }
.cover h1 { font-family: var(--serif); font-weight: 400; font-size: 3rem; line-height: 1.05;
  letter-spacing: -0.01em; margin: 0; }
.dateline { margin: 14px 0 0; color: var(--navy-tint); font-size: 15px;
  display: flex; flex-wrap: wrap; align-items: center; gap: 4px 12px; }
.dateline .sep { width: 4px; height: 4px; border-radius: 50%; background: var(--navy-tint); }
main { padding: 8px 40px 8px; }
main > *:first-child { margin-top: 32px; }
p, li { max-width: 70ch; }
p { margin: 0 0 1em; }
h2 { font-family: var(--serif); font-weight: 400; font-size: 1.6rem; line-height: 1.2;
  color: var(--navy); margin: 2.4rem 0 1rem; padding-bottom: .45rem;
  border-bottom: 1px solid var(--gold); text-wrap: balance; }
main h1 { font-family: var(--serif); font-weight: 400; font-size: 1.6rem; color: var(--navy);
  margin: 2rem 0 1rem; }
h3 { font-family: var(--sans); font-weight: 700; font-size: 1.06rem; line-height: 1.35;
  margin: 1.8rem 0 .4rem; text-wrap: balance; }
h2 + h3 { margin-top: 1.2rem; }
a { color: var(--navy); text-decoration: underline; text-decoration-thickness: 1px;
  text-underline-offset: 3px; text-decoration-color: rgba(14, 42, 71, .45); }
a:hover { text-decoration-color: var(--navy); text-decoration-thickness: 2px; }
ul, ol { padding-left: 1.2em; margin: 0 0 1em; }
li { margin: 0 0 .45em; }
li::marker { color: var(--gold); }
strong { font-weight: 700; }
.summary dl { margin: 0; }
.group + .group { margin-top: 18px; }
.group-name { font-weight: 600; font-size: 14px; color: var(--navy); margin: 0 0 2px; }
.row { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 16px;
  align-items: baseline; padding: 9px 0; border-bottom: 1px solid var(--hairline); }
.row:first-child { border-top: 1px solid var(--hairline); }
dt { color: var(--ink); }
dd { margin: 0; text-align: right; font-variant-numeric: tabular-nums lining-nums;
  white-space: nowrap; }
.val { font-weight: 600; }
.chg { display: inline-flex; align-items: center; gap: 4px; margin-left: 10px;
  font-size: 15px; min-width: 5.6em; justify-content: flex-end; }
.chg.up { color: var(--up); }
.chg.down { color: var(--down); }
.chg.unch { color: var(--muted); }
.tri { flex: none; }
.na { color: var(--ghost); cursor: help; }
.caption { color: var(--muted); font-size: 14px; margin: 12px 0 0; }
.takeaway { margin-top: 18px; }
.chart { margin: 24px 0 0; }
.chart img { display: block; width: 100%; height: auto; border: 1px solid var(--hairline); }
.chart figcaption { color: var(--muted); font-size: 14px; margin-top: 8px; }
.term { background: var(--navy-wash); padding: 4px 24px 8px; margin: 2.4rem 0 0; }
.term h2 { margin-top: 1.2rem; }
.term p:first-of-type strong { font-family: var(--serif); font-weight: 700; color: var(--navy);
  font-size: 1.12rem; }
.notes { background: var(--amber-bg); border: 1px solid var(--amber-line); color: var(--amber-ink);
  margin: 24px 40px 0; padding: 14px 20px; font-size: 15px; }
.notes h2 { font-family: var(--sans); font-weight: 700; font-size: 15px; color: var(--amber-ink);
  border: 0; margin: 0 0 6px; padding: 0; }
.notes ul { margin: 0; }
.notes li { margin: 0 0 2px; overflow-wrap: anywhere; }
.notes li::marker { color: var(--amber-ink); }
footer { margin: 40px 40px 0; padding: 18px 0 32px; border-top: 1px solid var(--hairline);
  color: var(--muted); font-size: 13px; line-height: 1.5; }
footer p { margin: 0 0 4px; }
@media (max-width: 600px) {
  body { padding: 0; font-size: 16px; }
  .sheet { box-shadow: none; }
  .cover { padding: 32px 16px 22px; }
  .cover h1 { font-size: 2.4rem; }
  main { padding: 0 16px; }
  .notes { margin: 16px 16px 0; padding: 12px 14px; }
  footer { margin: 32px 16px 0; }
  .term { padding: 2px 16px 6px; }
  h2 { font-size: 1.4rem; }
  .row { gap: 10px; }
  dd { white-space: normal; }
  .chg { margin-left: 6px; min-width: 4.8em; }
}
"""

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="{fonts}">
<style>{css}</style>
</head>
<body>
<div class="sheet">
{cover}
{notes}
<main>
{body}
</main>
<footer>
<p>{footer}</p>
<p>CRE Blurb &middot; Drafted by pipeline, reviewed by Robert</p>
</footer>
</div>
</body>
</html>
"""
