"""Publish gate: decides whether an approved draft may go on the website.

Usage: python -m src.publish <YYYY-MM-DD> [--root .]
Exit 0 = publish, 1 = refused (reasons on stdout), 2 = bad date.
"""
import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

BANNER = "> **Review before publishing:**"
FALLBACK_HEADING = "# Claude unavailable: fact sheet only"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _snip(line: str) -> str:
    return line.strip()[:80]


def check(md: str) -> list[str]:
    """Plain-English reasons the draft cannot be published; empty when clean."""
    lines = md.replace("\r\n", "\n").split("\n")
    rules = [
        ("a [CHECK] marker is still in the text",
         lambda s: "[check]" in s.lower()),
        ("an unfilled {{placeholder}} is still in the text",
         lambda s: "{{" in s),
        ("this is the fact-sheet-only fallback (Claude was unavailable)",
         lambda s: s.strip() == FALLBACK_HEADING),
    ]
    reasons = []
    for message, hit in rules:
        for line in lines:
            if hit(line):
                reasons.append(f'{message}: "{_snip(line)}"')
                break
    return reasons


def strip_banner(md: str) -> str:
    """Remove the editor-notes banner (and the blank line after it)."""
    lines = md.replace("\r\n", "\n").split("\n")
    out = []
    i = 0
    while i < len(lines):
        if lines[i].startswith(BANNER):
            i += 1
            while i < len(lines) and lines[i].startswith(">"):
                i += 1
            if i < len(lines) and lines[i].strip() == "":
                i += 1
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


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
        md = md_path.read_bytes().decode("utf-8")
        reasons += check(strip_banner(md))
    else:
        reasons.append(f"issues/{day}.md not found")
    if not (issues / f"{day}.json").exists():
        reasons.append(f"issues/{day}.json not found "
                       "(drafts from before 2026-10-05 cannot be published)")
    return reasons


def _valid_date(arg: str) -> bool:
    if not DATE_RE.match(arg):
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
