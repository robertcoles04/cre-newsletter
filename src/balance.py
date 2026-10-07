"""Layout safety net: Debt Markets and Market Watch sit side by side on the page, so a
Debt Markets that is much shorter than Market Watch leaves a big empty gap. If that
happens, ask Claude for one more debt item (a spare story from the fact sheet) and
insert it. Runs on the PRE-fill Markdown (it still holds {{PLACEHOLDERS}})."""

import math
import re

from src.draft import TONE, _read, _strip_fences
from src.llm import MODEL_WRITE

WORDS_PER_LINE = 9        # a ~60 character column (the default)
# The page puts Debt Markets in the wider (7 of 12) column and Market Watch in the
# narrower (5 of 12) one, so the same words take more lines in Market Watch.
DEBT_WORDS_PER_LINE = 11
WATCH_WORDS_PER_LINE = 8
BLOCK_SPACING = 1.2       # paragraph / bullet gap, in text lines
SUBHEAD_LINES = 2.5       # a "### " subhead with its margins
BALANCED_RATIO = 0.8      # Debt Markets height must be at least this x Market Watch height
DASHES = (chr(0x2014), chr(0x2013))  # em dash, en dash

_LINK = re.compile(r"\[([^\]\n]*)\]\([^)\s]*\)")
_URL = re.compile(r"https?://\S+")
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_H2 = re.compile(r"^## +(.+?)\s*$", re.M)


def _section(md: str, name: str) -> tuple[int, int] | None:
    """(start, end) character offsets of a section's body, or None if it is missing."""
    heads = list(_H2.finditer(md))
    for i, m in enumerate(heads):
        if m.group(1).strip() == name:
            end = heads[i + 1].start() if i + 1 < len(heads) else len(md)
            return m.end(), end
    return None


def _word_count(text: str) -> int:
    text = _LINK.sub(r"\1", _COMMENT.sub(" ", text))  # link text counts, URLs do not
    text = _URL.sub(" ", text).replace("**", " ")
    return sum(1 for tok in text.split() if re.search(r"\w", tok))


def estimate_height(section_md: str, words_per_line: float = WORDS_PER_LINE) -> float:
    """Rough rendered height of a section body in text lines (column ~60 characters, ~9
    words per line by default). Each paragraph or bullet block is ceil(words / 9) lines plus
    ~1.2 lines of spacing; each "### " subhead is ~2.5 lines."""
    lines = 0.0
    for block in re.split(r"\n\s*\n", _COMMENT.sub(" ", section_md)):
        block = block.strip()
        if not block or block.startswith("## ") or block.startswith("!["):
            continue
        if block.startswith("### "):
            lines += SUBHEAD_LINES
            continue
        n = _word_count(block)
        if n:
            lines += math.ceil(n / words_per_line) + BLOCK_SPACING
    return lines


def _heights(md: str) -> tuple[float, float] | None:
    debt, watch = _section(md, "Debt Markets"), _section(md, "Market Watch")
    if not debt or not watch:
        return None
    return (estimate_height(md[debt[0]:debt[1]], DEBT_WORDS_PER_LINE),
            estimate_height(md[watch[0]:watch[1]], WATCH_WORDS_PER_LINE))


def _prompt(story: dict) -> str:
    return f"""{_read("config/voice.md")}

# Task

Write ONE extra item for the Debt Markets section of today's CRE Blurb, from the story
below. Output exactly two parts and nothing else (no heading, no code fence, no commentary):

1. A short paragraph (2 or 3 sentences, in your own words, not a bare link) that ends with
   [{story["source"]}]({story["url"]}).
2. A blank line, then one line starting "**Why it matters:**" with ONE plain-English
   sentence, {TONE}.

Rules:
- Use only what the story's title and summary say. A number (a deal size, a loan amount) may
  appear only exactly as written in the title or summary; otherwise use no numbers.
- Keep every sentence under 30 words. No exclamation points.
- Never use em dashes or en dashes; use commas, periods, colons or parentheses instead.
- Do not write any {{{{PLACEHOLDER}}}} and do not write any "#" heading.

Story:
Title: {story["title"]}
Source: {story["source"]}
URL: {story["url"]}
Summary: {story.get("summary", "")}
"""


def _valid(item: str, url: str) -> str | None:
    """None when the item is usable, else why it was rejected."""
    if url not in item:
        return "the story link is missing"
    if any(ln.lstrip().startswith("#") for ln in item.splitlines()):
        return "it contains a heading"
    if "{{" in item:
        return "it contains a placeholder"
    if any(d in item for d in DASHES):
        return "it contains an em or en dash"
    if "**Why it matters:**" not in item:
        return 'the "Why it matters" line is missing'
    return None


def _insert(md: str, item: str) -> str:
    """Put the item in Debt Markets: before the Distress Watch line, else at the end."""
    start, end = _section(md, "Debt Markets")
    body = md[start:end]
    m = re.search(r"^\*\*Distress Watch:\*\*", body, re.M)
    if m:
        body = body[:m.start()] + item + "\n\n" + body[m.start():]
    else:
        body = body.rstrip("\n") + "\n\n" + item + "\n\n"
    return md[:start] + body + md[end:]


def balance_debt(md: str, factsheet: dict, run, problems: list, max_adds: int = 2) -> str:
    """Add up to `max_adds` spare debt stories to Debt Markets while it is much shorter
    than Market Watch. `run(prompt, model)` is the same Claude runner draft.py uses.
    Never raises: failures become "layout: ..." lines in `problems` and `md` is kept."""
    for _ in range(max_adds):
        h = _heights(md)
        if h is None or h[0] >= BALANCED_RATIO * h[1]:
            return md
        spare = next((s for s in factsheet.get("debt", []) if s["url"] not in md), None)
        if spare is None:
            problems.append("layout: Debt Markets is much shorter than Market Watch and no "
                            "spare debt story was available")
            return md
        try:
            item = _strip_fences(run(_prompt(spare), MODEL_WRITE)).strip()
        except Exception as e:  # noqa: BLE001 - a layout nicety must never fail the issue
            problems.append(f"layout: debt balance skipped ({e})")
            return md
        why = _valid(item, spare["url"])
        if why:
            problems.append(f"layout: debt balance item rejected ({why})")
            return md
        md = _insert(md, item)
    return md
