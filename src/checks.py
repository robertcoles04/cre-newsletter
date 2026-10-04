"""Deterministic slop and number checks on a drafted issue (before placeholders are filled).

check_issue returns a list of {kind, detail} problems; an empty list means clean.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FOOTER = "For informational purposes only. Not investment advice."
REQUIRED_PLACEHOLDERS = ("DGS10", "DGS10_CHG", "SOFR", "FED_TOP", "VNQ")
REQUIRED_DAYS = ("weekday", "friday")
MAX_EM_DASHES = 2
MAX_SENTENCE_WORDS = 30
BUDGET_SLACK = 1.3
BUDGETS = {
    "The Numbers": 100, "Debt Markets": 200, "Top Stories": 500, "Quick Hits": 100,
    "AI in Real Estate": 80, "Term of the Day": 50, "Week in Review": 200,
    "AI in Real Estate Weekly": 150, "REIT Weekly": 200, "Week Ahead": 120,
}
# Market-data sections: every number there must come from a placeholder.
MARKET_SECTIONS = {"The Numbers", "REIT Weekly", "Week Ahead"}
# Term of the Day may use a made-up worked example, so its numbers are not checked.
UNCHECKED_SECTIONS = {"Term of the Day"}
STORY_KEYS = ("top", "quick_hits", "debt", "ai", "week_top", "ai_week")

PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")
COMMENT = re.compile(r"<!--.*?-->", re.S)
IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
URL = re.compile(r"https?://\S+")
# The brief's patterns: `\d+(\.\d+)?\s?(%|bps|basis points)` and `\$\d`. The dollar
# form is widened to the whole amount so the sourced-number lookup compares
# "$120 million", not just "$1".
NUMBER = re.compile(
    r"\d+(?:\.\d+)?\s?(?:%|bps\b|basis points)"
    r"|\$\d+(?:,\d{3})*(?:\.\d+)?(?:\s?(?:million|billion|bn|mm|m|b|k)\b)?",
    re.I)
LIST_ITEM = re.compile(r"^(?:[-*+]|\d+[.)])\s+")
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


def _no_numbers_source(text: str) -> str:
    """Text the number check scans: drops placeholders as well as URLs/link targets."""
    return _plain(PLACEHOLDER.sub(" ", text))


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
             for key in STORY_KEYS for s in factsheet.get(key) or []]
    return _norm(" ".join(parts))


def _sourced(number: str, corpus: str) -> bool:
    pattern = r"(?<![\d.])" + re.escape(_norm(number)) + r"(?!\d|[.,]\d)"
    return re.search(pattern, corpus) is not None


def _check_numbers(sections, factsheet) -> list[dict]:
    corpus = _story_corpus(factsheet)
    problems = []
    for name, body in sections:
        if name in UNCHECKED_SECTIONS:
            continue
        for m in NUMBER.finditer(_no_numbers_source(body)):
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


def check_issue(md: str, factsheet: dict, banned: list[str]) -> list[dict]:
    problems: list[dict] = []
    sections = _sections(md)

    flat = md.replace("’", "'")
    for phrase in banned:
        if re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", flat, re.I):
            problems.append({"kind": "banned", "detail": phrase})

    dashes = md.count("—")
    if dashes > MAX_EM_DASHES:
        problems.append({"kind": "em_dash", "detail": f"{dashes} em dashes (max {MAX_EM_DASHES})"})

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

    for key in ("top", "quick_hits"):
        for s in factsheet.get(key) or []:
            if s["url"] not in md:
                problems.append({"kind": "missing_link", "detail": s["url"]})

    problems += _check_numbers(sections, factsheet)

    if factsheet.get("day_type") in REQUIRED_DAYS:
        present = set(PLACEHOLDER.findall(md))
        for name in REQUIRED_PLACEHOLDERS:
            if name not in present:
                problems.append({"kind": "missing_placeholder", "detail": name})

    lines = [ln.strip() for ln in md.splitlines() if ln.strip()]
    if not lines or lines[-1] != FOOTER:
        problems.append({"kind": "footer", "detail": "footer missing or not the last line"})

    if "<!--" in md:
        problems.append({"kind": "leftover_comment", "detail": "template comment left in draft"})

    return problems
