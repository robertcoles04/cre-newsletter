"""Draft and edit the issue with Claude. The model only ever sees placeholder NAMES,
never the market numbers, so it cannot misquote them; src/fill.py fills them later."""

import json
import re
from pathlib import Path

from src.checks import BUDGETS, CLICHES, FOOTER, load_banned
from src.llm import MODEL_WRITE, run_claude

ROOT = Path(__file__).resolve().parent.parent


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf8")


CLICHE_LIST = ", ".join(f'"{c}"' for c in CLICHES)

# House tone for every explanation sentence (Quick Hits, Market Watch, The Brief...).
TONE = ("in plain English for a smart college student new to CRE: define any term once, "
        "say who gains or loses and why it matters. Simple words, but never babyish or "
        "patronizing (no 'fancy', no 'big companies like to')")


def _sheet_for_model(factsheet: dict) -> str:
    # Numbers stay out of the model's view: values are placeholders only, and reit_moves
    # is chart data for code.
    sheet = {k: v for k, v in factsheet.items() if k not in ("values", "reit_moves")}
    # *_DATE values are machine dates for the page (Data Room "Updated" tags), not prose.
    sheet["placeholders"] = sorted(k for k in factsheet.get("values", {})
                                   if not k.endswith("_DATE"))
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
- Never use em dashes or en dashes; use commas, periods, colons or parentheses instead.
- Tone for every explanation: {TONE}.
- The Brief (first section): exactly 3 bullets, about 15 words each, no numbers, each
  ending with the [source](url) link of the story it summarizes. Those 3 stories still get
  their one full treatment below, but never retell a Brief line word for word.
- No story repeated inside the issue: a story may appear at most twice (its Brief line plus
  ONE full treatment). Never give a story a second full treatment in another section.
- What it means (The Numbers): scale it to the fact sheet's `move_size` for the 10Y. If it
  is "unchanged" or "small", say rates were little changed and explain what the level
  means for borrowers; do not claim a cap-rate impact. Only a "notable" move gets deal
  math (borrowing costs, refi pressure, cap rates). If the fact sheet's `big_movers` list
  is not empty, never say rates "barely moved" or were "little changed" overall: name
  those rows (for example "the 30-year mortgage rate jumped") and say why it matters.
- Coffee chat talking points (after Top Stories): the line "**Coffee chat talking
  points:**" then 2 or 3 bullets. Each bullet is ONE specific sentence a student could
  say to a broker, with exactly ONE concrete number that appears in a linked story's title
  or summary, ending with that story's [source](url). Synthesize across stories (connect
  two items, or tie a story to the market); never just restate one story. No clichés,
  including: {CLICHE_LIST}.
- Quick Hits: at most 6 bullets. Each is the one-line news item with its [source](url)
  link, then ONE explanation sentence (see Tone). No numbers in that sentence.
- Debt Markets: skip novelty-angle stories (sports, playoffs, celebrities). The Distress
  Watch line uses ONLY the fact sheet's `distress` story (never a story from `debt` or
  `top`): restate what it reports, with its link; never assert a trend that no story
  supports. If `distress` is null, omit the Distress Watch line.
- AI in Real Estate: pick a story about an AI tool or use case (underwriting, lease
  abstraction, valuation, property management, leasing chatbots, proptech launches,
  brokerages adopting AI) before data center or power-grid news, and say in plain English
  what the tool does and who uses it. If the chosen story is infrastructure (data
  centers, power, chips) rather than an AI tool or use case, the heading reads
  "## AI Infrastructure" today.
- Term of the Day: only tie the term to a story if the mechanism truly applies;
  otherwise use a standalone example with round illustrative figures.
- REIT movers (The Numbers): for each mover in `mover_news`, if it has a story write ONE
  plain-English sentence on the likely reason, citing it as [source](url), no numbers; if
  it is null use its note placeholder ({{{{MOVER_UP_NOTE}}}} / {{{{MOVER_DOWN_NOTE}}}}) and
  do not guess a reason.
- Market Watch: under each "### " region subhead, retell that region's story from
  `markets` as a short paragraph (2 or 3 sentences in our own words, never a bullet or a
  bare link) ending with its [source](url) link, then a separate line starting
  "**Why it matters:**" with one plain-English sentence (same layout as Top Stories). Delete the subhead
  of a region that is null, and the whole section if all are null.
- Respect the word budgets:
{_budget_lines()}
- Keep the `## ` headings exactly as in the template (except "## AI Infrastructure" as above). Follow each `<!-- ... -->` comment,
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
- Do not add any number. Remove any market figure that is not a placeholder and not
  written exactly in a fact-sheet story. That means every form: percent, %, pct,
  bp/bps/basis points, $ amounts, bare decimals (like 4.62) and odds (like 81 to 19).
- Term of the Day may use a made-up round-number example, but never a market rate.
- If a claim seems doubtful but may be right, keep it and put [CHECK] right after it.
- Keep sentences under 30 words, no exclamation points.
- Never use em dashes or en dashes; use commas, periods, colons or parentheses instead.
- Tone for every explanation: {TONE}.
- The Brief: keep 3 bullets, about 15 words each, no numbers, each ending with its link.
- Quick Hits: at most 6 bullets, each the news line with its link plus ONE explanation
  sentence. No numbers in that sentence.
- What it means: if the fact sheet's `move_size` is "unchanged" or "small", it must not
  claim a cap-rate impact; it says rates were little changed and what the level means.
  If `big_movers` is not empty, it must not call rates calm; it names those rows.
- Coffee chat talking points: 2 or 3 bullets, each one specific sentence with ONE number
  found in a linked story, ending with that story's link; they synthesize across stories
  rather than restate one. Cut clichés ({CLICHE_LIST}).
- No story repeated: a story may appear in its Brief line plus ONE full treatment only.
  Cut any third mention, and never retell a Brief line word for word.
- Debt Markets: cut novelty-angle stories; the Distress Watch line must restate the
  fact sheet's `distress` story (not one already in Debt Markets), never an unsupported
  trend. If `distress` is null, delete the Distress Watch line.
- Term of the Day: keep a story tie-in only if the mechanism truly applies; otherwise a
  standalone example with round illustrative figures.
- AI in Real Estate: keep the focus on what the AI tool does and who uses it. If the
  story is AI infrastructure (data centers, power, chips), the heading is
  "## AI Infrastructure".
- Remove these phrases if present:
{banned}
- Keep the `## ` headings (an "## AI Infrastructure" heading stays as is) and keep this
  footer as the last line: {FOOTER}
- Return the full edited issue Markdown only. No notes, no code fences.

# Fact sheet

```json
{_sheet_for_model(factsheet)}
```

# Draft

{md}
"""
    return _strip_fences(run(prompt, MODEL_WRITE))
