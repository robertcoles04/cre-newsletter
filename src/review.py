"""Editor review: a second Claude pass that fact-checks the drafted prose against its sources.

Runs after draft.edit and before the checks and fill, so the draft still has
{{PLACEHOLDERS}} where code puts the market numbers later. The editor may only cut or
reword (fixes are applied by code, under strict rules) or hold the issue. A hold puts a
"[CHECK] Editor hold: ..." line at the top of the body, which the existing publish gate
blocks on; the owner deletes that line and adds the `approved` label to publish.
"""

import json
import re
from datetime import date
from pathlib import Path

from src.checks import FOOTER
from src.draft import _read, _sheet_for_model
from src.llm import MODEL_WRITE, run_claude
from src.publish import strip_banner

RECENT_CHARS = 6000
VERDICTS = ("approve", "approve_with_fixes", "hold")
PLACEHOLDER = re.compile(r"\{\{[^{}]*\}\}")
LINK_URL = re.compile(r"\]\(\s*<?([^)\s>]+)")
BARE_URL = re.compile(r"https?://\S+")
NUMBER = re.compile(r"\d[\d,.]*")
DASHES = ("\u2013", "\u2014")
ISSUE_FILE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

PROMPT = """{voice}

# Task

You are the fact-check editor. The draft below is about to be published. Check it against
its source stories and the last few issues, then reply with JSON only.

About the draft:
- Every `{{{{NAME}}}}` is a market number that code fills in later. Never judge, change,
  move or remove a placeholder, and never ask for one.
- Each story links to its source with [name](url). The source stories (title, summary,
  url) are in the fact sheet below. It has no market numbers on purpose.

Look for these problems:
1. A headline or "Why it matters" line that claims more than the source title and summary
   support.
2. A fact, name, place or number that is not in the linked source story.
3. Wrong attribution: a claim linked to a story that does not say it, or the wrong
   company, person or source named.
4. A Market Watch story under the wrong region heading.
5. A story or angle repeated from the last 3 issues (below).
6. A Coffee chat talking point that misstates its story.

How to fix:
- Prefer fixes. Each fix replaces one exact piece of the draft: `find` is text copied
  exactly from the draft (long enough to appear only once, never a `#` heading line and
  never the footer), `replace` is the new text, or "" to cut it.
- Fixes may only cut or reword. Never add a number, a link, a placeholder or a new fact.
  Keep every placeholder and link that is inside `find` in `replace` unchanged, or cut a
  piece that has no placeholder.
- Never use em dashes or en dashes; use commas, periods, colons or parentheses.
- Use "hold" only for a problem a cut or reword cannot fix, for example the main story
  is misread, or the whole issue repeats yesterday's. A hold needs short `reasons`.
- If nothing is wrong, approve with no fixes. Do not fix style or tone; only accuracy
  and repeats.

Reply with this JSON object and nothing else:
{{"verdict": "approve" | "approve_with_fixes" | "hold",
 "fixes": [{{"find": "exact text from the draft", "replace": "new text or empty", "why": "short reason"}}],
 "reasons": ["short reason, required for hold"]}}

# Fact sheet (source stories)

```json
{sheet}
```

# Last issues (newest first, for repeat checks)

{recent}

# Draft

{md}
"""


def recent_issues(repo_root, run_date, n: int = 3) -> list[str]:
    """The last n issues/YYYY-MM-DD.md before run_date (newest first), banner stripped,
    each cut to about RECENT_CHARS characters."""
    if isinstance(run_date, str):
        run_date = date.fromisoformat(run_date)
    found = []
    for path in (Path(repo_root) / "issues").glob("????-??-??.md"):
        if not ISSUE_FILE.match(path.stem):
            continue
        try:
            day = date.fromisoformat(path.stem)
        except ValueError:
            continue
        if day < run_date:
            found.append((day, path))
    out = []
    for _, path in sorted(found, reverse=True)[:n]:
        text = strip_banner(path.read_text(encoding="utf-8-sig")).strip()
        out.append(text[:RECENT_CHARS])
    return out


def _parse(reply: str) -> dict | None:
    """The first JSON object in the reply, validated; None if unusable."""
    start = reply.find("{")
    if start == -1:
        return None
    try:
        data, _ = json.JSONDecoder().raw_decode(reply[start:])
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("verdict") not in VERDICTS:
        return None
    fixes = data.get("fixes") or []
    reasons = data.get("reasons") or []
    if not isinstance(fixes, list) or not isinstance(reasons, list):
        return None
    clean = []
    for f in fixes:
        if (isinstance(f, dict) and isinstance(f.get("find"), str)
                and isinstance(f.get("replace", ""), str)):
            why = f.get("why")
            clean.append({"find": f["find"], "replace": f.get("replace") or "",
                          "why": why if isinstance(why, str) else ""})
    return {"verdict": data["verdict"], "fixes": clean,
            "reasons": [r for r in reasons if isinstance(r, str) and r.strip()]}


def review(md: str, factsheet: dict, recent: list[str], run=run_claude) -> dict:
    """Ask the editor. Raises LLMError on a failed call and ValueError on a reply that is
    not the expected JSON."""
    if recent:
        past = "\n\n".join(f"## Previous issue {i} {'(newest)' if i == 1 else ''}".rstrip()
                           + f"\n\n{text}" for i, text in enumerate(recent, 1))
    else:
        past = "(none)"
    prompt = PROMPT.format(voice=_read("config/voice.md"), sheet=_sheet_for_model(factsheet),
                           recent=past, md=md)
    parsed = _parse(run(prompt, MODEL_WRITE))
    if parsed is None:
        raise ValueError("reply was not the expected JSON")
    return parsed


def _strip_refs(text: str) -> str:
    """Text without placeholders and URLs, so their digits are not counted as numbers."""
    text = PLACEHOLDER.sub(" ", text)
    text = LINK_URL.sub("](", text)
    return BARE_URL.sub(" ", text)


def _numbers(text: str) -> list[str]:
    return [n.rstrip(",.") for n in NUMBER.findall(_strip_refs(text))]


def _sub_multiset(small: list, big: list) -> bool:
    pool = list(big)
    for x in small:
        if x not in pool:
            return False
        pool.remove(x)
    return True


def _reject_reason(md: str, find: str, replace: str) -> str | None:
    if not find.strip():
        return "empty find"
    count = md.count(find)
    if count == 0:
        return "text not found"
    if count > 1:
        return f"text found {count} times"
    if any(ln.lstrip().startswith("#") for ln in find.split("\n")):
        return "touches a heading"
    if FOOTER in find:
        return "touches the footer"
    if any(ln.lstrip().startswith("#") for ln in replace.split("\n")):
        return "adds a heading"
    if any(d in replace for d in DASHES):
        return "uses a dash"
    if "[check]" in replace.lower() and "[check]" not in find.lower():
        return "adds a [CHECK] marker"
    if set(PLACEHOLDER.findall(replace)) != set(PLACEHOLDER.findall(find)):
        return "changes a placeholder"
    if "{{" in PLACEHOLDER.sub("", replace) or "}}" in PLACEHOLDER.sub("", replace):
        return "changes a placeholder"
    if not set(LINK_URL.findall(replace)) <= set(LINK_URL.findall(find)):
        return "adds a link"
    if not _sub_multiset(_numbers(replace), _numbers(find)):
        return "adds a number"
    return None


def apply_fixes(md: str, fixes: list[dict]) -> tuple[str, list[dict], list[tuple[dict, str]]]:
    """Apply the safe fixes in order. Returns (md, applied, [(fix, reason) rejected])."""
    applied, rejected = [], []
    for fix in fixes:
        find, replace = fix.get("find", ""), fix.get("replace", "") or ""
        reason = _reject_reason(md, find, replace)
        if reason:
            rejected.append((fix, reason))
            continue
        md = md.replace(find, replace, 1)
        if not replace:
            md = re.sub(r"\n{3,}", "\n\n", md)
        applied.append(fix)
    return md, applied, rejected


def _plain(text: str, limit: int = 160) -> str:
    """One safe line for the banner or the hold line: no newlines, dashes, braces or tags."""
    text = re.sub(r"\s*[\u2013\u2014]\s*", ", ", text)
    text = re.sub(r"[{}<>]", "", text)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 3].rstrip() + "..."


def _insert_hold(md: str, reasons: list[str]) -> str:
    line = "[CHECK] Editor hold: " + "; ".join(_plain(r, 200) for r in reasons)
    return line + "\n\n" + md.lstrip("\n")


def run_review(md: str, factsheet: dict, repo_root, run_date, run, problems: list[str]) -> str:
    """Review the draft and return it, fixed and/or held. Never raises: on any failure it
    adds an "editor: review unavailable" problem and returns the draft unchanged."""
    try:
        recent = recent_issues(repo_root, run_date)
        result = review(md, factsheet, recent, run=run)
    except Exception as exc:
        msg = str(exc) or type(exc).__name__
        problems.append(f"editor: review unavailable ({_plain(msg, 120)})")
        return md
    verdict = result["verdict"]
    if verdict == "approve":
        return md
    if verdict == "hold":
        reasons = result["reasons"] or ["no reason given"]
        problems.extend(f"editor: hold: {_plain(r)}" for r in reasons)
    md, applied, rejected = apply_fixes(md, result["fixes"])
    problems.extend(f"editor: fixed: {_plain(f['why'] or 'no reason given')}" for f in applied)
    problems.extend(f"editor: fix not applied ({reason}): {_plain(f.get('find', ''), 60)}"
                    for f, reason in rejected)
    if verdict == "hold":
        md = _insert_hold(md, reasons)
    return md
