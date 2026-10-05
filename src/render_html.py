"""Render an issue's Markdown + fact-sheet values as a self-contained HTML preview.

Design direction "Offering Memorandum" (see DESIGN.md). The Market Summary block is
built here from factsheet["values"], never from model text; the Markdown's own
"## The Numbers" section is replaced by it, keeping only its prose sentences.
"""

import html
import re
from datetime import date, timedelta

import markdown

from src.checks import FOOTER
from src.cleanup import tidy

NA = "n/a"

# (label, value key, change key or None). Groups are rendered in this order.
# A label may name an as-of value in braces, e.g. "30-Year Mortgage ({MORTGAGE30US_ASOF})";
# summary_label() fills it, or drops the " (...)" when that value is missing or n/a.
# A row shows when its value key or its change key is present (CMBS delinquency may
# have only a change).
SUMMARY_GROUPS: list[tuple[str, list[tuple[str, str, str | None]]]] = [
    ("Rates", [
        ("10-Year Treasury", "DGS10", "DGS10_CHG"),
        ("5-Year Treasury", "DGS5", "DGS5_CHG"),
        ("2-Year Treasury", "DGS2", "DGS2_CHG"),
        ("10Y-2Y curve", "T10Y2Y", "T10Y2Y_CHG"),
        ("SOFR", "SOFR", "SOFR_CHG"),
        ("Fed Funds", "DFF", "DFF_CHG"),
        ("30-Year Mortgage", "MORTGAGE30US", "MORTGAGE30US_CHG"),
    ]),
    ("Federal Reserve", [
        ("FOMC: next Fed meeting", "FED_MEETING", None),
        ("Odds of a cut", "FED_CUT", None),
        ("Odds of a hold", "FED_HOLD", None),
        ("Odds of a hike", "FED_HIKE", None),
    ]),
    ("REITs", [
        ("Real estate stocks (VNQ)", "VNQ", "VNQ_CHG"),
        ("Biggest gain today", "REIT_UP", None),
        ("Biggest drop today", "REIT_DOWN", None),
        ("REIT dividend yield", "VNQ_YIELD", None),
        ("REIT yield vs. 10-Year Treasury", "SPREAD_10Y", None),
    ]),
    ("Credit", [
        ("High-yield spread ({HY_OAS_ASOF})", "HY_OAS", "HY_OAS_CHG"),
        ("Bank CRE loans ({BANK_CRE_LOANS_ASOF})", "BANK_CRE_LOANS", "BANK_CRE_LOANS_CHG"),
        ("Bank CRE delinquency ({BANK_CRE_DQ_ASOF})", "BANK_CRE_DQ", "BANK_CRE_DQ_CHG"),
        ("CMBS delinquency ({CMBS_DQ_MONTH})", "CMBS_DQ", "CMBS_DQ_CHG"),
    ]),
]
SUMMARY_ROWS = [row for _, rows in SUMMARY_GROUPS for row in rows]
# Groups shown in a separate "Data Room" block after Term of the Day instead of the
# daily Market Summary: slower-moving data that rarely changes from one day to the next.
DATA_ROOM_GROUPS = {"Credit"}
DATA_ROOM_INTRO = "Slower-moving credit data. Rows marked Updated changed since the last issue."
UPDATED_DAYS = 7  # a Data Room row is "Updated" when its KEY_DATE is this recent
# Rates rows whose own as-of (KEY_ASOF, set only when it differs from RATES_ASOF) is added
# to the hint, e.g. "Base rate for floating-rate property loans (as of Oct 2)".
ROW_ASOF = {key for _, key, _ in dict(SUMMARY_GROUPS)["Rates"]}
# Short muted line under a row label, keyed by the row's value key, so every row explains
# itself. "{KEY}" pulls a value (the best/worst REIT's property type); no hint if missing.
HINTS = {
    "DGS10": "Benchmark for long-term property loans",
    "DGS5": "Benchmark for many 5-year commercial mortgages",
    "DGS2": "Tracks where the Fed is expected to set rates",
    "T10Y2Y": "Negative = short-term rates above long-term, often a slowdown signal",
    "SOFR": "Base rate for floating-rate property loans",
    "DFF": "The Fed's target for overnight bank lending; other rates key off it",
    "MORTGAGE30US": ("30-year home mortgage rate (Freddie Mac); a housing-demand gauge, "
                     "not a CRE loan rate"),
    "FED_MEETING": "When the Fed next decides on rates",
    "FED_CUT": "Chance rates go down. Prediction-market odds from Polymarket traders",
    "FED_HOLD": "Chance rates stay the same",
    "FED_HIKE": "Chance rates go up",
    "VNQ": "A fund holding about 150 REITs",
    "REIT_UP": "{REIT_UP_TYPE}",
    "REIT_DOWN": "{REIT_DOWN_TYPE}",
    "VNQ_YIELD": "Yearly income per $100 invested in VNQ",
    "SPREAD_10Y": "Below zero: Treasuries out-yield REIT dividends, so REITs look pricey vs. bonds",
    "HY_OAS": "Extra interest risky companies pay over Treasuries; higher = lenders more nervous",
    "BANK_CRE_LOANS": "Total commercial property loans banks hold",
    "BANK_CRE_DQ": "Share of banks' commercial property loans that are behind on payments",
    "CMBS_DQ": "Share of commercial property loans in bonds that are behind on payments",
}
HINT_VALUE = re.compile(r"^\{(\w+)\}$")
LABEL_ASOF = re.compile(r"\s*\(\{(\w+)\}\)")
TICKER_MOVE = {"REIT_UP", "REIT_DOWN"}  # values like "NNN +1.9%": ticker, then a move

EDITIONS = {"weekday": "Daily Edition", "friday": "Daily Edition",
            "saturday": "Weekend Edition", "sunday": "Weekend Edition"}

COMMENT = re.compile(r"<!--.*?-->", re.S)
CHART_IMG = re.compile(r"^[ \t]*!\[Chart of the Day\]\([^)\n]*\)[ \t]*$", re.M)
SECTION = re.compile(r"^## +(.+?)\s*$", re.M)
MOVE = re.compile(r"^(.*?)\s*([+-]\d[\d.,]*\s*(?:%|bps)|unch)$")

UP_SVG = ('<svg class="tri" viewBox="0 0 10 10" width="9" height="9" aria-hidden="true" '
          'focusable="false"><path d="M5 1.5 9.2 8.5H.8z" fill="currentColor"/></svg>')
DOWN_SVG = ('<svg class="tri" viewBox="0 0 10 10" width="9" height="9" aria-hidden="true" '
            'focusable="false"><path d="M.8 1.5h8.4L5 8.5z" fill="currentColor"/></svg>')
GHOST = '<span class="na" title="Data unavailable today" aria-label="Data unavailable today">n/a</span>'

FONTS = ("https://fonts.googleapis.com/css2?family=Libre+Caslon+Text:ital,wght@0,400;0,700;1,400"
         "&family=Public+Sans:ital,wght@0,400;0,600;0,700;1,400&display=swap")


def summary_label(label: str, values: dict) -> str:
    """Fill "({KEY})" in a row label from values; drop it when KEY is missing or n/a."""
    def sub(m: re.Match) -> str:
        v = values.get(m.group(1))
        return f" ({v})" if not _is_na(v) else ""
    return LABEL_ASOF.sub(sub, label)


def hint_for(key: str, values: dict) -> str:
    hint = HINTS.get(key, "")
    m = HINT_VALUE.match(hint)
    if m:
        v = values.get(m.group(1))
        hint = "" if _is_na(v) else v
    asof = values.get(f"{key}_ASOF") if key in ROW_ASOF else None
    if not _is_na(asof):
        hint = f"{hint} (as of {asof})" if hint else f"As of {asof}"
    return hint


def has_row(key: str, chg_key: str | None, values: dict) -> bool:
    return key in values or (chg_key is not None and chg_key in values)


DASHES = "[\\u2014\\u2013]"  # em dash, en dash (escaped so the source has neither)
DASH_DIGITS = re.compile(r"(?<=\d)[ \t]*" + DASHES + r"[ \t]*(?=\d)")
DASH_AFTER_PUNCT = re.compile(r"(?<=[,;:.!?])[ 	]*" + DASHES + r"+[ 	]*")
DASH_BETWEEN = re.compile(r"(?<=\S)[ \t]*" + DASHES + r"+[ \t]*(?=\S)")
DASH_EDGE = re.compile(r"[ \t]*" + DASHES + r"+[ \t]*")


LINKISH = re.compile(r"\]\([^)\n]*\)|<https?://[^>\s]+>|https?://[^\s)>\]]+")


def no_dashes(text: str) -> str:
    """Replace dashes in prose only: link targets "](...)", autolinks and bare URLs stay."""
    out, last = [], 0
    for m in LINKISH.finditer(text):
        out += [_no_dashes_prose(text[last:m.start()]), m.group(0)]
        last = m.end()
    return "".join(out + [_no_dashes_prose(text[last:])])


def _no_dashes_prose(text: str) -> str:
    """Owner style: no em/en dashes on the site. "a [em dash] b" and "a[em dash]b" become "a, b";
    a range like "2020[en dash]2021" becomes "2020-2021"; hyphens are kept."""
    text = DASH_DIGITS.sub("-", text)
    text = DASH_AFTER_PUNCT.sub(" ", text)
    text = DASH_BETWEEN.sub(", ", text)
    return DASH_EDGE.sub(" ", text)


def _slot(s: str) -> str:
    """Optional insertion on its own line; adds nothing when empty."""
    return f"\n{s}" if s else ""


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


def _row(label: str, key: str, chg_key: str | None, values: dict, *, tag: str = "",
         quiet: bool = False) -> str:
    """One summary row. `tag` adds a muted word after the label ("Updated"); `quiet`
    shows the change in muted text without an arrow (a Data Room row with no new data)."""
    label = summary_label(label, values)
    raw = values.get(key, NA)
    val, chg = raw, values.get(chg_key) if chg_key else None
    if key in TICKER_MOVE and not _is_na(raw):
        m = MOVE.match(raw.strip())
        if m:
            val, chg = m.group(1), m.group(2)
        name = values.get(f"{key}_NAME")  # "NNN REIT (NNN)" instead of the bare ticker
        if not _is_na(name):
            val = name
    change = _change(chg)
    if quiet and change:
        change = f'<span class="chg unch">{_esc(chg.strip())}</span>'
    if _is_na(val) and not _is_na(chg):  # change only (e.g. CMBS delinquency)
        cell = change
    elif _is_na(val):
        cell = GHOST
    else:
        cell = f'<span class="val">{_value(val)}</span>{change}'
    hint = hint_for(key, values)
    hint_html = f'<span class="hint">{_esc(hint)}</span>' if hint else ""
    tag_html = f' <span class="tag">{_esc(tag)}</span>' if tag else ""
    return (f'<div class="row"><dt>{_esc(label)}{tag_html}{hint_html}</dt>'
            f'<dd>{cell}</dd></div>')


def _updated(key: str, values: dict, run_date: date | None) -> bool:
    """True when the row's KEY_DATE (ISO) is within UPDATED_DAYS of the run date."""
    raw = values.get(f"{key}_DATE")
    if run_date is None or _is_na(raw):
        return False
    try:
        d = date.fromisoformat(str(raw)[:10])
    except ValueError:
        return False
    return run_date - timedelta(days=UPDATED_DAYS) <= d <= run_date


def data_room(values: dict, run_date: date | None) -> str:
    """The Data Room block (slower-moving credit rows), or "" when it has no rows."""
    cells = []
    for title, rows in SUMMARY_GROUPS:
        if title not in DATA_ROOM_GROUPS:
            continue
        for lbl, k, ck in rows:
            if not has_row(k, ck, values):
                continue
            fresh = _updated(k, values, run_date)
            cells.append(_row(lbl, k, ck, values, tag="Updated" if fresh else "",
                              quiet=not fresh))
    if not cells:
        return ""
    return ('<section class="data-room" aria-labelledby="dataroom-h">'
            '<h2 id="dataroom-h">Data Room</h2>'
            f'<p class="intro">{_esc(DATA_ROOM_INTRO)}</p><dl>{"".join(cells)}</dl></section>')


def market_summary(values: dict, chart_rel: str | None, prose_html: str) -> str:
    groups = []
    for title, rows in SUMMARY_GROUPS:
        if title in DATA_ROOM_GROUPS:
            continue
        cells = [_row(lbl, k, ck, values) for lbl, k, ck in rows if has_row(k, ck, values)]
        if cells:
            groups.append(f'<div class="group"><p class="group-name">{_esc(title)}</p>'
                          f'<dl>{"".join(cells)}</dl></div>')
    asof = values.get("RATES_ASOF")
    parts = ['<section class="summary" aria-labelledby="summary-h">',
             '<h2 id="summary-h">Market Summary</h2>', *groups]
    if not _is_na(asof):
        parts.append(f'<p class="caption">Rates as of {_esc(asof)} close.</p>')
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


WORDS_PER_MINUTE = 230
LINK_TARGET = re.compile(r"\]\([^)\n]*\)")


def read_minutes(md: str) -> int:
    """Reading time: words / 230, rounded, at least 1. Link targets are not words."""
    text = LINK_TARGET.sub("]", md)
    words = sum(1 for tok in text.split() if re.search(r"\w", tok))
    return max(1, round(words / WORDS_PER_MINUTE))


def _cover(run_date: date | None, day_type: str | None, minutes: int | None = None) -> str:
    bits = []
    if run_date:
        bits.append(f'<time datetime="{run_date.isoformat()}">'
                    f'{run_date:%A}, {run_date:%B} {run_date.day}, {run_date.year}</time>')
    if day_type in EDITIONS:
        bits.append(f'<span>{EDITIONS[day_type]}</span>')
    if minutes:
        bits.append(f'<span>{minutes} min read</span>')
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
                      chart_rel: str | None, run_date: date | None = None, *,
                      head_extra: str = "", nav: str = "", extra_body: str = "",
                      title: str | None = None) -> str:
    md = COMMENT.sub("", md)
    md = CHART_IMG.sub("", md)
    md = "\n".join(ln for ln in md.splitlines() if ln.strip() != FOOTER)
    md = md.replace("{{", "").replace("}}", "")
    md = tidy(no_dashes(md))

    values = (factsheet or {}).get("values") or {}
    if run_date is None and factsheet and factsheet.get("date"):
        run_date = date.fromisoformat(factsheet["date"])
    day_type = (factsheet or {}).get("day_type")

    pre, sections = _split(md)
    body = [_md(pre)] if pre.strip() else []
    summary_done = factsheet is None
    room = data_room(values, run_date) if factsheet is not None else ""
    brief_at = None  # index in body just after The Brief
    for heading, text in sections:
        if heading == "The Numbers" and not summary_done:
            body.append(market_summary(values, chart_rel, _numbers_prose(text)))
            summary_done = True
        elif heading == "The Numbers":
            continue
        elif heading == "Term of the Day":
            body.append(_term(text))
            if room:  # the Data Room follows Term of the Day
                body.append(room)
                room = ""
        else:
            body.append(f'<section>{_md(f"## {heading}{text}")}</section>')
            if heading == "The Brief" and brief_at is None:
                brief_at = len(body)
    if not summary_done:  # weekend issues have no Numbers section: lead with the summary
        at = brief_at if brief_at is not None else (1 if pre.strip() else 0)
        body.insert(at, market_summary(values, chart_rel, ""))
    if room:  # no Term of the Day: the Data Room goes last
        body.append(room)

    if title is None:
        title = "CRE Blurb" + (f" | {run_date:%B} {run_date.day}, {run_date.year}" if run_date else "")
    return PAGE.format(
        title=_esc(title), head_extra=_slot(head_extra), fonts=FONTS, css=CSS,
        cover=_cover(run_date, day_type, read_minutes(md)), nav=_slot(nav),
        notes=_notes(problems),
        body="\n".join(body), extra_body=_slot(extra_body), footer=_esc(FOOTER))


def render_page(title: str, body_html: str, *, head_extra: str = "", nav: str = "") -> str:
    """A non-issue page (About, Archive...) in the same shell: no dateline, no notes."""
    return PAGE.format(
        title=_esc(title), head_extra=_slot(head_extra), fonts=FONTS, css=CSS,
        cover=_cover(None, None), nav=_slot(nav), notes="", body=body_html,
        extra_body="", footer=_esc(FOOTER))


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
.sheet { max-width: 760px; margin: 0 auto; background: var(--sheet);
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
.hint { display: block; color: var(--muted); font-size: 14px; line-height: 1.35;
  margin-top: 2px; }
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
.data-room dl { margin: 0; }
.data-room .intro { color: var(--muted); font-size: 15px; }
.tag { color: var(--muted); font-size: 12px; font-weight: 600; letter-spacing: .04em;
  text-transform: uppercase; margin-left: 4px; }
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
<title>{title}</title>{head_extra}
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="{fonts}">
<style>{css}</style>
</head>
<body>
<div class="sheet">
{cover}{nav}
{notes}
<main>
{body}{extra_body}
</main>
<footer>
<p>{footer}</p>
<p>Written with AI from the linked sources. Every number is pulled automatically from public data. Edited by Robert.</p>
</footer>
</div>
</body>
</html>
"""
