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
FALLBACK_PREFIX = "# Claude unavailable"
BOM = "\ufeff"
COMMENT = re.compile(r"<!--.*?-->", re.S)
AUTOLINK = re.compile(r"<https?://[^<>\s]*>", re.I)  # <https://...> is allowed
RAW_HTML = re.compile(r"<[A-Za-z/!?]")
# [text](javascript:...), ![alt]( DATA:...), [ref]: vbscript:...
UNSAFE_LINK = re.compile(
    r"(\]\(\s*<?|^\s*\[[^\]]*\]:\s*<?)\s*(javascript|data|vbscript)\s*:", re.I)


def _snip(line: str) -> str:
    return line.strip()[:80]


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
        ("a link or image points at a javascript:, data: or vbscript: address",
         lambda s: UNSAFE_LINK.search(s)),
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
    if not (issues / f"{day}.json").exists():
        reasons.append(f"issues/{day}.json not found "
                       "(drafts from before 2026-10-05 cannot be published)")
    return reasons


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
