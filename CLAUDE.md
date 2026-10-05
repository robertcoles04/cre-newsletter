# cre-newsletter

CRE Blurb: daily commercial real estate (CRE) newsletter published as a GitHub Pages website (approved via an `approved` label on the draft Issue). A scheduled Python pipeline collects news + data, Claude drafts the issue, Robert (the owner, the user) reviews and publishes. Full original spec: `BRIEF.md`. Design spec: `docs/superpowers/specs/2026-10-04-cre-newsletter-design.md`.

## Status
Phase 1 is merged (pending secrets + first scheduled run). Website publishing is built (GitHub Pages; approve a draft Issue by adding the `approved` label, workflow `.github/workflows/publish.yml`), pending going public + enabling Pages. Phase 1 = MVP (feeds + FRED + Polymarket + REIT strip -> SQLite -> Claude draft -> GitHub Issue delivery). Workflow: `.github/workflows/daily.yml` (cron 09:00 + 10:00 UTC, DB kept on the `data` branch).

## Commands
- Setup: `python -m venv .venv && .venv/Scripts/python -m pip install -r requirements.txt`; copy `.env.example` to `.env`.
- Test: `.venv/Scripts/python -m pytest`
- Local run: `.venv/Scripts/python -m src.main --force --dry-run` (flags: `--date`, `--dry-run`, `--force`, `--db`)
- CI: `gh workflow run daily.yml -f force=true`, then `gh run watch`.

## Decisions so far (override BRIEF.md where they differ)
- Runs 7 days: Mon-Fri detailed (~1,000 words), Sat/Sun lighter (~500).
- Sections (match `templates/`): every day opens with The Brief (3 bullets, ~15 words each, no numbers, each ending with its story link). Mon-Thu: The Numbers (+ REIT movers, Chart of the Day), Debt Markets (+ Distress Watch), Top Stories (+ a "Coffee chat line"), Market Watch, Quick Hits (max 6), AI in Real Estate (heading becomes "AI Infrastructure" when the story is data centers/power/chips; only when real news), Term of the Day. Fri: same plus Deals of the Week. Sat: Week in Review, Market Spotlight (one metro or property type from the week's stories), AI in RE Weekly, Term of the Day. Sun: REIT Weekly, Week Ahead, Careers Corner (one practical tip, no numbers), Term of the Day. The site renders a Data Room block after Term of the Day.
- Everything free for now; no paid tier.
- Runtime: Python on GitHub Actions cron; Claude steps via Claude Code headless on Robert's Max subscription (CLAUDE_CODE_OAUTH_TOKEN), no API key.
- Blocked feeds (The Real Deal, GlobeSt, Multi-Housing News, CPE, CoStar) -> Google News `site:` queries, headline only. Never bypass bot protection.
- No X via browser cookies; no LinkedIn scraping. Gmail ingestion deferred.
- Market Summary (2026-10-05): adds 2Y, 10Y-2Y curve (shown in bps), 30Y mortgage, and a Credit group (now rendered as the Data Room, see Reader review below: HY OAS, bank CRE loans `CREACBW027SBOG`, bank CRE delinquency `DRCRELEXFACBS`, Trepp CMBS delinquency parsed from its monthly headline in `src/trepp.py`). Weekly/quarterly series use longer FRED windows (`rates.SERIES_LOOKBACK_DAYS`) and show an as-of in the row label. Every row has a gray hint (`render_html.HINTS`). Fed odds are split into cut/hold/hike. No "Sources" caption. REIT movers show company names (`reit_info` in sources.yaml) and a code-picked "why it moved" story or a code-filled "no company-specific news" note.
- Week Ahead (Sunday) merges the Fed calendar JSON with FRED `releases/dates` (free, existing key) filtered to a major-release allowlist (`calendar.MAJOR_RELEASES`: CPI, jobs, GDP, PCE, retail sales, PPI, JOLTS, housing starts, construction spending, new/existing home sales, ECI), code-filled via `{{WEEK_AHEAD}}`. FRED gives no times. No Alpha Vantage earnings calendar (would eat the ~25/day quota). BLS `bls.ics` returns 403 to our descriptive User-Agent (not worked around); Census's indicator RSS lists only past releases. Both skipped.
- Market Watch (weekdays, after Top Stories): one story each for Sun Belt / West Coast / International, picked in code (`src/markets.py`) by keyword region match or the regional Google News query tag, never a story already used above. PERE and React News `site:` queries returned nothing, so they are not used.
- Style: no em or en dashes anywhere. Prompts forbid them, `checks` flags any, and `render_html.no_dashes()` replaces them at render time. Quick Hits = news line + link + ONE plain explanation sentence; AI section favors tools/use cases over data centers.
- Editions: Mon-Fri "Daily Edition", Sat/Sun "Weekend Edition", plus "N min read" (words/230). Sheet width 760px.
- Reader review (2026-10-05): tone is "plain English for a smart college student new to CRE", never babyish. 10Y `move_size` (unchanged < 3 bps, small 3-9, notable >= 10) scales "What it means". Fed Funds row = target range (FRED DFEDTARL/DFEDTARU, DFF fallback). Rates rows show their own "(as of ...)" when not RATES_ASOF's day. Credit rows moved to a "Data Room" block after Term of the Day with an "Updated" tag (as-of within 7 days). Stock/REIT recap stories older than 24h are dropped; `checks` adds a `contradiction` editor note when prose says REITs fell/rose against VNQ_CHG's sign. Politically charged government deals (ICE detention, prisons) are kept out of Top Stories when other stories exist. Story source names go through `source_names` in sources.yaml. Site: RSS `feed.xml` (20 newest), About page "How each issue is made" + "Corrections", Recent issues list only from 3 issues. Not done: decoding Google News links (unreliable, no official API).

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
