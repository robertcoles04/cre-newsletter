"""Deterministic slop and number checks on a drafted issue (before placeholders are filled).

check_issue returns a list of {kind, detail} problems; an empty list means clean.
"""

import re
from collections import Counter
from pathlib import Path

from datetime import date

from src.cleanup import empty_headings
from src.freshness import core_stale, stale_series

ROOT = Path(__file__).resolve().parent.parent
FOOTER = "For informational purposes only. Not investment advice."
REQUIRED_PLACEHOLDERS = ("DGS10", "DGS10_CHG", "SOFR", "FED_TOP", "VNQ")
REQUIRED_DAYS = ("weekday", "friday")
MAX_EM_DASHES = 0  # owner style: none (render_html.no_dashes also strips them from the site)
MAX_SENTENCE_WORDS = 30
BUDGET_SLACK = 1.3
BUDGETS = {
    "The Brief": 60, "The Numbers": 100, "Debt Markets": 200, "Top Stories": 550,
    "Quick Hits": 220, "AI in Real Estate": 80, "AI Infrastructure": 80,
    "Term of the Day": 50, "Week in Review": 200, "AI in Real Estate Weekly": 150,
    "REIT Weekly": 200, "Week Ahead": 120, "Market Watch": 200, "Market Spotlight": 150,
    "Careers Corner": 100,
}
# Market-data sections: every number there must come from a placeholder.
MARKET_SECTIONS = {"The Numbers", "REIT Weekly", "Week Ahead"}
# Term of the Day may use a made-up round-number example, so its numbers are only
# checked in sentences that talk about market rates (TERM_RATE_WORDS).
TERM_SECTION = "Term of the Day"
TERM_RATE_WORDS = re.compile(r"\b(?:10Y|5Y|SOFR|Treasury|Treasuries|Fed|fed funds)\b", re.I)
STORY_KEYS = ("top", "quick_hits", "debt", "ai", "week_top", "ai_week")


def all_stories(factsheet: dict) -> list[dict]:
    """Every story the issue may cite: the STORY_KEYS lists plus Market Watch picks, mover
    stories and the Distress Watch pick."""
    stories = [s for key in STORY_KEYS for s in factsheet.get(key) or []]
    extra = list((factsheet.get("markets") or {}).values()) + [factsheet.get("distress")]
    extra += list((factsheet.get("mover_news") or {}).values())
    return stories + [s for s in extra if s]

PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")
COMMENT = re.compile(r"<!--.*?-->", re.S)
IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
URL = re.compile(r"https?://\S+")
SLOP_PATTERN = re.compile(r"\bnot (?:just|only)\b[^.]{1,80}\bbut\b", re.I)
# Based on the brief's `\d+(\.\d+)?\s?(%|bps|basis points)` and `\$\d`, widened to other
# unit spellings ("4.6 percent", "25bp", "25-basis-point") and to the whole dollar
# amount, so the sourced-number lookup compares "$120 million", not just "$1".
RATE_UNIT = r"(?:%|percent\b|per cent\b|pct\b|bps?\b|basis[- ]points?\b)"
NUMBER = re.compile(
    r"\d+(?:\.\d+)?[\s-]{0,2}" + RATE_UNIT +
    r"|\$\d+(?:,\d{3})*(?:\.\d+)?"
    r"(?:\s?(?:million|billion|trillion|bn|tn|mm|m|b|k)\b)?",
    re.I)
# Extra forms that only count in market-data sections: bare decimals ("4.62"),
# "91 dollars" and odds like "81 to 19". "10Y", "5Y" and "Oct 28" do not match.
MARKET_EXTRA = re.compile(r"\d+\.\d+|\d+\s+(?:dollars\b|to\s+\d+)", re.I)
LIST_ITEM = re.compile(r"^(?:[-*+]|\d+[.)])\s+")
# Contradiction check: prose saying REITs fell while VNQ rose (or the reverse).
REIT_SUBJECT = re.compile(
    r"\b(?:REITs?|REIT (?:stocks|shares)|real estate (?:stocks|shares|equities)|"
    r"property stocks)\b", re.I)
REIT_DOWN_WORDS = re.compile(
    r"\b(?:slid|slides?|sliding|f[ae]ll|falls|falling|drop(?:s|ped|ping)?|sank|sinks?|"
    r"sunk|tumbl\w*|declin\w*|slump\w*)\b", re.I)
REIT_UP_WORDS = re.compile(
    r"\b(?:rose|rises?|rising|rall\w*|climb\w*|gain\w*|jump\w*|surg\w*|soar\w*|rebound\w*)\b", re.I)
SENTENCE_END = re.compile(r"(?<=[.?!])\s+")


def load_banned(path: Path | None = None) -> list[str]:
    path = path or ROOT / "config" / "banned_phrases.txt"
    lines = path.read_text(encoding="utf8").splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.startswith("#")]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _plain(text: str) -> str:
    """Prose only: no comments, images or URLs; links -> link text; {{X}} -> X."""
    text = COMMENT.sub(" ", text)
    text = IMAGE.sub(" ", text)
    text = LINK.sub(r"\1", text)
    text = URL.sub(" ", text)
    return PLACEHOLDER.sub(r"\1", text)


def _number_text(text: str) -> str:
    """Text the number check scans: no placeholders, comments, link targets or URLs.
    Image alt text and link text are kept."""
    text = PLACEHOLDER.sub(" ", text)
    text = COMMENT.sub(" ", text)
    text = IMAGE.sub(r" \1 ", text)
    text = LINK.sub(r"\1", text)
    return URL.sub(" ", text)


def _words(text: str) -> int:
    return sum(1 for tok in text.split() if re.search(r"\w", tok))


def _sections(md: str) -> list[tuple[str, str]]:
    """Split on `## ` headings. Text before the first heading has name ""."""
    out: list[tuple[str, list[str]]] = [("", [])]
    for line in md.splitlines():
        if line.startswith("## "):
            out.append((line[3:].strip(), []))
        else:
            out[-1][1].append(line)
    return [(name, "\n".join(lines)) for name, lines in out]


def _story_corpus(factsheet: dict) -> str:
    parts = [f"{s.get('title', '')} {s.get('summary', '')}"
             for s in all_stories(factsheet)]
    return _norm(" ".join(parts))


DOLLAR_SCALE = {"": 1, "k": 10**3, "m": 10**6, "mm": 10**6, "million": 10**6,
                "b": 10**9, "bn": 10**9, "billion": 10**9, "tn": 10**12, "trillion": 10**12}


def _dollars(text: str) -> float | None:
    """'$631M', '$631 million' -> 631e6. None if not a dollar amount."""
    m = re.fullmatch(r"\$([\d,]+(?:\.\d+)?)\s?([a-z]*)", _norm(text))
    if not m or m.group(2) not in DOLLAR_SCALE:
        return None
    return float(m.group(1).replace(",", "")) * DOLLAR_SCALE[m.group(2)]


def _rate(text: str) -> tuple[float, str] | None:
    """'5.8 percent', '5.8%', '5.8 pct' -> (5.8, '%'); '25 bps', '25 basis points' -> (25, 'bp')."""
    m = re.fullmatch(r"(\d+(?:\.\d+)?)[\s-]{0,2}(.+)", _norm(text))
    if not m:
        return None
    unit = m.group(2)
    if unit.startswith(("%", "percent", "per cent", "pct")):
        return float(m.group(1)), "%"
    if unit.startswith(("bp", "basis")):
        return float(m.group(1)), "bp"
    return None


def _sourced(number: str, corpus: str) -> bool:
    value = _dollars(number)
    if value is not None:  # "$631 million" is sourced by "$631M": compare amounts
        return any(_dollars(m.group(0)) == value for m in NUMBER.finditer(corpus))
    rate = _rate(number)
    if rate is not None:  # "5.8 percent" is sourced by "5.8%": compare value and unit
        return any(_rate(m.group(0)) == rate for m in NUMBER.finditer(corpus))
    pattern = r"(?<![\d.])" + re.escape(_norm(number)) + r"(?!\d|[.,]\d)"
    return re.search(pattern, corpus) is not None


def _check_numbers(sections, factsheet) -> list[dict]:
    corpus = _story_corpus(factsheet)
    problems = []
    for name, body in sections:
        text = _number_text(body)
        if name == TERM_SECTION:
            for sentence in SENTENCE_END.split(text):
                if TERM_RATE_WORDS.search(sentence):
                    for m in NUMBER.finditer(sentence):
                        if not m.group(0).startswith("$"):
                            problems.append({"kind": "model_number",
                                             "detail": f"{name}: {m.group(0)}"})
            continue
        matches = list(NUMBER.finditer(text))
        if name in MARKET_SECTIONS:
            matches += [m for m in MARKET_EXTRA.finditer(text)
                        if not any(m.start() < n.end() and n.start() < m.end()
                                   for n in matches)]
        for m in matches:
            label = f"{name or 'intro'}: {m.group(0)}"
            if name in MARKET_SECTIONS:
                problems.append({"kind": "model_number", "detail": label})
            elif not _sourced(m.group(0), corpus):
                problems.append({"kind": "unsourced_number", "detail": label})
    return problems


def _prose_units(md: str) -> list[str]:
    """Paragraphs and list items, skipping headings, images, comments and blank lines."""
    units, cur = [], []

    def flush():
        if cur:
            units.append(" ".join(cur))
            cur.clear()

    for line in COMMENT.sub(" ", md).splitlines():
        s = line.strip().lstrip(">").strip()
        if not s or s.startswith("#") or s.startswith("!["):
            flush()
            continue
        if LIST_ITEM.match(s):
            flush()
            s = LIST_ITEM.sub("", s)
        cur.append(s)
    flush()
    return units


def _check_sentences(md: str) -> list[dict]:
    problems = []
    for unit in _prose_units(md):
        for sentence in SENTENCE_END.split(_plain(unit)):
            n = _words(sentence)
            if n > MAX_SENTENCE_WORDS:
                snippet = " ".join(sentence.split()[:8])
                problems.append({"kind": "long_sentence", "detail": f"{n} words: {snippet}..."})
    return problems


def _vnq_sign(factsheet: dict) -> int:
    """+1 / -1 for VNQ_CHG like "+0.4%" / "-1.2%"; 0 when flat or unavailable."""
    raw = str((factsheet.get("values") or {}).get("VNQ_CHG", ""))
    m = re.match(r"\s*([+-]?\d+(?:\.\d+)?)", raw)
    if not m:
        return 0
    value = float(m.group(1))
    return (value > 0) - (value < 0)


CLAUSE_END = re.compile(r"[,;:(]|\b(?:as|while|but|after|because|even though|and|though)\b",
                        re.I)


# A general cause-and-effect explanation, not a report of today's move.
GENERAL_RULE = re.compile(r"\b(?:when|whenever|if)\b[^.]*,", re.I)


def _check_contradictions(sections, factsheet) -> list[dict]:
    """Editor note when prose says REITs / real estate stocks fell while VNQ_CHG is
    positive, or rose while it is negative. A simple keyword + sign check: the move word
    must follow the subject in the same clause ("REITs fell as rates rose" is a fall)."""
    sign = _vnq_sign(factsheet)
    if sign == 0:
        return []
    wrong = REIT_DOWN_WORDS if sign > 0 else REIT_UP_WORDS
    problems = []
    for name, body in sections:
        for unit in _prose_units(body):
            for sentence in SENTENCE_END.split(_plain(unit)):
                if GENERAL_RULE.search(sentence):
                    continue  # "when yields rise, REIT stocks fall" explains, not reports
                clauses = [CLAUSE_END.split(sentence[m.end():], 1)[0]
                           for m in REIT_SUBJECT.finditer(sentence)]
                if any(wrong.search(c) for c in clauses):
                    snippet = " ".join(sentence.split()[:10])
                    vnq = "up" if sign > 0 else "down"
                    problems.append({"kind": "contradiction",
                                     "detail": f"{name or 'intro'}: says {snippet!r} but "
                                               f"VNQ is {vnq} today"})
    return problems


# ---------------------------------------------------------------- issue structure checks

LINK_URL = re.compile(r"\]\((https?://[^)\s]+)\)")
DISTRESS_LEAD = "**Distress Watch:**"
REPEAT_MAX = 2  # a story may appear in The Brief plus one full treatment


def _check_repeats(sections) -> list[dict]:
    """Editor note when one story URL is linked in 3+ places, or in both Debt Markets and
    its Distress Watch line."""
    counts: Counter = Counter()
    debt, distress = set(), set()
    for name, body in sections:
        for line in COMMENT.sub(" ", body).splitlines():
            urls = LINK_URL.findall(line)
            counts.update(urls)
            if name == "Debt Markets":
                (distress if line.strip().startswith(DISTRESS_LEAD) else debt).update(urls)
    problems = [{"kind": "repeat_in_issue", "detail": f"linked {n} times: {url}"}
                for url, n in counts.items() if n > REPEAT_MAX]
    problems += [{"kind": "repeat_in_issue",
                  "detail": f"Debt Markets and Distress Watch cite the same story: {url}"}
                 for url in sorted(debt & distress)]
    return problems


COFFEE_LABEL = "**Coffee chat talking points:**"
COFFEE_MIN, COFFEE_MAX = 2, 3
# Clichés banned from the talking points (prompt + check).
CLICHES = ("smart money", "worst is behind", "time will tell", "only time", "game changer",
           "game-changer")
ANY_NUMBER = re.compile(r"\$?\d[\d,]*(?:\.\d+)?")


def coffee_points(md: str) -> list[str] | None:
    """The talking-point bullets under COFFEE_LABEL, or None when the label is absent."""
    lines = md.splitlines()
    at = next((i for i, ln in enumerate(lines) if ln.strip().startswith(COFFEE_LABEL)), None)
    if at is None:
        return None
    points: list[str] = []
    for ln in lines[at + 1:]:
        s = ln.strip()
        if LIST_ITEM.match(s):
            points.append(LIST_ITEM.sub("", s))
        elif not s:
            if points:
                break
        elif s.startswith("#") or not points:
            break
        else:
            points[-1] += " " + s  # a wrapped bullet
    return points


def _check_coffee(md: str, factsheet: dict) -> list[dict]:
    """Talking points: 2 or 3 bullets, each with a link and one number found in a story,
    and no clichés."""
    points = coffee_points(md)
    if points is None:
        return []
    problems = []
    if not COFFEE_MIN <= len(points) <= COFFEE_MAX:
        problems.append({"kind": "coffee_chat",
                         "detail": f"{len(points)} talking points (want {COFFEE_MIN} to {COFFEE_MAX})"})
    corpus = _story_corpus(factsheet)
    for p in points:
        snippet = " ".join(_plain(p).split()[:8])
        if not LINK_URL.search(p):
            problems.append({"kind": "coffee_chat", "detail": f"no source link: {snippet}..."})
        nums = ANY_NUMBER.findall(_number_text(p))
        if not nums:
            problems.append({"kind": "coffee_chat", "detail": f"no number: {snippet}..."})
        for n in nums:
            if not NUMBER.fullmatch(n) and not re.search(
                    r"(?<![\d.])" + re.escape(n.lower()) + r"(?!\d|[.,]\d)", corpus):
                problems.append({"kind": "unsourced_number",
                                 "detail": f"Coffee chat talking points: {n}"})
        low = p.lower()
        for c in CLICHES:
            if c in low:
                problems.append({"kind": "coffee_chat", "detail": f"cliche {c!r}: {snippet}..."})
    return problems


# "What it means" must not call rates calm when a rates row moved >= 15 bps.
CALM_WORDS = re.compile(r"\b(?:barely|little[- ]changed|hardly|unchanged|flat|steady|"
                        r"did(?:n't| not) move|stood still)\b", re.I)
# How "What it means" may name each big mover (fact sheet `big_movers` labels).
MOVER_WORDS = {"10-Year Treasury": r"10-year|10Y|ten-year", "5-Year Treasury": r"5-year|5Y|five-year",
               "2-Year Treasury": r"2-year|2Y|two-year", "10Y-2Y curve": r"curve",
               "SOFR": r"SOFR", "Fed Funds": r"fed funds|federal funds",
               "30-Year Mortgage": r"mortgage"}


def _check_big_movers(sections, factsheet) -> list[dict]:
    movers = factsheet.get("big_movers") or []
    if not movers:
        return []
    body = dict(sections).get("The Numbers", "")
    line = next((ln for ln in body.splitlines() if "What it means" in ln), "")
    text = _plain(line)
    missed = [m for m in movers
              if not re.search(MOVER_WORDS.get(m, re.escape(m)), text, re.I)]
    if CALM_WORDS.search(text) and missed:
        return [{"kind": "big_move_ignored",
                 "detail": f"What it means calls rates calm but {', '.join(missed)} moved 15+ bps"}]
    return []


def _bps(raw) -> int | None:
    m = re.match(r"\s*([+-]?\d+)\s*bps", str(raw or ""))
    return int(m.group(1)) if m else None


def _check_curve(factsheet: dict) -> list[dict]:
    """The 10Y-2Y curve change should equal the 10Y change minus the 2Y change (within
    1 bp of rounding) when all three are from the same date."""
    v = factsheet.get("values") or {}
    dates = {v.get(f"{k}_DATE") for k in ("DGS10", "DGS2", "T10Y2Y")}
    if len(dates) != 1 or None in dates:
        return []
    c, a, b = _bps(v.get("T10Y2Y_CHG")), _bps(v.get("DGS10_CHG")), _bps(v.get("DGS2_CHG"))
    if None in (a, b, c) or abs(c - (a - b)) <= 1:
        return []
    return [{"kind": "curve_mismatch",
             "detail": f"10Y-2Y change {c:+d} bps vs 10Y {a:+d} minus 2Y {b:+d}"}]


def _check_stale(factsheet: dict) -> list[dict]:
    """A series whose latest observation is older than its cadence allows (see
    src/freshness.py). Core rates (10Y, SOFR) missing entirely are also flagged."""
    asof = factsheet.get("as_of_dates")
    if asof is None:
        return []  # fact sheet built before this check existed
    try:
        run = date.fromisoformat(str(factsheet.get("date")))
    except ValueError:
        return []
    problems = [{"kind": "stale_data", "detail": f"{name} last updated {d}"}
                for name, d in stale_series(asof, run)]
    problems += [{"kind": "stale_data", "detail": r}
                 for r in core_stale(asof, run) if r.endswith("has no data")]
    return problems


def check_issue(md: str, factsheet: dict, banned: list[str]) -> list[dict]:
    problems: list[dict] = []
    sections = _sections(md)

    flat = md.replace("’", "'")
    for phrase in banned:
        if re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", flat, re.I):
            problems.append({"kind": "banned", "detail": phrase})

    for m in SLOP_PATTERN.finditer(_plain(md)):
        problems.append({"kind": "slop_pattern", "detail": m.group(0)})

    dashes = md.count("\u2014") + md.count("\u2013")  # em dash + en dash
    if dashes > MAX_EM_DASHES:
        problems.append({"kind": "em_dash",
                         "detail": f"{dashes} em/en dashes (use commas or periods instead)"})

    # Comments (even unclosed ones) are reported as leftover_comment, not exclaim.
    for line in COMMENT.sub(" ", md).replace("<!--", " ").splitlines():
        if "!" in _plain(line):
            problems.append({"kind": "exclaim", "detail": line.strip()})

    problems += _check_sentences(md)

    for name, body in sections:
        budget = BUDGETS.get(name)
        if budget is None:
            continue
        n = _words(_plain(body.replace(FOOTER, " ")))  # footer trails the last section
        if n > budget * BUDGET_SLACK:
            problems.append({"kind": "budget", "detail": f"{name}: {n} words (budget {budget})"})

    for s in all_stories(factsheet):
        if s["url"] not in md:
            problems.append({"kind": "missing_link", "detail": s["url"]})

    problems += _check_numbers(sections, factsheet)
    problems += _check_contradictions(sections, factsheet)
    problems += _check_repeats(sections)
    problems += _check_coffee(md, factsheet)
    problems += _check_big_movers(sections, factsheet)
    problems += _check_curve(factsheet)
    problems += _check_stale(factsheet)

    if (factsheet.get("day_type") == "sunday" and "WEEK_AHEAD" in factsheet.get("values", {})
            and "WEEK_AHEAD" not in PLACEHOLDER.findall(md)):
        problems.append({"kind": "missing_placeholder", "detail": "WEEK_AHEAD"})

    if factsheet.get("day_type") in REQUIRED_DAYS:
        present = set(PLACEHOLDER.findall(md))
        for name in REQUIRED_PLACEHOLDERS:
            if name not in present:
                problems.append({"kind": "missing_placeholder", "detail": name})

    lines = [ln.strip() for ln in md.splitlines() if ln.strip()]
    if not lines or lines[-1] != FOOTER:
        problems.append({"kind": "footer", "detail": "footer missing or not the last line"})

    for name in empty_headings(md):
        problems.append({"kind": "empty_heading", "detail": name})

    if "<!--" in md:
        problems.append({"kind": "leftover_comment", "detail": "template comment left in draft"})

    return problems
