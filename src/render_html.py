"""Render an issue's Markdown + fact-sheet values as a self-contained HTML preview.

Design direction "Broadsheet" (see DESIGN.md). The Market Summary block is
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
# Color means good/bad only for prices (VNQ, REIT movers). Rates, Fed and Data Room rows
# show direction in navy: a rising rate is not simply "good", so no green/red there.
NEUTRAL_GROUPS = {"Rates", "Federal Reserve", "Credit"}
NEUTRAL_KEYS = {k for title, rows in SUMMARY_GROUPS if title in NEUTRAL_GROUPS
                for _, k, _ in rows}
# Shown as n/a (not dropped) when the odds feed is missing but the meeting date is known.
FED_ODDS = ("FED_CUT", "FED_HOLD", "FED_HIKE")
SAME_TICKER = re.compile(r"\b([A-Z][A-Z.]{0,5}) \(\1\)")  # "UDR (UDR)" -> "UDR"
# The compact Market Snapshot right after The Brief: (label, value key, change key).
SNAPSHOT_ROWS = (("10-Year Treasury", "DGS10", "DGS10_CHG"), ("SOFR", "SOFR", "SOFR_CHG"),
                 ("Real estate stocks (VNQ)", "VNQ", "VNQ_CHG"))

EDITIONS = {"weekday": "Daily Edition", "friday": "Daily Edition",
            "saturday": "Weekend Edition", "sunday": "Weekend Edition"}

COMMENT = re.compile(r"<!--.*?-->", re.S)
CHART_IMG = re.compile(r"^[ \t]*!\[Chart of the Day\]\([^)\n]*\)[ \t]*$", re.M)
SECTION = re.compile(r"^## +(.+?)\s*$", re.M)
MOVE = re.compile(r"^(.*?)\s+([+-]?\d[\d.,]*\s*(?:%|bps)|unch)$")  # "0.0%" has no sign

UP_SVG = ('<svg class="tri" viewBox="0 0 10 10" width="9" height="9" aria-hidden="true" '
          'focusable="false"><path d="M5 1.5 9.2 8.5H.8z" fill="currentColor"/></svg>')
DOWN_SVG = ('<svg class="tri" viewBox="0 0 10 10" width="9" height="9" aria-hidden="true" '
            'focusable="false"><path d="M.8 1.5h8.4L5 8.5z" fill="currentColor"/></svg>')
GHOST = '<span class="na">n/a<span class="sr-only"> (data unavailable today)</span></span>'

# The standalone 5 AM preview (issues/<date>.html, opened from a download or the repo)
# keeps the Google Fonts link: only the editor sees it and it always loads. Site pages use
# the self-hosted copies in fonts/ (SIL Open Font License), copied to site/fonts/.
FONTS = ("https://fonts.googleapis.com/css2?family=Libre+Caslon+Display"
         "&family=Source+Serif+4:ital,opsz,wght@0,8..60,400;0,8..60,600;0,8..60,700;1,8..60,400"
         "&family=Public+Sans:wght@400;500;600;700&display=swap")
GOOGLE_FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com">\n'
                '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
                f'<link rel="stylesheet" href="{FONTS}">\n')
SITE_ROOT = "https://creblurb.org/"  # where the standalone preview's footer links point
LATIN = ("U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+0304, "
         "U+0308, U+0329, U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, "
         "U+FEFF, U+FFFD")
# (family, style, weight range, file in fonts/): latin subset woff2 from Google Fonts.
# Source Serif 4 and Public Sans are variable fonts (Source Serif 4 keeps its opsz axis).
FONT_FILES = (
    ("Libre Caslon Display", "normal", "400", "libre-caslon-display-400.woff2"),
    ("Source Serif 4", "normal", "400 700", "source-serif-4-var.woff2"),
    ("Source Serif 4", "italic", "400", "source-serif-4-italic-400.woff2"),
    ("Public Sans", "normal", "400 700", "public-sans-var.woff2"),
)


def font_faces(fonts_base: str) -> str:
    """@font-face rules for the self-hosted fonts in `fonts_base` ("../../fonts/" etc.)."""
    return "".join(
        f'@font-face {{ font-family: "{fam}"; font-style: {style}; font-weight: {weight}; '
        f'font-display: swap; src: url("{fonts_base}{file}") format("woff2"); '
        f"unicode-range: {LATIN}; }}\n"
        for fam, style, weight, file in FONT_FILES)


# A quiet row of links at the bottom of every page and issue (paths under the site root).
FOOTER_LINKS = (("about/", "About"), ("privacy/", "Privacy"), ("terms/", "Terms"),
                ("accessibility/", "Accessibility"))


def footer_links(base: str) -> str:
    sep = '<span class="sep" aria-hidden="true">&middot;</span>'
    return ('<nav class="foot-links" aria-label="Site information">' + sep.join(
        f'<a href="{html.escape(base + path, quote=True)}">{text}</a>'
        for path, text in FOOTER_LINKS) + "</nav>")


def _shell(base: str | None, fonts_base: str | None) -> tuple[str, str, str]:
    """(font link tags, @font-face css, footer links) for a page. `base` reaches the site
    root from this page ("", "../", "../../" or an absolute URL); `fonts_base` defaults
    to base + "fonts/". Both None = the standalone preview: Google Fonts, absolute links."""
    if base is None and fonts_base is None:
        return GOOGLE_FONTS, "", footer_links(SITE_ROOT)
    links = footer_links(SITE_ROOT if base is None else base)
    faces = font_faces(fonts_base if fonts_base is not None else f"{base}fonts/")
    return "", faces, links


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
    if key in FED_ODDS and not _is_na(values.get("FED_MEETING")):
        return True  # meeting date known: all three odds rows show, n/a when missing
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


def _change(chg: str | None, neutral: bool = False) -> str:
    """A change like '+4 bps', '-1.0%' or 'unch' as a marked span. Empty when n/a.
    `neutral` (rates rows) keeps the triangle and sign but drops the green/red."""
    if _is_na(chg):
        return ""
    c = chg.strip()
    tone = " rate" if neutral else ""
    if c.startswith("+") and re.search(r"[1-9]", c):
        return f'<span class="chg up{tone}">{UP_SVG}{_esc(c)}</span>'
    if c.startswith("-") and re.search(r"[1-9]", c):
        return f'<span class="chg down{tone}">{DOWN_SVG}{_esc(c)}</span>'
    return f'<span class="chg unch">{_esc(c)}</span>'


def _row(label: str, key: str, chg_key: str | None, values: dict, *, tag: str = "",
         quiet: bool = False, cell_layout: bool = False) -> str:
    """One summary row. `tag` adds a muted word after the label ("Updated"); `quiet`
    shows the change in muted text without an arrow (a Data Room row with no new data).
    `cell_layout` (the Market Snapshot strip) puts the hint in its own <dd> after the
    value, so a cell reads label, big value, change, hint."""
    label = summary_label(label, values)
    raw = values.get(key, NA)
    val, chg = raw, values.get(chg_key) if chg_key else None
    if key in TICKER_MOVE and not _is_na(raw):
        m = MOVE.match(raw.strip())
        if m:
            val, chg = m.group(1), m.group(2)
        name = values.get(f"{key}_NAME")  # "NNN REIT (NNN)" instead of the bare ticker
        if not _is_na(name):
            val = SAME_TICKER.sub(r"\1", name)  # never "UDR (UDR)"
    change = _change(chg, neutral=key in NEUTRAL_KEYS)
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
    if cell_layout:
        return (f'<div class="row cell"><dt>{_esc(label)}{tag_html}</dt><dd>{cell}</dd>'
                + (f'<dd class="hint">{_esc(hint)}</dd>' if hint else "") + "</div>")
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


# Small line icons authored here as inline SVG (no emoji, no unicode glyphs, no files):
# 24-unit grid, stroke 2, drawn at 18px, so the stroke is 1.5px. Navy via currentColor.
ICON_PATHS = {
    "sun": ('<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2.5M12 19v2.5M2.5 12H5M19 12h2.5'
            'M5.3 5.3l1.8 1.8M16.9 16.9l1.8 1.8M5.3 18.7l1.8-1.8M16.9 7.1l1.8-1.8"/>'),
    "waves": ('<path d="M2 7q2.5-2.5 5 0t5 0 5 0 5 0M2 12q2.5-2.5 5 0t5 0 5 0 5 0'
              'M2 17q2.5-2.5 5 0t5 0 5 0 5 0"/>'),
    "globe": ('<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.7 3.8 5.7 3.8 9'
              's-1.3 6.3-3.8 9c-2.5-2.7-3.8-5.7-3.8-9s1.3-6.3 3.8-9z"/>'),
    "database": ('<ellipse cx="12" cy="5.5" rx="8" ry="3"/><path d="M4 5.5v13c0 1.7 3.6 3 8 3'
                 's8-1.3 8-3v-13M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/>'),
    "calendar": '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>',
    "book": ('<path d="M12 6.5C10 5 7 4.5 3 4.5v14c4 0 7 .5 9 2 2-1.5 5-2 9-2v-14'
             'c-4 0-7 .5-9 2zM12 6.5v14"/>'),
}
HEADING_ICONS = {"Sun Belt": "sun", "West Coast": "waves", "International": "globe",
                 "Week Ahead": "calendar", "Data Room": "database", "Term of the Day": "book"}


def icon(name: str) -> str:
    return ('<svg class="icon" viewBox="0 0 24 24" width="18" height="18" fill="none" '
            'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
            'stroke-linejoin="round" aria-hidden="true" focusable="false">'
            f'{ICON_PATHS[name]}</svg>')



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


# --- The ticker: a navy band of the day's numbers, filled only from the fact sheet ----------
# (short label, spoken label, value key, change key or None). Order is the band's order.
TICKER_ITEMS = (
    ("10-Yr", "10-Year", "DGS10", "DGS10_CHG"),
    ("5-Yr", "5-Year", "DGS5", "DGS5_CHG"),
    ("2-Yr", "2-Year", "DGS2", "DGS2_CHG"),
    ("SOFR", "SOFR", "SOFR", "SOFR_CHG"),
    ("Fed funds", "Fed funds", "DFF", None),
    ("30-Yr Mortgage", "30-Year mortgage", "MORTGAGE30US", "MORTGAGE30US_CHG"),
    ("REITs (VNQ)", "VNQ", "VNQ", "VNQ_CHG"),
    ("", "", "REIT_UP", None),
    ("", "", "REIT_DOWN", None),
    ("Fed hold odds", "Fed hold odds", "FED_HOLD", None),
    ("High-yield spread", "high-yield spread", "HY_OAS", None),
    ("CMBS delinquency", "CMBS delinquency", "CMBS_DQ", None),
)
PRICE_KEYS = {"VNQ", "REIT_UP", "REIT_DOWN"}  # colored up/down; every other change is neutral
TICKER_COPIES = 3  # the track holds 3 copies and slides one copy (33.333%) per loop


def _bp(text: str) -> str:
    """"1 bps" reads "1 bp"."""
    return re.sub(r"(?<![\d.])1 bps\b", "1 bp", text)


def _direction(chg: str | None) -> int:
    if _is_na(chg):
        return 0
    c = chg.strip()
    if not re.search(r"[1-9]", c):
        return 0
    return 1 if c.startswith("+") else -1 if c.startswith("-") else 0


def _tick_change(chg: str | None, price: bool) -> tuple[str, str]:
    """(html, words) for one change in the ticker; ("", "") when n/a."""
    if _is_na(chg):
        return "", ""
    c = _bp(chg.strip())
    d = _direction(c)
    mag = c.lstrip("+-")
    if d == 0:
        return f'<span class="tk-flat">{_esc(mag)}</span>', "unchanged"
    tone = ("tk-up" if d > 0 else "tk-down") if price else "tk-rate"
    svg = UP_SVG if d > 0 else DOWN_SVG
    return (f'<span class="{tone}">{svg}{_esc(mag)}</span>',
            f'{"up" if d > 0 else "down"} {mag}')


def ticker_items(values: dict) -> list[tuple[str, str]]:
    """[(html, words)] for each ticker item whose value exists and is not n/a."""
    out = []
    for short, spoken, key, chg_key in TICKER_ITEMS:
        raw = values.get(key)
        if _is_na(raw):
            continue
        if key in TICKER_MOVE:  # "UDR +1.7%": the ticker, then its colored move
            m = MOVE.match(raw.strip())
            if not m:
                continue
            chg_html, chg_words = _tick_change(m.group(2), True)
            out.append((f'<span class="tk">{_esc(m.group(1))} <b>{chg_html}</b></span>',
                        f"{m.group(1)} {chg_words}"))
            continue
        chg_html, chg_words = _tick_change(values.get(chg_key) if chg_key else None,
                                           key in PRICE_KEYS)
        val = raw.strip()
        out.append((f'<span class="tk">{_esc(short)} <b>{_esc(val)}</b>'
                    f'{" " + chg_html if chg_html else ""}</span>',
                    f"{spoken} {val}{' ' + chg_words if chg_words else ''}"))
    return out


def ticker(values: dict | None) -> str:
    """The scrolling markets band (pure CSS). The moving track is aria-hidden; the band's
    aria-label reads every value once in words. "" when there is nothing to show."""
    items = ticker_items(values or {})
    if not items:
        return ""
    asof = (values or {}).get("RATES_ASOF")
    head = "Markets" + (f" as of {asof} close" if not _is_na(asof) else "")
    label = f"{head}: " + ", ".join(words for _, words in items)
    cells = [h for h, _ in items]
    if not _is_na(asof):
        cells.insert(0, f'<span class="tk tk-date">{_esc(asof.upper())} CLOSE</span>')
    # Copies 2 and 3 only exist for the loop: under reduced motion CSS hides them
    # (class tk-copy) and the first copy wraps as plain rows.
    copy = "".join(re.sub(r'^<span class="', '<span class="tk-copy ', c, count=1) for c in cells)
    track = "".join(cells) + copy * (TICKER_COPIES - 1)
    return (f'<div class="ticker" role="img" aria-label="{_esc(label)}" '
            f'style="--n: {len(cells)}"><div class="ticker-track" aria-hidden="true">'
            f'{track}</div></div>')


PAUSE_SVG = ('<svg class="tk-icon" viewBox="0 0 10 10" width="10" height="10" '
             'aria-hidden="true" focusable="false"><path d="M2 1h2v8H2zM6 1h2v8H6z" '
             'fill="currentColor"/></svg>')
PLAY_SVG = ('<svg class="tk-icon" viewBox="0 0 10 10" width="10" height="10" '
            'aria-hidden="true" focusable="false"><path d="M2 1l7 4-7 4z" '
            'fill="currentColor"/></svg>')


def ticker_band(values: dict | None) -> str:
    """The ticker plus its Pause/Play control (WCAG 2.2.2), no JavaScript: a real,
    keyboard-focusable checkbox and its label styled as a button. When checked, CSS
    pauses the track and the label reads "Play". The control sits outside the
    aria-hidden track. "" when there is no ticker."""
    band = ticker(values)
    if not band:
        return ""
    return ('<div class="ticker-band">'
            '<input type="checkbox" id="ticker-pause" class="visually-hidden-but-focusable">'
            '<label for="ticker-pause" class="tk-pause">'
            f'<span class="tk-off">{PAUSE_SVG}Pause</span>'
            f'<span class="tk-on">{PLAY_SVG}Play</span>'
            '<span class="sr-only"> markets ticker</span></label>'
            f'{band}</div>')


# Code-generated charts (src/chart.py), keyed by name. "chart" is the 10-Year Treasury
# line (file name kept from before the other charts existed).
CHART_CAPTIONS = {
    "chart": "10-Year Treasury yield, last 45 days.",
    "curve": ("Treasury yields by maturity, today vs. a month ago. "
              "Upward slope = longer loans cost more."),
    "mortgage": "30-year mortgage rate, last six months (Freddie Mac).",
    "fed": "What prediction markets expect at the next Fed meeting.",
    "reits": "Daily move for the REITs we track. Shows which property types had a good day.",
}
CHART_NAMES = tuple(CHART_CAPTIONS)
DEFAULT_ALT = {"chart": "Line chart of the 10-Year Treasury yield over the last 45 days"}


def default_alt(name: str, values: dict | None) -> str:
    """Alt text when a chart has no recorded alt (issues from before the JSON's "charts"):
    the 10-Year chart says its latest value from the fact sheet, e.g. "...; latest 5.31%
    as of Oct 5"."""
    base = DEFAULT_ALT.get(name, "Chart")
    values = values or {}
    if name == "chart" and not _is_na(values.get("DGS10")):
        asof = values.get("DGS10_ASOF")
        asof = asof if not _is_na(asof) else values.get("RATES_ASOF")
        return (f"{base}; latest {values['DGS10'].strip()}"
                + (f" as of {asof.strip()}" if not _is_na(asof) else ""))
    return base
# Charts that follow a Market Summary group, in order.
GROUP_CHARTS = {"Rates": ("curve", "mortgage"), "Federal Reserve": ("fed",),
                "REITs": ("reits",)}
SUMMARY_SIDE = {"Federal Reserve", "REITs"}  # groups in the right-hand column on desktop
NUMBERS_INTRO = "Every number is filled in automatically from public data."


def as_charts(charts) -> dict:
    """Normalize the chart argument: a dict {name: {"src", "alt", "width", "height"}},
    or a plain string (the 10-Year chart's src, the older call style), or None."""
    if not charts:
        return {}
    if isinstance(charts, str):
        return {"chart": {"src": charts}}
    return dict(charts)


def figure(name: str, charts: dict, values: dict | None = None) -> str:
    """<figure> for one chart, or "" when it is missing. Images are lazy; the first image
    on the page is made eager by render_issue_html."""
    c = charts.get(name) or {}
    if not c.get("src"):
        return ""
    w, h = c.get("width") or 800, c.get("height") or 400
    alt = c.get("alt") or default_alt(name, values)
    img = (f'<img src="{_esc(c["src"])}" alt="{_esc(alt)}" width="{int(w)}" '
           f'height="{int(h)}" loading="lazy">')
    sm = c.get("sm") or {}
    if sm.get("src"):  # phone variant: a narrower chart whose text stays readable
        img = (f'<picture><source media="(max-width: 600px)" srcset="{_esc(sm["src"])}" '
               f'width="{int(sm.get("width") or w)}" height="{int(sm.get("height") or h)}">'
               f'{img}</picture>')
    return (f'<figure class="chart chart-{name}">{img}'
            f'<figcaption>{_esc(CHART_CAPTIONS[name])}</figcaption></figure>')


def market_summary(values: dict, charts, prose_html: str) -> str:
    """The Numbers (id "numbers"): the full Market Summary. Desktop: Rates, its caption and
    the rate charts on the left; Federal Reserve and REITs (each with its chart) on the
    right; then the prose ("What it means", REIT movers) across the full width."""
    charts = as_charts(charts)
    asof = values.get("RATES_ASOF")
    main, side = [], []
    for title, rows in SUMMARY_GROUPS:
        if title in DATA_ROOM_GROUPS:
            continue
        col = side if title in SUMMARY_SIDE else main
        cells = [_row(lbl, k, ck, values) for lbl, k, ck in rows if has_row(k, ck, values)]
        if cells:
            col.append(f'<div class="group"><h3 class="group-name">{_esc(title)}</h3>'
                       f'<dl>{"".join(cells)}</dl></div>')
        if title == "Rates" and not _is_na(asof):
            col.append(f'<p class="caption">Rates as of {_esc(asof)} close.</p>')
        if title == "Rates":
            col.append(figure("chart", charts, values))
        figs = [f for f in (figure(n, charts, values) for n in GROUP_CHARTS.get(title, ()))
                if f]
        if title == "Rates" and figs:  # yield curve + mortgage: 2-up when there is room
            col.append(f'<div class="chart-pair">{"".join(figs)}</div>')
        else:
            col += figs
    main, side = [x for x in main if x], [x for x in side if x]
    cols = "".join(f'<div class="{cls}">{"".join(parts)}</div>'
                   for cls, parts in (("num-main", main), ("num-side", side)) if parts)
    parts = ['<section class="summary" id="numbers" aria-labelledby="summary-h">',
             '<h2 id="summary-h" class="display">The Numbers</h2>',
             f'<p class="intro">{NUMBERS_INTRO}</p>']
    if cols:
        parts.append(f'<div class="num-cols">{cols}</div>')
    if prose_html:
        parts.append(f'<div class="takeaway">{prose_html}</div>')
    parts.append("</section>")
    return "\n".join(parts)


def market_snapshot(values: dict) -> str:
    """The Market Snapshot: a full-width strip under the front row with three cells
    (10-Year, SOFR, VNQ: label, big value, change, hint), plus a link down to The Numbers.
    Phones show the cells as rows. "" when none of them has data."""
    if not any(not _is_na(values.get(k)) for _, k, _ in SNAPSHOT_ROWS):
        return ""
    cells = "".join(_row(lbl, k, ck, values, cell_layout=True) for lbl, k, ck in SNAPSHOT_ROWS)
    asof = values.get("RATES_ASOF")
    when = f"Rates as of {_esc(asof)} close. " if not _is_na(asof) else ""
    return ('<section class="snapshot" aria-labelledby="snapshot-h">'
            '<h2 id="snapshot-h">Market Snapshot</h2>'
            f'<dl>{cells}</dl><p class="caption">{when}'
            '<a href="#numbers">Full market data</a></p></section>')


def _md(text: str) -> str:
    return markdown.markdown(text, extensions=["sane_lists"], output_format="html")


def _inline(text: str) -> str:
    """Markdown for one line, without the wrapping <p>."""
    out = _md(text.strip()).strip()
    m = re.fullmatch(r"<p>(.*)</p>", out, re.S)
    return m.group(1) if m and "<p>" not in m.group(1) else out


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


TERM_HEAD = re.compile(r"^\s*\*\*([^*\n]+?):?\*\*:?[ \t]*")


def _cap(text: str) -> str:
    """Capitalize the first letter ("when a lender..." -> "When a lender...")."""
    i = next((n for n, ch in enumerate(text) if ch.isalpha()), None)
    return text if i is None else text[:i] + text[i].upper() + text[i + 1:]


def split_term(body: str) -> tuple[str | None, str]:
    """"**Cap rate:** Net operating income..." -> ("Cap rate", "Net operating income...").
    (None, body) when the section does not open with a bold term."""
    body = body.strip()
    m = TERM_HEAD.match(body)
    if not m:
        return None, body
    return m.group(1).strip().rstrip(":").strip(), _cap(body[m.end():])


def _term(body: str) -> str:
    """Term of the Day: the label, the term in the display serif, then its definition."""
    name, rest = split_term(body)
    name_html = f'<p class="term-name">{_inline(name)}</p>' if name else ""
    return (f'<section class="term" aria-labelledby="term-h">'
            f'<h2 id="term-h">Term of the Day</h2>{name_html}'
            f'<div class="term-def">{_md(rest)}</div></section>')


WORDS_PER_MINUTE = 230
LINK_TARGET = re.compile(r"\]\([^)\n]*\)")


def read_minutes(text: str) -> int:
    """Reading time from the rendered page body: HTML tags stripped (so tables, hints and
    captions count the same everywhere), words / 230, rounded half up, at least 1.
    Markdown link targets are not words either."""
    text = html.unescape(TAGS.sub(" ", LINK_TARGET.sub("]", text)))
    words = sum(1 for tok in text.split() if re.search(r"\w", tok))
    return max(1, int(words / WORDS_PER_MINUTE + 0.5))


TAGLINE = "The daily commercial real estate briefing for students and young professionals"
TAGLINE_SHORT = "Daily CRE briefing for students"
EDITION_NOTE = "Free. New issue every weekday morning, lighter on weekends."


def _date_text(d: date) -> str:
    return f"{d:%A}, {d:%B} {d.day}, {d.year}"


def masthead(run_date: date | None, day_type: str | None, minutes: int | None = None,
             values: dict | None = None, nav: str = "", h1: bool = False) -> str:
    """Every page's masthead: the utility row (date, edition, the "Free..." note), the
    navy ticker, "CRE Blurb" with its tagline, the thick-and-thin rule and the nav.
    Phones swap the utility row for a one-line dateline above the name."""
    edition = EDITIONS.get(day_type or "")
    when = (f'<time datetime="{run_date.isoformat()}">{_date_text(run_date)}</time>'
            if run_date else "")
    ed = edition or ""
    if ed and minutes:
        ed = f"{ed} · {minutes} min read"
    util = "".join(x for x in (
        f"<span>{when}</span>" if when else "",
        f'<span class="edition">{ed}</span>' if ed else "",
        f"<span>{EDITION_NOTE}</span>") if x)
    short = " · ".join(x for x in (_date_text(run_date) if run_date else "", edition or "") if x)
    dateline = f'<p class="dateline-m">{short}</p>' if short else ""
    # Issue pages (and the home page) use the name as their h1, with the full date for
    # screen readers; other pages keep a <p> and have their own h1.
    if h1:
        when_sr = f'<span class="sr-only">: {_date_text(run_date)}</span>' if run_date else ""
        name = f'<h1 class="name">CRE Blurb{when_sr}</h1>'
    else:
        name = '<p class="name">CRE Blurb</p>'
    return ('<header class="site-head" id="top">'
            f'<div class="utility"><div class="wrap">{util}</div></div>'
            f'{ticker_band(values)}'
            f'<div class="masthead wrap">{dateline}{name}'
            f'<p class="tagline"><span class="tagline-long">{TAGLINE}</span>'
            f'<span class="tagline-short">{TAGLINE_SHORT}</span></p>'
            '<div class="double-rule" role="presentation"></div>'
            f'{nav}</div></header>')


def _notes(problems: list[str]) -> str:
    if not problems:
        return ""
    items = "".join(f"<li>{_esc(p)}</li>" for p in problems)
    return (f'<aside class="notes" aria-labelledby="notes-h"><h2 id="notes-h">'
            f'Editor notes, not for publishing</h2><ul>{items}</ul></aside>')


# Visible link text must never be a raw URL (long Google News links): autolinks, bare
# URLs and [url](url) links get a short source label instead.
URL_TEXT_LINK = re.compile(r"\[(https?://[^\]\s]+)\]\(")
AUTOLINK = re.compile(r"<(https?://[^>\s]+)>")
BARE_URL = re.compile(r"(?<![(<\"'=])\bhttps?://[^\s)<>\]]+")
LINK_PART = re.compile(r"\[[^\]\n]*\]\([^)\n]*\)")
HOST_LABELS = {"news.google.com": "Google News"}


def url_label(url: str) -> str:
    """"https://www.bisnow.com/x" -> "bisnow.com"; Google News links -> "Google News"."""
    host = re.sub(r"^https?://", "", url, flags=re.I).split("/", 1)[0].split(":", 1)[0].lower()
    host = host[4:] if host.startswith("www.") else host
    return HOST_LABELS.get(host, host or "link")


def tame_urls(md: str) -> str:
    md = URL_TEXT_LINK.sub(lambda m: f"[{url_label(m.group(1))}](", md)
    md = AUTOLINK.sub(lambda m: f"[{url_label(m.group(1))}]({m.group(1)})", md)
    out, last = [], 0
    for m in LINK_PART.finditer(md):  # leave existing [text](target) links alone
        out += [BARE_URL.sub(lambda u: f"[{url_label(u.group(0))}]({u.group(0)})",
                             md[last:m.start()]), m.group(0)]
        last = m.end()
    out.append(BARE_URL.sub(lambda u: f"[{url_label(u.group(0))}]({u.group(0)})", md[last:]))
    return "".join(out)


H2_PLAIN = re.compile(r"<h2>(.*?)</h2>", re.S)
H3_PLAIN = re.compile(r"<h3>(.*?)</h3>", re.S)
TAGS = re.compile(r"<[^>]+>")
TOC_MIN = 3


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", html.unescape(TAGS.sub("", text)).lower()).strip("-")
    return s or "section"


def _unique(base: str, used: set[str]) -> str:
    sid, n = base, 2
    while sid in used:
        sid, n = f"{base}-{n}", n + 1
    used.add(sid)
    return sid


def _decorate(section_html: str, used: set[str], icons_h3: bool = False) -> str:
    """Give markdown h2s an id (jump-list anchor); Market Watch h3s get their region icon."""
    def h2(m: re.Match) -> str:
        return f'<h2 id="{_unique(slug(m.group(1)), used)}">{m.group(1)}</h2>'

    def h3(m: re.Match) -> str:
        name = HEADING_ICONS.get(html.unescape(TAGS.sub("", m.group(1))).strip())
        return f"<h3>{icon(name)}{m.group(1)}</h3>" if name else m.group(0)

    out = H2_PLAIN.sub(h2, section_html)
    return H3_PLAIN.sub(h3, out) if icons_h3 else out


def jump_list(links: list[tuple[str, str]]) -> str:
    """"In this issue": plain links to each section after The Brief and the Snapshot, in
    page order. "" below TOC_MIN links. Shown on phones and tablets only (CSS)."""
    if len(links) < TOC_MIN:
        return ""
    items = "".join(f'<li><a href="#{_esc(sid)}">{_esc(text)}</a></li>' for sid, text in links)
    return ('<nav class="toc" aria-labelledby="toc-h"><p class="toc-title" id="toc-h">'
            f'In this issue</p><ul role="list">{items}</ul></nav>')


COFFEE = re.compile(r"<p><strong>Coffee chat line:</strong>\s*(.*?)</p>", re.S)
WHY_LEAD = "<strong>Why it matters:</strong>"
WHY = re.compile(r"<p>" + re.escape(WHY_LEAD))
LIST = re.compile(r"<ul>\s*(.*?)\s*</ul>", re.S)
LIST_ITEM = re.compile(r"<li>(.*?)</li>", re.S)
INNER_P = re.compile(r"^\s*<p>(.*)</p>\s*$", re.S)

COFFEE_POINTS = re.compile(
    r"<p><strong>Coffee chat talking points:</strong>\s*</p>\s*<ul>\s*(.*?)\s*</ul>", re.S)
COFFEE_LABEL_MD = re.compile(r"^(\*\*Coffee chat talking points:\*\*)[ \t]*\n(?=[ \t]*[-*+] )",
                             re.M)
COFFEE_POINTS_MD = re.compile(
    r"^\*\*Coffee chat talking points:\*\*[ \t]*\n+((?:[ \t]*[-*+][ \t].*(?:\n|$))+)", re.M)
COFFEE_LINE_MD = re.compile(r"^\*\*Coffee chat line:\*\*[ \t]*(.+?)[ \t]*$", re.M)
POINTS_NOTE = "Talking points you can use in networking conversations"
LINE_NOTE = "A one-line take you can use in networking conversations"


def coffee_band(label: str, note: str, items: list[str]) -> str:
    """The Coffee chat band: title and note on the left, the numbered serif italic points
    on the right (one column per point, up to 3)."""
    items = [i for i in items if i.strip()]
    if not items:
        return ""
    lis = "".join(f"<li>{i}</li>" for i in items)
    return (f'<section class="coffee" aria-label="{_esc(label)}">'
            f'<div class="coffee-head"><h2 class="display">{_esc(label)}</h2>'
            f'<p class="coffee-note">{_esc(note)}</p></div>'
            f'<ol class="points c{min(len(items), 3)}" role="list">{lis}</ol></section>')


def _coffee_from_md(text: str) -> tuple[str, str]:
    """Pull the Coffee chat block out of a section's Markdown: (rest, band html)."""
    m = COFFEE_POINTS_MD.search(text)
    if m:
        items = [_inline(re.sub(r"^[ \t]*[-*+][ \t]+", "", ln))
                 for ln in m.group(1).splitlines() if re.sub(r"[-*+\s]", "", ln)]
        return (text[:m.start()] + text[m.end():],
                coffee_band("Coffee chat talking points", POINTS_NOTE, items))
    m = COFFEE_LINE_MD.search(text)
    if m:
        return (text[:m.start()] + text[m.end():],
                coffee_band("Coffee chat line", LINE_NOTE, [_inline(m.group(1))]))
    return text, ""


def _pull_quote(m: re.Match) -> str:
    """A Coffee chat line outside Top Stories: the same band."""
    return coffee_band("Coffee chat line", LINE_NOTE, [m.group(1).strip()])


def _talking_points(m: re.Match) -> str:
    items = [INNER_P.sub(r"\1", i).strip() for i in LIST_ITEM.findall(m.group(1))]
    return coffee_band("Coffee chat talking points", POINTS_NOTE, items)


def _list_to_paragraphs(m: re.Match) -> str:
    """Market Watch reads like Top Stories: a bullet (often a lone link) becomes a paragraph."""
    items = LIST_ITEM.findall(m.group(1))
    if not items or "<ul>" in m.group(1) or "<ol>" in m.group(1):
        return m.group(0)
    out = []
    for item in items:
        inner = INNER_P.match(item)
        text = inner.group(1).strip() if inner else item.strip()
        # "summary [source](url) Why it matters: ..." in one bullet: two paragraphs.
        head, sep, tail = text.partition(WHY_LEAD)
        if sep and head.strip():
            out += [f"<p>{head.strip()}</p>", f"<p>{WHY_LEAD}{tail}</p>"]
        else:
            out.append(f"<p>{text}</p>")
    return "\n".join(out)


# Section heading -> section class. Anything else is a full-width "wide" section.
SECTION_CLASS = {"The Brief": "brief", "Debt Markets": "sec debt", "Market Watch": "sec watch",
                 "Quick Hits": "sec quick-hits", "Top Stories": "sec top-stories"}


def style_section(section_html: str, heading: str) -> str:
    """Section-level touches: the Coffee chat band, muted "Why it matters:" lead-ins,
    Market Watch bullets as paragraphs, The Brief as a numbered list, and a body wrapper
    (two newspaper columns on desktop) for the full-width sections."""
    if heading == "Market Watch":
        section_html = LIST.sub(_list_to_paragraphs, section_html)
    section_html = COFFEE.sub(_pull_quote, section_html)
    section_html = COFFEE_POINTS.sub(_talking_points, section_html)
    section_html = WHY.sub(f'<p class="why">{WHY_LEAD}', section_html)
    if heading == "The Brief":
        section_html = section_html.replace("<ul>", '<ol role="list">').replace("</ul>", "</ol>")
    elif heading == "Quick Hits":  # list-style: none, so keep the list role explicit
        section_html = section_html.replace("<ul>", '<ul role="list">')
    cls = SECTION_CLASS.get(heading, "sec wide")
    if cls.startswith("sec") and "</h2>" in section_html:
        head, _, body = section_html.partition("</h2>")
        section_html = f'{head}</h2><div class="sec-body">{body}</div>'
    return f'<section class="{cls}">{section_html}</section>'


# --- Top Stories: the lead story and the story grid -------------------------------------
H3_MD = re.compile(r"^### +(.+?)\s*$", re.M)
MD_LINK = re.compile(r"\[([^\]\n]+)\]\(([^)\s]+)\)")
TRAIL_LINK = re.compile(r"\[([^\]\n]+)\]\(([^)\s]+)\)\s*$")
WHY_MD = re.compile(r"\*\*Why it matters:\*\*[ \t]*")


def _story(title_md: str, body: str, lead: bool = False) -> str:
    """One Top Story from its "### " heading and Markdown body: headline (linked to its
    source), summary, "Why it matters" and a "Source:" line when the source link stands
    at the end. Nothing is invented: no kicker, no image."""
    body = body.strip()
    source = None
    m = TRAIL_LINK.search(body)
    if m:
        before = body[:m.start()].rstrip(" \t")
        if not before or before.endswith(("\n", ".", "!", "?", ")", '"', "”")):
            source, body = (m.group(1), m.group(2)), before.strip()
    title_link = MD_LINK.search(title_md)
    links = MD_LINK.findall(body)
    href = (title_link.group(2) if title_link else source[1] if source
            else links[0][1] if links else None)
    hl = _inline(MD_LINK.sub(r"\1", title_md))
    if href:
        hl = f'<a href="{_esc(href)}">{hl}</a>'
    parts = WHY_MD.split(body, maxsplit=1)
    summary, why = parts[0].strip(), (parts[1].strip() if len(parts) > 1 else "")
    summary_html = _md(summary) if summary else ""
    story_sr = f'<span class="sr-only">: {TAGS.sub("", hl)}</span>'
    src_html = (f'<p class="source">Source: <a href="{_esc(source[1])}">'
                f'{_inline(source[0])}{story_sr}</a></p>' if source else "")
    if lead:
        why_html = (_md(_cap(why)).replace("<p>", '<p><strong class="why-label">Why it matters'
                                           "</strong> ", 1) if why else "")
        why_html = f'<div class="why-panel">{why_html}</div>' if why_html else ""
        deck = f'<div class="deck">{summary_html}</div>' if summary_html else ""
        return ('<article class="lead" id="top-stories" aria-labelledby="lead-h">'
                '<p class="kicker">Top Story</p>'
                f'<h2 class="hl" id="lead-h">{hl}</h2>{deck}{src_html}{why_html}</article>')
    why_html = _md(why).replace("<p>", f'<p class="why">{WHY_LEAD} ', 1) if why else ""
    return f'<article class="story"><h3 class="hl">{hl}</h3>{summary_html}{why_html}{src_html}</article>'


def top_stories(text: str, used: set[str]) -> tuple[str, str, str]:
    """(lead html, rest-of-stories section html, coffee band html) from the Top Stories
    Markdown. Without any "### " story it falls back to one plain section."""
    text, coffee = _coffee_from_md(text)
    heads = list(H3_MD.finditer(text))
    if not heads:
        if not text.strip():
            return "", "", coffee
        sec = style_section(_decorate(_md(f"## Top Stories\n{text}"), used), "Top Stories")
        return "", sec, coffee
    intro = text[:heads[0].start()].strip()
    stories = []
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        stories.append((m.group(1), text[m.end():end]))
    lead = _story(*stories[0], lead=True)
    used.update({"top-stories", "lead-h"})
    rest = [_story(t, b) for t, b in stories[1:]]
    if not rest and not intro:
        return lead, "", coffee
    n = len(rest)
    cols = 1 if n == 1 else 2 if n in (2, 4) else 3
    sid = _unique("more-stories", used)
    grid = f'<div class="stories c{cols}">{"".join(rest)}</div>' if rest else ""
    sec = (f'<section class="sec top-stories" aria-labelledby="{sid}">'
           f'<h2 id="{sid}">{"More top stories" if not rest else "Top Stories"}</h2>'
           f'{_md(intro) if intro else ""}{grid}</section>')
    return lead, sec, coffee


PAIR_RATIO = 2.0  # stack a pair when one side has this many times the other's words


def _words(fragment: str) -> int:
    return len(html.unescape(TAGS.sub(" ", fragment)).split())


def _solo(section: str) -> str:
    """Mark a section as full width on its own (its body flows in two columns)."""
    return re.sub(r'^<section class="([^"]*)"', r'<section class="\1 solo"', section, count=1)


def _pair(a: str, b: str, cls: str = "") -> str:
    """Two sections side by side on desktop (7 + 5 columns). When one is missing the
    other spans the full width, so the grid never shows an empty cell. When one side is
    much longer (PAIR_RATIO), both go full width, one after the other, so a short column
    never leaves a tall blank area beside the long one."""
    if not a and not b:
        return ""
    if a and b:
        wa, wb = _words(a), _words(b)
        if max(wa, wb) > PAIR_RATIO * max(1, min(wa, wb)):
            return "\n".join(_solo(x) for x in (a, b))
    inner = "".join(f'<div class="{k}">{x}</div>' for k, x in (("pa", a), ("pb", b)) if x)
    return f'<div class="pair{" " + cls if cls else ""}">{inner}</div>'


def _first_eager(body_html: str) -> str:
    """Only the first image on the page loads right away; the rest stay lazy."""
    return body_html.replace(' loading="lazy"', "", 1)


def prepare_md(md: str) -> str:
    """Clean an issue's Markdown before rendering: comments, the chart image line, the
    footer line and stray braces go; URLs get short labels; dashes are replaced."""
    md = COMMENT.sub("", md)
    md = CHART_IMG.sub("", md)
    md = "\n".join(ln for ln in md.splitlines() if ln.strip() != FOOTER)
    md = md.replace("{{", "").replace("}}", "")
    md = tidy(no_dashes(tame_urls(md)))
    md = SAME_TICKER.sub(r"\1", md)  # "Why UDR (UDR) moved" -> "Why UDR moved"
    return COFFEE_LABEL_MD.sub(r"\1\n\n", md)  # the talking-point bullets parse as a list


def _h2_text(section_html: str) -> tuple[str, str] | None:
    m = re.search(r'<h2 id="([^"]+)"[^>]*>(.*?)</h2>', section_html, re.S)
    return (m.group(1), html.unescape(TAGS.sub("", m.group(2))).strip()) if m else None


def render_issue_html(md: str, factsheet: dict | None, problems: list[str],
                      chart_rel=None, run_date: date | None = None, *,
                      head_extra: str = "", nav: str = "", extra_body: str = "",
                      title: str | None = None, charts: dict | None = None,
                      base: str | None = None, fonts_base: str | None = None) -> str:
    """`chart_rel` is the 10-Year chart's src (older call style); `charts` maps chart names
    (CHART_NAMES) to {"src", "alt", "width", "height"} and wins over `chart_rel`.
    `base` / `fonts_base`: see _shell (both None = the standalone preview)."""
    md = prepare_md(md)
    all_charts = {**as_charts(chart_rel), **(charts or {})}

    values = (factsheet or {}).get("values") or {}
    if run_date is None and factsheet and factsheet.get("date"):
        run_date = date.fromisoformat(factsheet["date"])
    day_type = (factsheet or {}).get("day_type")

    # Page order (Broadsheet): the front (lead Top Story beside The Brief and the Market
    # Snapshot), the rest of Top Stories, the Coffee chat band, Debt Markets beside Market
    # Watch, Quick Hits, any other sections full width, The Numbers, then Term of the Day
    # beside the Data Room. Phones stack it all: Brief, Snapshot, lead, and so on.
    pre, sections = _split(md)
    used = {"summary-h", "snapshot-h", "dataroom-h", "term-h", "notes-h", "toc-h", "numbers",
            "content", "top", "lead-h"}
    # The masthead name is the page's only h1, so a "# " title in the Markdown becomes h2.
    pre_html = re.sub(r"<(/?)h1\b", r"<\1h2", _md(pre)) if pre.strip() else ""
    head = [_decorate(pre_html, used)] if pre_html else []
    brief, terms, named, others = "", [], {}, []
    lead, more, coffee = "", "", ""
    numbers_prose = ""
    for heading, text in sections:
        if heading == "The Numbers":
            numbers_prose = numbers_prose or _numbers_prose(text)
        elif heading == "Term of the Day":
            terms.append(_term(text))
        elif heading == "Top Stories" and not (lead or more or coffee):
            lead, more, coffee = top_stories(text, used)
        else:
            sec = _decorate(_md(f"## {heading}{text}"), used,
                            icons_h3=heading == "Market Watch")
            if heading == "The Brief" and not brief:
                brief = style_section(sec, heading)
            elif heading in ("Debt Markets", "Market Watch", "Quick Hits") and heading not in named:
                named[heading] = style_section(sec, heading)
            else:
                others.append(style_section(sec, heading))
    has_data = factsheet is not None
    snapshot = market_snapshot(values) if has_data else ""
    summary = market_summary(values, all_charts, numbers_prose) if has_data else ""
    room = data_room(values, run_date) if has_data else ""

    # The front: the lead story (8 columns) beside The Brief (4), then the Snapshot strip
    # across the full width. Phones reorder it with CSS: Brief, Snapshot, lead.
    front = ""
    if lead or brief or snapshot:
        cls = "front" + ("" if lead else " no-lead") + ("" if brief else " no-brief")
        # DOM (and focus) order matches the phone order; desktop places them by grid.
        front = f'<div class="{cls}">{brief}{snapshot}{lead}</div>'

    story_part = [x for x in (more, coffee,
                              _pair(named.get("Debt Markets", ""), named.get("Market Watch", "")),
                              named.get("Quick Hits", ""), *others) if x]
    back = _pair("".join(terms), room, "back")
    rest = story_part + [x for x in (summary, back) if x]

    links = []
    if lead:
        links.append(("top-stories", "Top Stories"))
    for part in rest:
        for sec in re.findall(r"<section\b.*?(?=<section\b|$)", part, re.S):
            found = _h2_text(sec)
            if not found or found[0] in ("notes-h", "more-stories" if lead else ""):
                continue
            if sec.startswith('<section class="coffee"'):
                continue
            links.append(found)
    toc = jump_list(links)
    body = head + ([front] if front else []) + ([toc] if toc else []) + rest
    body_html = _first_eager("\n".join(body))

    if title is None:
        title = "CRE Blurb" + (f" | {run_date:%B} {run_date.day}, {run_date.year}" if run_date else "")
    fonts, faces, links = _shell(base, fonts_base)
    return PAGE.format(
        title=_esc(title), head_extra=_slot(head_extra), fonts=fonts, css=faces + CSS,
        masthead=masthead(run_date, day_type, read_minutes(body_html), values, nav, h1=True),
        notes=_slot(_notes(problems)), body=body_html, extra_body=_slot(extra_body),
        footer=_esc(FOOTER), footer_links=links)


def render_page(title: str, body_html: str, *, head_extra: str = "", nav: str = "",
                factsheet: dict | None = None, base: str | None = None,
                fonts_base: str | None = None) -> str:
    """A non-issue page (About, Archive, Glossary, Privacy, Terms, Accessibility, 404) in
    the same shell, with the latest issue's date, edition and ticker in the masthead
    (`factsheet`), and no editor notes. `base` / `fonts_base`: see _shell."""
    fs = factsheet or {}
    run_date = date.fromisoformat(fs["date"]) if fs.get("date") else None
    fonts, faces, links = _shell(base, fonts_base)
    return PAGE.format(
        title=_esc(title), head_extra=_slot(head_extra), fonts=fonts, css=faces + CSS,
        masthead=masthead(run_date, fs.get("day_type"), None, fs.get("values"), nav),
        notes="", body=f'<div class="page">{body_html}</div>', extra_body="",
        footer=_esc(FOOTER), footer_links=links)


CSS = """
:root {
  --paper: #FFFFFF; --ink: #121417; --navy: #0E2A47; --navy-tint: #B9C8DA; --gold: #A9853A;
  --gold-text: #806226;
  --gold-on-navy: #C9A85A; --hairline: #DADDE1; --muted: #5B6470; --panel: #F3F5F8;
  --deck: #3A424C; --up: #1F7A4D; --down: #B23A3A; --up-on-navy: #9ED9B6;
  --down-on-navy: #F2A6A6; --select: #CCD8E6;
  --amber-bg: #FBF4E4; --amber-line: #E3C88A; --amber-ink: #6E4A0B;
  --display: "Libre Caslon Display", "Libre Caslon Text", Georgia, "Times New Roman", serif;
  --serif: "Source Serif 4", Georgia, "Times New Roman", serif;
  --sans: "Public Sans", -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body { margin: 0; background: var(--paper); color: var(--ink); font-family: var(--serif);
  font-size: 17px; line-height: 1.55; overflow-x: hidden; }
::selection { background: var(--select); color: var(--ink); }
:focus-visible { outline: 2px solid var(--navy); outline-offset: 3px; }
a { color: var(--navy); text-decoration: underline; text-decoration-thickness: 1px;
  text-underline-offset: 3px; text-decoration-color: rgba(14, 42, 71, .35); }
a:hover { color: var(--navy); text-decoration-color: var(--navy); }
p, li { max-width: 70ch; }
p { margin: 0 0 1em; }
ul, ol { padding-left: 1.2em; margin: 0 0 1em; }
li { margin: 0 0 .5em; }
strong { font-weight: 700; }
.wrap { max-width: 1180px; margin: 0 auto; padding-left: 24px; padding-right: 24px; }
.skip { position: absolute; left: 8px; top: -60px; background: var(--navy); color: #FFFFFF;
  padding: 10px 14px; z-index: 10; text-decoration: none; font-family: var(--sans); }
.skip:focus { top: 8px; }

/* Masthead: utility row, ticker, name, double rule, nav */
.utility { border-bottom: 1px solid var(--hairline); font-family: var(--sans); font-size: 12px;
  line-height: 1.5; color: var(--muted); }
.utility .wrap { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 8px;
  padding-top: 10px; padding-bottom: 10px; }
.utility .edition { letter-spacing: .08em; text-transform: uppercase; font-weight: 600;
  color: var(--navy); }
.ticker-band { position: relative; display: flex; align-items: center;
  background: var(--navy); }
.ticker { flex: 1 1 auto; min-width: 0; background: var(--navy); color: #FFFFFF;
  font-family: var(--sans); font-size: 13px;
  line-height: 1.4; font-variant-numeric: tabular-nums; padding: 10px 0 10px 24px;
  overflow: hidden; white-space: nowrap; }
/* Pause/Play: a visually hidden but focusable checkbox, its label styled as a button at
   the band's right end. Checked = paused; the label then reads "Play". */
.sr-only, .visually-hidden-but-focusable { position: absolute; width: 1px;
  height: 1px; margin: -1px; padding: 0; border: 0; overflow: hidden; clip: rect(0 0 0 0);
  clip-path: inset(50%); white-space: nowrap; }
.tk-pause { order: 2; flex: none; display: inline-flex; align-items: center;
  justify-content: center; align-self: stretch; min-width: 44px; margin: 0; padding: 0 16px;
  border-left: 1px solid rgba(185, 200, 218, .35); background: var(--navy); color: #FFFFFF;
  font-family: var(--sans); font-size: 11px; font-weight: 700; line-height: 1;
  letter-spacing: .1em; text-transform: uppercase; cursor: pointer; user-select: none; }
.tk-pause:hover { text-decoration: underline; text-underline-offset: 3px; }
.tk-pause .tk-icon { width: 9px; height: 9px; margin-right: 6px; vertical-align: -1px; }
.tk-pause .tk-on { display: none; }
#ticker-pause:checked ~ .tk-pause .tk-off { display: none; }
#ticker-pause:checked ~ .tk-pause .tk-on { display: inline; }
#ticker-pause:checked ~ .ticker .ticker-track { animation-play-state: paused; }
#ticker-pause:focus-visible ~ .tk-pause { outline: 2px solid #FFFFFF; outline-offset: -5px; }
.ticker-track { display: inline-flex; animation: cb-ticker calc(var(--n, 12) * 4.6s) linear infinite; }
.ticker:hover .ticker-track { animation-play-state: paused; }
@keyframes cb-ticker { from { transform: translateX(0); } to { transform: translateX(-33.3333%); } }
.tk { padding-right: 30px; }
.tk b { font-weight: 600; }
.tk-date { font-weight: 700; letter-spacing: .1em; font-size: 11px; color: var(--gold-on-navy);
  align-self: center; }
.tk-rate, .tk-flat { color: var(--navy-tint); }
.tk-up { color: var(--up-on-navy); }
.tk-down { color: var(--down-on-navy); }
.tk .tri { width: 8px; height: 8px; margin-right: 2px; vertical-align: 0; }
.masthead { text-align: center; padding-top: 36px; }
.masthead .name { margin: 0 auto; max-width: none; font-family: var(--display); font-weight: 400;
  font-size: clamp(48px, 7vw, 76px); line-height: 1; letter-spacing: -.01em; color: var(--ink); }
.tagline { margin: 12px auto 0; max-width: none; font-family: var(--sans); font-size: 12px;
  line-height: 1.5; letter-spacing: .16em; text-transform: uppercase; color: var(--muted); }
.tagline-short, .dateline-m { display: none; }
.double-rule { margin-top: 22px; height: 7px; border-top: 3px solid var(--ink);
  border-bottom: 1px solid var(--ink); }
.site-nav { display: flex; flex-wrap: wrap; justify-content: center; gap: 4px 34px;
  padding: 4px 0; border-bottom: 1px solid var(--hairline); font-family: var(--sans);
  font-size: 13px; font-weight: 600; letter-spacing: .1em; text-transform: uppercase; }
.site-nav a { color: var(--ink); text-decoration: none; padding: 12px 0;
  border-bottom: 2px solid transparent; }
.site-nav a:hover { border-bottom-color: var(--hairline); }
.site-nav a[aria-current="page"] { border-bottom-color: var(--gold); }

/* Shared type: section labels, display titles, headlines */
main { padding-top: 32px; overflow-wrap: break-word; }
main img, main svg { max-width: 100%; }
main h1 { font-family: var(--display); font-weight: 400; font-size: 44px; line-height: 1.1;
  margin: 8px 0 16px; }
main h2, .group-name, .toc-title { font-family: var(--sans); font-size: 13px; font-weight: 700;
  line-height: 1.4; letter-spacing: .14em; text-transform: uppercase; color: var(--ink);
  margin: 0 0 16px; padding-bottom: 8px; border-bottom: 1px solid var(--ink); max-width: none; }
main h2 .icon, main h3 .icon { display: inline-block; vertical-align: -0.15em; margin-right: 8px; }
.icon { flex: none; color: var(--navy); }
.display, main h2.display { font-family: var(--display); font-weight: 400; font-size: 34px;
  line-height: 1.1; letter-spacing: 0; text-transform: none; border: 0; padding: 0;
  margin: 0 0 6px; color: var(--ink); }
h3 { font-family: var(--serif); font-weight: 700; font-size: 22px; line-height: 1.25;
  margin: 24px 0 8px; text-wrap: balance; }
h2 + h3 { margin-top: 0; }
.hl a { color: var(--ink); text-decoration: none; }
.hl a:hover { text-decoration: underline; text-decoration-color: var(--ink); }
.kicker { margin: 0 0 10px; font-family: var(--sans); font-size: 12px; font-weight: 700;
  letter-spacing: .12em; text-transform: uppercase; color: var(--navy); }
.source, .caption, .intro, .coffee-note, .return { font-family: var(--sans); font-size: 13px;
  line-height: 1.5; color: var(--muted); }
.why strong { color: var(--muted); font-weight: 600; }
.sec, .coffee, .pair, .summary, .toc { margin-top: 44px; }
.pair .sec { margin-top: 0; }

/* Front: lead story beside The Brief, then the Market Snapshot strip */
.front { display: flex; flex-direction: column; }
.lead { margin-top: 28px; }
.lead .hl { margin: 0; font-family: var(--serif); font-weight: 700; font-size: clamp(30px, 4vw, 44px);
  line-height: 1.08; letter-spacing: -.01em; text-transform: none; border: 0; padding: 0;
  color: var(--ink); text-wrap: balance; }
.deck p { margin: 14px 0 0; font-size: 21px; line-height: 1.45; color: var(--deck); }
.lead .source { margin: 14px 0 0; }
.why-panel { margin-top: 22px; padding: 18px 20px; background: var(--panel);
  border-top: 2px solid var(--navy); }
.why-panel p { margin: 0; font-size: 18px; line-height: 1.6; max-width: none; }
.why-panel p + p { margin-top: 10px; }
.why-label { display: block; font-family: var(--sans); font-size: 13px; font-weight: 700;
  letter-spacing: .08em; text-transform: uppercase; color: var(--navy); }
.brief h2, .snapshot h2 { border-bottom-width: 2px; margin-bottom: 14px; }
.brief ol { list-style: none; margin: 0; padding: 0; counter-reset: brief; }
.brief li { counter-increment: brief; position: relative; padding-left: 30px; margin: 0;
  font-size: 17px; line-height: 1.45; max-width: none; }
.brief li + li { margin-top: 16px; padding-top: 16px; border-top: 1px solid var(--hairline); }
.brief li::before { content: counter(brief); position: absolute; left: 0; top: 0;
  font-family: var(--display); font-size: 26px; line-height: 1; color: var(--gold-text); }
.brief li + li::before { top: 16px; }
.brief p:last-child { margin-bottom: 0; }
.snapshot { margin-top: 30px; }
.snapshot h2 { margin-bottom: 0; }
.snapshot .cell { grid-template-columns: minmax(0, 1fr) auto; }
.snapshot .cell dt { grid-column: 1; grid-row: 1; }
.snapshot .cell dd { grid-column: 2; grid-row: 1 / span 2; display: flex;
  flex-direction: column; align-items: flex-end; gap: 1px; }
.snapshot .cell .hint { grid-column: 1; grid-row: 2; display: block; margin: 0;
  text-align: left; white-space: normal; }
.snapshot .chg { margin-left: 0; }
.snapshot .caption { margin: 10px 0 0; }
.snapshot .caption a { white-space: nowrap; }
@media (min-width: 600px) {
  .snapshot dl { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr));
    border-bottom: 1px solid var(--ink); }
  .snapshot .cell { display: block; padding: 14px 24px 16px; border-bottom: 0;
    border-left: 1px solid var(--hairline); }
  .snapshot .cell:first-child { padding-left: 0; border-left: 0; }
  .snapshot .cell:last-child { padding-right: 0; }
  .snapshot .cell dt { font-size: 14px; font-weight: 600; }
  .snapshot .cell dd { flex-direction: row; align-items: baseline; justify-content: flex-start;
    flex-wrap: wrap; gap: 4px 12px; margin-top: 6px; text-align: left; max-width: none; }
  .snapshot .cell .val { font-size: 30px; line-height: 1.1; }
  .snapshot .cell .chg { font-size: 14px; }
  .snapshot .cell .hint { margin-top: 6px; }
}

/* Data rows (Snapshot, The Numbers, Data Room) */
dl { margin: 0; }
.row { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 12px;
  align-items: baseline; padding: 11px 0; border-bottom: 1px solid var(--hairline);
  font-family: var(--sans); }
dt { font-size: 15px; line-height: 1.4; color: var(--ink); }
.hint { display: block; color: var(--muted); font-size: 13px; line-height: 1.4; margin-top: 2px; }
dd { margin: 0; text-align: right; font-variant-numeric: tabular-nums lining-nums;
  white-space: nowrap; }
.val { font-weight: 600; font-size: 16px; }
.chg { display: inline-flex; align-items: center; justify-content: flex-end; gap: 4px;
  margin-left: 10px; font-size: 13px; font-weight: 500; }
.chg.up { color: var(--up); }
.chg.down { color: var(--down); }
.chg.unch { color: var(--muted); }
.chg.rate { color: var(--navy); }  /* rates: direction only, no good/bad color */
.tri { flex: none; }
.na { color: var(--muted); font-style: italic; }
.tag { color: var(--muted); font-size: 13px; font-weight: 600; letter-spacing: .08em;
  text-transform: uppercase; margin-left: 4px; }

/* Top Stories grid */
.story { padding: 18px 0; border-bottom: 1px solid var(--hairline); min-width: 0; }
.story:first-child { padding-top: 4px; }
.story .hl { margin: 0; font-size: 24px; line-height: 1.18; }
.story p { margin: 10px 0 0; font-size: 17px; line-height: 1.55; }
.story .source { font-size: 13px; }
.top-stories > p { font-size: 17px; }

/* Coffee chat band */
.coffee { padding: 28px 0; border-top: 3px solid var(--ink); border-bottom: 1px solid var(--ink); }
.coffee-note { margin: 10px 0 18px; font-size: 14px; }
.points { list-style: none; margin: 0; padding: 0; display: grid; gap: 20px; counter-reset: pt; }
.points li { counter-increment: pt; margin: 0; font-size: 18px; line-height: 1.5;
  font-style: italic; }
.points li::before { content: "0" counter(pt); display: block; font-style: normal;
  font-family: var(--sans); font-size: 12px; font-weight: 700; letter-spacing: .1em;
  color: var(--gold-text); }
.points a { font-style: normal; }

/* Debt Markets, Market Watch, Quick Hits, other sections */
.sec p { font-size: 17px; line-height: 1.6; }
.watch h3 { font-size: 20px; }
.quick-hits ul { list-style: none; margin: 0; padding: 0; }
.quick-hits li { margin: 0; padding: 12px 0; border-bottom: 1px solid var(--hairline);
  font-size: 16px; line-height: 1.55; max-width: none; }
.quick-hits li:first-child { padding-top: 0; }
.sec-body > *:last-child { margin-bottom: 0; }

/* The Numbers */
.summary { padding-top: 28px; border-top: 3px solid var(--ink); }
.summary .intro { margin: 0 0 20px; }
.group-name { margin: 0; }
.group + .group, .chart + .group, .chart-pair + .group, .num-side { margin-top: 32px; }
.summary .caption { margin: 10px 0 0; }
.chart { margin: 28px 0 0; min-width: 0; }
.chart picture { display: block; }
.chart img { display: block; width: 100%; max-width: 100%; height: auto; }
.chart figcaption { font-family: var(--sans); color: var(--muted); font-size: 13px;
  line-height: 1.45; margin-top: 8px; padding-top: 8px; border-top: 1px solid var(--hairline); }
.chart-pair { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 0 28px; }
.takeaway { margin-top: 32px; padding-top: 20px; border-top: 1px solid var(--hairline); }
.takeaway p { font-size: 17px; line-height: 1.6; }

/* Term of the Day and Data Room */
.term { background: var(--panel); padding: 22px 20px; border-top: 2px solid var(--gold); }
.term h2 { border: 0; padding: 0; margin: 0; color: var(--navy); }
.term-name { margin: 12px 0 0; font-family: var(--display); font-size: 32px; line-height: 1.1; }
.term-def p { margin: 10px 0 0; font-size: 18px; line-height: 1.6; }
.data-room .intro { margin: 0 0 4px; }
.pair.back .pb { margin-top: 32px; }
.term.solo, .data-room.solo { margin-top: 44px; }

/* Jump list (phones and tablets), end of issue, notes, footer */
.toc { padding: 12px 0 14px; border-top: 1px solid var(--hairline);
  border-bottom: 1px solid var(--hairline); font-family: var(--sans); }
.toc-title { border: 0; padding: 0; margin: 0 0 4px; }
.toc ul { list-style: none; padding: 0; margin: 0; display: flex; flex-wrap: wrap;
  gap: 0 20px; font-size: 15px; }
.toc li { margin: 0; }
.toc a, .issue-nav a, .issue-list a { display: inline-flex; align-items: center; min-height: 44px; }
.return { margin: 48px 0 0; font-size: 14px; }
.issue-nav { margin: 12px 0 0; padding-top: 8px; border-top: 1px solid var(--hairline);
  display: flex; flex-wrap: wrap; justify-content: space-between; gap: 0 24px;
  font-family: var(--sans); font-size: 15px; }
.issue-nav .top { margin-left: auto; }
.recent { margin-top: 32px; }
.notes { background: var(--amber-bg); border: 1px solid var(--amber-line); color: var(--amber-ink);
  margin: 0 0 24px; padding: 14px 20px; font-family: var(--sans); font-size: 15px; }
.notes h2 { font-size: 13px; color: var(--amber-ink); border: 0; margin: 0 0 6px; padding: 0; }
.notes ul { margin: 0; }
.notes li { margin: 0 0 2px; overflow-wrap: anywhere; }
footer { margin-top: 56px; border-top: 3px solid var(--ink); }
footer .wrap { padding-top: 22px; padding-bottom: 40px; display: flex; flex-wrap: wrap;
  justify-content: space-between; align-items: baseline; gap: 8px 24px; font-family: var(--sans);
  font-size: 13px; line-height: 1.5; color: var(--muted); }
footer p { margin: 0; }
footer .foot-name { font-family: var(--display); font-size: 22px; color: var(--ink); }
footer .foot-links { flex-basis: 100%; display: flex; flex-wrap: wrap; align-items: center;
  gap: 0 10px; padding-top: 6px; }
footer .foot-links a { color: var(--muted); }
footer .foot-links a:hover { color: var(--navy); }
footer .foot-links .sep { color: var(--hairline); }

/* Archive, About, Glossary, 404 */
.page { max-width: 820px; margin: 0 auto; }
.page h2 { margin-top: 40px; }
.page h1 + h2, .page h1 + p + h2 { margin-top: 24px; }
.issue-list { list-style: none; margin: 0; padding: 0; }
.issue-list li { margin: 0; padding: 6px 0; border-bottom: 1px solid var(--hairline); max-width: none; }
.issue-list .when { font-family: var(--sans); font-size: 14px; }
.issue-list .headline { display: block; padding-bottom: 8px; font-size: 20px; font-weight: 600;
  line-height: 1.3; color: var(--ink); }
.gloss { margin: 0; padding: 22px 0; border-bottom: 1px solid var(--hairline); }
.gloss .term-name { margin: 0; padding: 0; border: 0; font-family: var(--display);
  font-size: 30px; font-weight: 400; line-height: 1.1; letter-spacing: 0; text-transform: none; }
.gloss .term-def p { font-size: 17px; }
.gloss-from { margin: 8px 0 0; font-family: var(--sans); font-size: 13px; color: var(--muted); }

/* Desktop: the 12-column broadsheet grid */
@media (min-width: 900px) {
  .front { display: grid; grid-template-columns: repeat(12, minmax(0, 1fr)); column-gap: 32px; }
  /* The lead is a flex column: its "Why it matters" panel sits at the bottom, so the
     lead and The Brief end at about the same height. */
  .lead { grid-column: span 8; margin-top: 0; padding-right: 32px; display: flex;
    flex-direction: column; border-right: 1px solid var(--hairline); }
  .lead > .why-panel { margin-top: auto; }
  .lead > :has(+ .why-panel) { margin-bottom: 22px; }
  .front .lead { grid-column: 1 / span 8; grid-row: 1; }
  .front .brief { grid-column: 9 / span 4; grid-row: 1; }
  .front .brief li + li { margin-top: 14px; padding-top: 14px; }
  .front .brief li + li::before { top: 14px; }
  .front .snapshot { grid-column: 1 / -1; grid-row: 2; margin-top: 36px; }
  .front.no-brief .lead { grid-column: 1 / -1; padding-right: 0; border-right: 0; }
  .front.no-lead .brief { grid-column: 1 / -1; }
  .front.no-lead .brief ol { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 0 32px; }
  .front.no-lead .brief li + li { margin-top: 0; padding-top: 0; border-top: 0; }
  .front.no-lead .brief li + li::before { top: 0; }
  .stories { display: grid; }
  .stories.c1 { grid-template-columns: minmax(0, 1fr); }
  .stories.c2 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .stories.c3 { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .story, .story:first-child { padding: 0 24px; border-bottom: 0;
    border-left: 1px solid var(--hairline); }
  .c1 .story, .c2 .story:nth-child(2n+1), .c3 .story:nth-child(3n+1) { padding-left: 0;
    border-left: 0; }
  .c2 .story:nth-child(2n), .c3 .story:nth-child(3n), .story:last-child { padding-right: 0; }
  .c2 .story:nth-child(n+3), .c3 .story:nth-child(n+4) { margin-top: 28px; padding-top: 24px;
    border-top: 1px solid var(--hairline); }
  .c3 .story:last-child:nth-child(3n+2) { grid-column: span 2; }
  .coffee { display: grid; grid-template-columns: repeat(12, minmax(0, 1fr)); column-gap: 32px; }
  .coffee-head { grid-column: span 4; }
  .coffee-note { margin-bottom: 0; }
  .points { grid-column: span 8; gap: 24px; }
  .points.c2 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .points.c3 { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .pair { display: grid; grid-template-columns: repeat(12, minmax(0, 1fr)); column-gap: 32px; }
  .pair > .pa { grid-column: span 7; padding-right: 32px; border-right: 1px solid var(--hairline); }
  .pair > .pb { grid-column: span 5; }
  .pair > :only-child { grid-column: 1 / -1; padding-right: 0; border-right: 0; }
  .pair.back > .pa { padding-right: 0; border-right: 0; }
  /* the Term panel fills its cell, so a taller Data Room never leaves a blank area */
  .pair.back > .pa { display: flex; flex-direction: column; }
  .pair.back > .pa > .term { flex: 1; }
  .pair.back .pb { margin-top: 0; }
  .term { padding: 26px 28px; }
  .quick-hits ul { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 0 32px; }
  .quick-hits li, .quick-hits li:first-child { padding: 0 0 14px; border-bottom: 0; }
  .wide .sec-body, .solo .sec-body { columns: 2; column-gap: 48px; column-rule: 1px solid var(--hairline); }
  .wide .sec-body > *, .solo .sec-body > * { break-inside: avoid; }
  .wide .sec-body h3, .solo .sec-body h3 { break-after: avoid; margin-top: 0; }
  .num-cols { display: grid; grid-template-columns: repeat(12, minmax(0, 1fr)); column-gap: 32px; }
  .num-main { grid-column: span 7; }
  .num-side { grid-column: span 5; margin-top: 0; }
  .num-cols > :only-child { grid-column: 1 / -1; }
  .takeaway { columns: 2; column-gap: 48px; }
  .takeaway p { break-inside: avoid; }
  .toc { display: none; }
  .issue-list li { display: grid; grid-template-columns: 250px minmax(0, 1fr); gap: 4px 24px;
    align-items: baseline; padding: 14px 0; }
  .issue-list .headline { padding-bottom: 0; }
}

/* Phones */
@media (max-width: 599px) {
  .wrap { padding-left: 16px; padding-right: 16px; }
  body { font-size: 16px; }
  .utility { display: none; }
  .ticker { font-size: 12px; padding: 9px 0 9px 16px; }
  .ticker-track { animation-duration: calc(var(--n, 12) * 2.5s); }
  .tk-pause { min-height: 44px; padding: 0 12px; }
  .tk { padding-right: 22px; }
  .tk-date { font-size: 12px; }
  .masthead { padding-top: 22px; }
  .dateline-m { display: block; margin: 0 auto 8px; max-width: none; font-family: var(--sans);
    font-size: 11px; letter-spacing: .1em; text-transform: uppercase; color: var(--muted); }
  .tagline { font-size: 10px; letter-spacing: .12em; margin-top: 8px; }
  .tagline-long { display: none; }
  .tagline-short { display: inline; }
  .double-rule { margin-top: 16px; }
  .site-nav { flex-wrap: nowrap; justify-content: space-between; gap: 0; padding: 0;
    font-size: 12px; letter-spacing: .08em; }
  .site-nav a { display: flex; align-items: center; min-height: 44px; padding: 0; }
  main { padding-top: 22px; }
  main h1 { font-size: 34px; }
  main h2 { font-size: 12px; }
  .display, main h2.display { font-size: 28px; }
  .brief { background: var(--panel); padding: 16px; border-top: 2px solid var(--navy); }
  .brief h2 { border: 0; padding: 0; margin-bottom: 10px; }
  .brief li { font-size: 16px; padding-left: 24px; }
  .brief li + li { margin-top: 12px; padding-top: 0; border-top: 0; }
  .brief li + li::before { top: 0; }
  .brief li::before { font-size: 22px; }
  .snapshot { margin-top: 24px; }
  .lead .hl { font-size: 30px; line-height: 1.1; }
  .deck p { font-size: 17px; line-height: 1.5; margin-top: 10px; }
  .why-panel { background: none; padding: 12px 0 0; margin-top: 14px;
    border-top: 1px solid var(--hairline); }
  .why-panel p { font-size: 16px; line-height: 1.55; }
  .why-label { font-size: 12px; }
  .sec, .coffee, .pair, .summary, .toc { margin-top: 28px; }
  .story { padding: 14px 0; }
  .story .hl { font-size: 21px; line-height: 1.2; }
  .story p, .sec p, .takeaway p { font-size: 16px; line-height: 1.5; }
  .coffee { padding: 20px 0; }
  .coffee-note { margin: 6px 0 14px; }
  .points { gap: 14px; }
  .points li { font-size: 17px; }
  .quick-hits li { font-size: 16px; line-height: 1.5; }
  .term { padding: 18px 16px; }
  .term-name { font-size: 26px; margin-top: 8px; }
  .term-def p { font-size: 16px; line-height: 1.55; margin-top: 8px; }
  .row { gap: 10px; padding: 10px 0; }
  /* Value over change, right-aligned, so long values never wrap awkwardly. */
  dd { display: flex; flex-direction: column; align-items: flex-end; gap: 1px; max-width: 52vw; }
  .val { white-space: normal; text-align: right; }
  .chg { margin-left: 0; }
  .issue-list .headline { font-size: 18px; }
  footer { margin-top: 32px; }
  footer .wrap { display: block; padding-top: 18px; padding-bottom: 28px; font-size: 12px; }
  footer p { margin: 0 0 4px; }
  footer .foot-links { padding-top: 4px; }
  footer .foot-links a { display: inline-flex; align-items: center; min-height: 44px; }
}
@media (max-width: 359px) {
  .site-nav { font-size: 11px; letter-spacing: .03em; }
}
@media (prefers-reduced-motion: reduce) {
  .ticker { overflow: visible; white-space: normal; padding-right: 24px; }
  .ticker-track { animation: none; flex-wrap: wrap; row-gap: 4px; }
  .tk { white-space: nowrap; }
  .tk-copy { display: none; }
  .tk-pause, #ticker-pause { display: none; }
  * { animation: none !important; transition: none !important; scroll-behavior: auto !important; }
}
"""

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>{head_extra}
{fonts}<style>{css}</style>
</head>
<body>
<a class="skip" href="#content">Skip to content</a>
{masthead}
<main id="content" class="wrap">{notes}
{body}{extra_body}
</main>
<footer>
<div class="wrap">
<p class="foot-name">CRE Blurb</p>
<p>Every number is pulled automatically from public data. Reviewed by the editor.</p>
<p>{footer}</p>
{footer_links}
</div>
</footer>
</body>
</html>
"""
