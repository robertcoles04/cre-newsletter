"""Draft and edit the issue with Claude. The model only ever sees placeholder NAMES,
never the market numbers, so it cannot misquote them; src/fill.py fills them later."""

import json
import re
from pathlib import Path

from src.checks import BUDGETS, FOOTER, load_banned
from src.llm import MODEL_WRITE, run_claude

ROOT = Path(__file__).resolve().parent.parent


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf8")


def _sheet_for_model(factsheet: dict) -> str:
    sheet = {k: v for k, v in factsheet.items() if k != "values"}
    sheet["placeholders"] = sorted(factsheet.get("values", {}))
    return json.dumps(sheet, indent=2, ensure_ascii=False)


def _budget_lines() -> str:
    return "\n".join(f"- {name}: about {words} words" for name, words in BUDGETS.items())


def _strip_fences(text: str) -> str:
    text = text.strip()
    m = re.fullmatch(r"```[\w-]*\n(.*?)\n?```", text, re.S)
    return (m.group(1).strip() if m else text) + "\n"


def write(factsheet: dict, run=run_claude) -> str:
    template = _read(f"templates/{factsheet['day_type']}.md")
    banned = "\n".join(f"- {p}" for p in load_banned())
    prompt = f"""{_read("config/voice.md")}

# Task

Write today's CRE Blurb issue in Markdown by filling in the template below, using ONLY
the fact sheet at the end.

Rules:
- Use `{{{{NAME}}}}` for every number from the fact sheet's `placeholders` list (for example
  `{{{{DGS10}}}}`). Code replaces them later. Keep every `{{{{...}}}}` in the template exactly as written.
- Strings inside the fact sheet such as "FED_TOP" or "REITW_BEST_1" are placeholder names:
  write them as `{{{{FED_TOP}}}}`, `{{{{REITW_BEST_1}}}}`.
- Write no other numeric rates, percentages, prices, odds or bps. Deal figures (a sale
  price, a loan size) may appear only exactly as written in a story's title or summary.
- Link each story to its source as [source](url), using the story's `source` and `url`.
- Respect the word budgets:
{_budget_lines()}
- Keep the `## ` headings exactly as in the template. Follow each `<!-- ... -->` comment,
  then delete every comment.
- Never use these phrases:
{banned}
- End with this footer as the last line, exactly: {FOOTER}
- Output only the issue Markdown. No preamble, no code fences.

# Template

{template}

# Fact sheet

```json
{_sheet_for_model(factsheet)}
```
"""
    return _strip_fences(run(prompt, MODEL_WRITE))


def edit(md: str, factsheet: dict, run=run_claude) -> str:
    banned = "\n".join(f"- {p}" for p in load_banned())
    prompt = f"""{_read("config/voice.md")}

# Task

You are the editor. Tighten the draft below against the voice rules and the fact sheet.

Rubric:
- Cut vague or unsupported lines: anything not backed by the fact sheet, generic
  "why it matters" lines with no concrete consequence, filler and throat-clearing.
- Keep every `{{{{PLACEHOLDER}}}}` and every [source](url) link exactly unchanged.
- Do not add any number. Remove any rate, percentage, price, odds or bps that is not a
  placeholder and not written exactly in a fact-sheet story.
- If a claim seems doubtful but may be right, keep it and put [CHECK] right after it.
- Keep sentences under 30 words, no exclamation points, at most two em dashes.
- Remove these phrases if present:
{banned}
- Keep the `## ` headings and keep this footer as the last line: {FOOTER}
- Return the full edited issue Markdown only. No notes, no code fences.

# Fact sheet

```json
{_sheet_for_model(factsheet)}
```

# Draft

{md}
"""
    return _strip_fences(run(prompt, MODEL_WRITE))
