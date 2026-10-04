# CRE Newsletter (brand name TBD)

A daily commercial real estate briefing for Substack. Every morning a program gathers rates, debt news, deals, REIT moves and top stories, and Claude writes a draft. I review it and hit publish.

**Status:** planning. The original idea is in [`BRIEF.md`](BRIEF.md); the approved design will live in `docs/superpowers/specs/`.

## How it will work
1. A scheduled job (GitHub Actions) runs early each morning.
2. It pulls news feeds, Treasury/SOFR rates (FRED), Fed odds (Polymarket) and REIT prices.
3. Everything is saved to a small database (SQLite) and duplicate stories are merged.
4. Claude drafts the issue; code fills in every number so nothing is made up.
5. The draft is emailed to me (later: saved straight to Substack as a draft).
6. I review for ~15 minutes and publish.

## Setup
Coming in Phase 1. You will need a free FRED API key and an Anthropic API key in a `.env` file.
