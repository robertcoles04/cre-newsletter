"""Publish gate: decides whether an approved draft may go on the website.

Usage: python -m src.publish <YYYY-MM-DD> [--root .]
Exit 0 = publish, 1 = refused (reasons on stdout), 2 = bad date.
"""
import argparse
import html
import json
import re
import sys
from datetime import date
from pathlib import Path

from src.freshness import core_stale

BANNER = "> **Review before publishing:**"
FALLBACK_PREFIX = "# Claude unavailable"
BOM = "\ufeff"
COMMENT = re.compile(r"<!--.*?-->", re.S)
AUTOLINK = re.compile(r"<https?://[^<>\s]*>", re.I)  # <https://...> is allowed
RAW_HTML = re.compile(r"<[A-Za-z/!?]")
# Link/image targets: inline [text](target) and reference definitions [id]: target.
INLINE_TARGET = re.compile(r"\]\(\s*(<[^>\n]*>|[^\s)]*)")
REF_TARGET = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*(<[^>\n]*>|\S*)")
SAFE_PREFIXES = ("http://", "https://", "mailto:", "#")
CONTROL_OR_SPACE = re.compile(r"[\x00-\x20\x7f-\x9f]")


def _safe_target(raw: str) -> bool:
    """Allowlist: web/mail links, #anchors, or relative paths (no scheme)."""
    t = CONTROL_OR_SPACE.sub("", html.unescape(raw.strip("<>")))
    if t.lower().startswith(SAFE_PREFIXES):
        return True
    return ":" not in t.split("/", 1)[0]


def _unsafe_link(line: str) -> bool:
    targets = INLINE_TARGET.findall(line) + REF_TARGET.findall(line)
    return any(not _safe_target(t) for t in targets)


def _snip(line: str) -> str:
    return line.strip()[:80]


import re as _re

# A model talking to the editor instead of writing the issue ("Here is the edited issue.
# I reviewed the draft..."). Reader-facing first person ("I'd point out") is not matched.
MODEL_CHATTER = _re.compile(
    r"\bhere (?:is|are) the (?:edited|revised|updated|final|corrected) (?:issue|draft|version)\b"
    r"|\bI (?:have |'ve )?(?:reviewed|edited|trimmed|revised|checked|made)\b"
    r"|\bthe only changes? I\b|\beverything checks out\b|\bthe (?:voice rules|fact sheet)\b",
    _re.I)


def check(md: str) -> list[str]:
    """Plain-English reasons the draft cannot be published; empty when clean."""
    lines = md.lstrip(BOM).replace("\r\n", "\n").split("\n")
    rules = [
        ("a [CHECK] marker is still in the text",
         lambda s: "[check]" in s.lower()),
        ("an unfilled {{placeholder}} is still in the text",
         lambda s: "{{" in s),
        ("this is the fact-sheet-only fallback (Claude was unavailable)",
         lambda s: " ".join(s.split()).startswith(FALLBACK_PREFIX)),
        ("the AI's notes to the editor are in the text, not part of the issue",
         lambda s: not s.lstrip().startswith(">") and MODEL_CHATTER.search(s)),
    ]
    reasons = []
    for message, hit in rules:
        for line in lines:
            if hit(line):
                reasons.append(f'{message}: "{_snip(line)}"')
                break
    # Comments are dropped by the renderer; raw HTML and script links are not.
    visible = COMMENT.sub("", "\n".join(lines)).split("\n")
    html_rules = [
        ("raw HTML is not allowed on the website (use plain markdown)",
         lambda s: RAW_HTML.search(AUTOLINK.sub("", s))),
        ("a link or image must be an http(s)://, mailto:, # or relative address",
         _unsafe_link),
    ]
    for message, hit in html_rules:
        for line in visible:
            if hit(line):
                reasons.append(f'{message}: "{_snip(line)}"')
                break
    return reasons


def strip_banner(md: str) -> str:
    """Remove the editor-notes banner (and the blank line after it).

    Only a banner that is the first non-blank line is stripped; a banner-like
    line anywhere else stays in the text so it gets checked normally.
    """
    text = md.lstrip(BOM).replace("\r\n", "\n")
    lines = text.split("\n")
    i = 0
    while i < len(lines) and lines[i].strip() == "":
        i += 1
    if i >= len(lines) or not lines[i].startswith(BANNER):
        return text
    i += 1
    while i < len(lines) and lines[i].startswith(">"):
        i += 1
    if i < len(lines) and lines[i].strip() == "":
        i += 1
    return "\n".join(lines[i:])


def _published_path(root: Path) -> Path:
    return Path(root) / "issues" / "published.json"


def load_published(root: Path) -> list[str]:
    p = _published_path(root)
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))


def add_published(root: Path, day: str) -> list[str]:
    days = sorted(set(load_published(root)) | {day})
    p = _published_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(days, indent=2), encoding="utf-8")
    return days


def gate(root: Path, day: str) -> list[str]:
    issues = Path(root) / "issues"
    reasons = []
    md_path = issues / f"{day}.md"
    if md_path.exists():
        md = md_path.read_bytes().decode("utf-8-sig")
        reasons += check(strip_banner(md))
    else:
        reasons.append(f"issues/{day}.md not found")
    json_path = issues / f"{day}.json"
    if not json_path.exists():
        reasons.append(f"issues/{day}.json not found "
                       "(drafts from before 2026-10-05 cannot be published)")
    else:
        reasons += _stale_reasons(json_path, day)
    return reasons


def _stale_reasons(json_path: Path, day: str) -> list[str]:
    """Hard stop: refuse when 10-Year or SOFR data is missing or over 5 business days old.
    A json without `as_of_dates` (issues made before this check) is never refused."""
    try:
        data = json.loads(json_path.read_bytes().decode("utf-8-sig"))
    except ValueError:
        return [f"issues/{day}.json is not valid JSON"]
    asof = data.get("as_of_dates") if isinstance(data, dict) else None
    if not isinstance(asof, dict):
        return []
    stale = core_stale(asof, date.fromisoformat(day))
    return [f"core rates are stale: {'; '.join(stale)}"] if stale else []


def _valid_date(arg: str) -> bool:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", arg, re.ASCII):
        return False
    try:
        date.fromisoformat(arg)
    except ValueError:
        return False
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.publish")
    ap.add_argument("date")
    ap.add_argument("--root", default=".")
    args = ap.parse_args(argv)
    if not _valid_date(args.date):
        print(f"not a valid date: {args.date}")
        return 2
    root = Path(args.root)
    reasons = gate(root, args.date)
    if reasons:
        for r in reasons:
            print(r)
        return 1
    add_published(root, args.date)
    print(f"published {args.date}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
