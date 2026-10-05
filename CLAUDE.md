# cre-newsletter

CRE Blurb: daily commercial real estate (CRE) newsletter for Substack. A scheduled Python pipeline collects news + data, Claude drafts the issue, Robert (the owner, the user) reviews and publishes. Full original spec: `BRIEF.md`. Design spec: `docs/superpowers/specs/2026-10-04-cre-newsletter-design.md`.

## Status
Phase 1 is merged (pending secrets + first scheduled run). Website publishing is built (GitHub Pages; approve a draft Issue by adding the `approved` label, workflow `.github/workflows/publish.yml`), pending going public + enabling Pages. Phase 1 = MVP (feeds + FRED + Polymarket + REIT strip -> SQLite -> Claude draft -> GitHub Issue delivery). Workflow: `.github/workflows/daily.yml` (cron 09:00 + 10:00 UTC, DB kept on the `data` branch).

## Commands
- Setup: `python -m venv .venv && .venv/Scripts/python -m pip install -r requirements.txt`; copy `.env.example` to `.env`.
- Test: `.venv/Scripts/python -m pytest`
- Local run: `.venv/Scripts/python -m src.main --force --dry-run` (flags: `--date`, `--dry-run`, `--force`, `--db`)
- CI: `gh workflow run daily.yml -f force=true`, then `gh run watch`.

## Decisions so far (override BRIEF.md where they differ)
- Runs 7 days: Mon-Fri detailed (~1,000 words), Sat/Sun lighter (~500).
- Sections: The Numbers (+ daily REIT move, Chart of the Day), Debt Markets (+ Distress Watch), Top Stories, Quick Hits, AI in Real Estate (only when real news), Term of the Day. Fri: Deals of the Week. Sat: Week in Review, AI in RE Weekly, Market Spotlight. Sun: REIT Weekly, Week Ahead, Careers Corner.
- Everything free for now; no paid tier.
- Runtime: Python on GitHub Actions cron; Claude steps via Claude Code headless on Robert's Max subscription (CLAUDE_CODE_OAUTH_TOKEN), no API key.
- Blocked feeds (The Real Deal, GlobeSt, Multi-Housing News, CPE, CoStar) -> Google News `site:` queries, headline only. Never bypass bot protection.
- No X via browser cookies; no LinkedIn scraping. Gmail ingestion deferred.

## Guardrails (non-negotiable)
- The 5 AM pipeline saves drafts only. Only the label-triggered publish workflow puts an approved issue on the website; nothing is emailed.
- Rates, bps changes, REIT prices: inserted by code from APIs, never written by the model.
- Deal numbers must appear in source text, else blank or `[CHECK]`.
- Summarize in our own words, link every claim, max one short quote per source.
- Secrets in `.env` only (see `.env.example`). Never commit keys or the SQLite DB.
- Ask Robert before adding any paid service or API.

## Conventions
- Python 3.11+, SQLite, no web framework. Bash on Windows (Git Bash).
- Robert is a college student: explain decisions in plain English.
