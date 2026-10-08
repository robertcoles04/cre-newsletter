"""Deterministic tidy-up of an issue's Markdown so empty template scaffolding never
reaches the site: empty "### " subheads, "## " sections left with no body, and
"**Why ... moved:**" lines with no reason (or for an n/a mover)."""

import re

FOOTER = "For informational purposes only. Not investment advice."
COMMENT = re.compile(r"<!--.*?-->", re.S)
HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
EMPTY_WHY = re.compile(r"^\s*\*\*Why .* moved:\*\*\s*(?:n/a)?\s*$")
NA_WHY = re.compile(r"\*\*Why n/a moved:\*\*", re.I)


def strip_chatter(md: str) -> str:
    """Drop anything a model wrote outside the issue itself: text before the first heading
    (e.g. "Here is the edited issue. I reviewed...") and text after the footer line."""
    lines = md.split("\n")
    first = next((i for i, ln in enumerate(lines) if HEADING.match(ln)), None)
    if first is not None:
        # Keep "[CHECK] ..." markers (e.g. an editor hold): they block publishing on purpose.
        holds = [ln for ln in lines[:first] if ln.lstrip().startswith("[CHECK]")]
        lines = holds + ([""] if holds else []) + lines[first:]
    if FOOTER in (ln.strip() for ln in lines):
        last = max(i for i, ln in enumerate(lines) if ln.strip() == FOOTER)
        lines = lines[:last + 1]
    return "\n".join(lines).rstrip("\n") + "\n"


def _is_body(line: str) -> bool:
    s = COMMENT.sub("", line).strip()
    return bool(s) and s != FOOTER


def _empty_at(lines: list[str], level: int) -> list[tuple[int, int]]:
    """(start, end) line ranges of headings of exactly `level` whose body, up to the next
    heading of the same or higher level, has no content (deeper headings count)."""
    spans = []
    for i, line in enumerate(lines):
        m = HEADING.match(line)
        if not m or len(m.group(1)) != level:
            continue
        j, has_body = i + 1, False
        while j < len(lines):
            n = HEADING.match(lines[j])
            if n and len(n.group(1)) <= level:
                break
            has_body = has_body or bool(n) or _is_body(lines[j])
            j += 1
        if not has_body:
            spans.append((i, j))
    return spans


def _drop_empty(lines: list[str], level: int) -> list[str]:
    # Drop the heading and its blank/comment lines; a trailing footer line stays.
    drop = {k for a, b in _empty_at(lines, level) for k in range(a, b)
            if k == a or lines[k].strip() != FOOTER}
    return [ln for k, ln in enumerate(lines) if k not in drop]


def empty_headings(md: str) -> list[str]:
    """Names of "##"/"###" headings tidy() would drop (for the editor notes)."""
    lines = COMMENT.sub("", md).splitlines()
    names = []
    for level in (3, 2):
        names += [HEADING.match(lines[a]).group(2) for a, _ in _empty_at(lines, level)]
        lines = _drop_empty(lines, level)
    return names


def tidy(md: str) -> str:
    lines = [ln for ln in md.splitlines()
             if not EMPTY_WHY.match(ln) and not NA_WHY.search(ln)]
    lines = _drop_empty(lines, 3)
    lines = _drop_empty(lines, 2)
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
    return out + ("\n" if md.endswith("\n") else "")
