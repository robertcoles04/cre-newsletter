# cre-newsletter

Daily commercial real estate (CRE) newsletter for Substack. A scheduled Python pipeline collects news + data, Claude drafts the issue, Robert (the owner, the user) reviews and publishes. Full original spec: `BRIEF.md`. Design spec: `docs/superpowers/specs/2026-10-04-cre-newsletter-design.md`.

## Status
Design/brainstorming stage (2026-10-04). No pipeline code yet. Phase 1 = MVP (RSS + FRED -> SQLite -> Claude draft -> email Markdown).

## Decisions so far (override BRIEF.md where they differ)
- Runs 7 days: Mon-Fri detailed (~1,000 words), Sat/Sun lighter (~500).
- Sections: The Numbers (+ daily REIT move, Chart of the Day), Debt Markets (+ Distress Watch), Top Stories, AI in Real Estate (only when real news), Term of the Day. Fri: Deals of the Week. Sat: Week in Review, AI in RE Weekly, Market Spotlight. Sun: REIT Weekly, Week Ahead, Careers Corner.
- Everything free for now; no paid tier.
- Runtime: Python on GitHub Actions cron; Claude steps via Claude Code headless on Robert's Max subscription (CLAUDE_CODE_OAUTH_TOKEN), no API key.
- Blocked feeds (The Real Deal, GlobeSt, Multi-Housing News, CPE, CoStar) -> Google News `site:` queries, headline only. Never bypass bot protection.
- No X via browser cookies; no LinkedIn scraping. Gmail ingestion deferred.

## Guardrails (non-negotiable)
- Pipeline saves drafts only. Never publishes or sends to subscribers.
- Rates, bps changes, REIT prices: inserted by code from APIs, never written by the model.
- Deal numbers must appear in source text, else blank or `[CHECK]`.
- Summarize in our own words, link every claim, max one short quote per source.
- Secrets in `.env` only (see `.env.example`). Never commit keys or the SQLite DB.
- Ask Robert before adding any paid service or API.

## Conventions
- Python 3.11+, SQLite, no web framework. Bash on Windows (Git Bash).
- Robert is a college student: explain decisions in plain English.
