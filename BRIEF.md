# CRE Daily Newsletter: Project Brief

Oct 4, 2026 · @b

## Overview

&#91;Brand name TBD\] is a 5-minute weekday CRE briefing on Substack. A pipeline drafts each issue every morning, and Robert reviews and sends it.

- **Reader:** anyone interested in commercial real estate, from students to analysts, brokers and investors.
- **Promise:** know what moved in rates, debt, deals and news today, and learn the market along the way.
- **Goals:** build an audience first, then monetize with a paid tier and sponsors.
- **Owner time:** 1 to 2 hours a week, about 15 minutes each weekday reviewing the draft.
- **Success for v1:** a draft is ready by 6:30 AM ET every weekday and published by 8 AM ET.

"Daily" means Monday to Friday. Rates and deal flow are quiet on weekends, and five issues a week fits the time budget.

## Issue format

Each issue runs about 1,000 words, a 5-minute read. Rates, debt and news run every day; deals of the week run as a bigger Friday section.

| Section | Runs | Length | What it covers |
| --- | --- | --- | --- |
| The Numbers | Daily | \~75 words | 10Y and 5Y Treasury, SOFR, fed funds; Polymarket odds on the next Fed move (from Phase 4); change vs. prior day in bps; one-line read on what it means for CRE |
| Debt Markets | Daily | \~200 words | Notable financings and refis, CMBS and CRE CLO issuance, lender moves, spreads, delinquency data when released |
| Top Stories | Daily | \~500 words | 3 to 5 stories, each 2 sentences plus a "why it matters" line and a source link |
| Term of the Day (suggested) | Daily | \~50 words | One CRE term explained with a real example from the issue; serves the "learn the market" goal |
| Deals of the Week | Friday | \~400 words | 1 to 2 deals per asset class: multifamily, industrial, office, retail, hospitality, alternatives (data centers, self storage, senior housing) |

On Fridays, Top Stories drops to 2 or 3 items so the issue stays near 5 minutes.

Each deal lists property, market, buyer, seller, price, price per SF or unit, cap rate when disclosed, and the source.

**Voice:** plain and confident, like a sharp analyst briefing a friend. Explain jargon the first time it appears. No hype words, no predictions stated as fact, every claim linked to its source.

## Data sources

Start with free, reliable sources (RSS, FRED, SEC EDGAR) and add social media in a later phase. Every feed URL and API term below gets verified during the build before the pipeline relies on it.

| Source type | Examples | How it's pulled | Phase |
| --- | --- | --- | --- |
| Rates | FRED series DGS10, DGS5, SOFR, DFF; Treasury.gov daily yields as backup | FRED API (free key) | 1 |
| CRE news | Commercial Observer, GlobeSt, Bisnow, The Real Deal, Connect CRE, Multi-Housing News | RSS feeds | 1 |
| Broad news search | Google News RSS queries like "commercial real estate sale", "CMBS", "refinance loan" | RSS query URLs | 1 |
| Deal announcements | Brokerage and lender newsrooms (CBRE, JLL, Newmark, Walker & Dunlop, Berkadia), PR Newswire and Business Wire real estate feeds | RSS or page fetch | 2 |
| REIT filings | 8-Ks announcing acquisitions, dispositions and financings | SEC EDGAR API (free) | 2 |
| Debt data | Trepp monthly CMBS delinquency, MBA origination reports, Fed Senior Loan Officer Survey | Page fetch on release dates | 2 |
| Social | r/CommercialRealEstate and Polymarket Fed-rate markets first; curated X accounts later | last30days skill (mvanhorn/last30days-skill), JSON output; X only via official API | 4 |
| Your tips | Links or LinkedIn posts you spot during the day | You drop them in a tips file or form | 2 |

LinkedIn is not scraped: it breaks LinkedIn's terms and gets blocked. Paywalled outlets contribute headline and public summary only.

## Pipeline architecture

A scheduled job runs every weekday at 5:30 AM ET and ends with a Substack draft waiting for Robert's approval.

&#91;embedded content: newsletter pipeline · 6 steps, 1 human review\]

Everything left of the last box is automated; the only manual step is the review. In Phase 1 the "Save Substack draft" step is an email with the Markdown instead.

## Data model and storage

One SQLite file holds everything, with four tables. It's free, needs no server, and the deals table becomes a searchable deal database over time.

| Table | Key fields | Purpose |
| --- | --- | --- |
| items | source, url, title, published\_at, summary, content\_hash, section, asset\_class, market, importance\_score, used\_in\_issue | Every story collected, tagged and scored |
| deals | item\_id, property, market, asset\_class, buyer, seller, price\_usd, size (SF or units), price\_per, cap\_rate, source\_url | Structured deal records for Friday issues |
| rates | date, series, value | Daily rate history for the Numbers section and change calculations |
| issues | date, type (daily or Friday), status (draft, approved, published), markdown, substack\_draft\_id | Every issue and its status |

**Dedupe rule:** when several outlets cover the same story, cluster them by canonical URL and title similarity. Keep the best source and list the others as "also covered by".

The deals table is a future paid-tier asset: a searchable deal log that grows every week.

## Guardrails

Nothing publishes without Robert's approval. In a newsletter meant to teach the market, one wrong number costs more than one late issue.

- **Human approval:** the pipeline only saves drafts. It never sends.
- **Rates come from the API:** the model never writes a rate or a change in bps. Code inserts them from FRED.
- **Deal numbers must match the source:** price, size and cap rate must appear in the source text. Otherwise the field is left blank or flagged `[CHECK]` for review.
- **Copyright:** summarize in our own words, at most one short quote per source, always link back. Never republish article text. Paywalled outlets contribute headline and public summary only.
- **Social posts:** only public posts, always attributed. Engagement can pick a "what CRE is talking about" item, never the deals or top stories. Skip the last30days skill's X browser-cookie and LinkedIn scraper routes for the newsletter, since they risk account flags; add X only through the official API.
- **Substack integration is unofficial:** Substack has no official publishing API, and the community tools log in with your browser session cookie, so they can break without warning. Fallback: the pipeline also writes the issue as Markdown you can paste in.
- **Secrets:** API keys and the Substack cookie live in `.env`, never committed to git.
- **Footer:** every issue ends with "For informational purposes only. Not investment advice."

## Tech stack and repo structure

Python, SQLite and a scheduled job. No web framework, no paid infrastructure in v1.

| Piece | Choice | Why |
| --- | --- | --- |
| Language | Python 3.11+ | Best libraries for feeds and data; easy for Claude Code to work in |
| Feeds and HTTP | feedparser, httpx | Standard, simple |
| Storage | SQLite | One file, free, no server |
| Writing and tagging | Anthropic API (Claude) | Classifies items, extracts deal fields, drafts the issue |
| Substack drafts | python-substack (unofficial) | Saves the issue as a Substack draft (Phase 3) |
| Scheduling | GitHub Actions cron, weekdays | Free and runs while your laptop is off; cron runs in UTC, so adjust for daylight saving |
| Alerts | Email via Gmail SMTP | Tells you the draft is ready |

```text
cre-newsletter/
  CLAUDE.md            project rules for Claude Code
  BRIEF.md             this document, exported as Markdown
  .env.example         API keys template
  config/
    sources.yaml       feeds, FRED series, search queries
    voice.md           brand voice + 2 sample issues
  templates/
    daily.md
    friday.md
  src/
    collect/           rss.py, fred.py, edgar.py, social.py
    store.py           SQLite + dedupe
    classify.py        tag section, asset class, score
    extract_deals.py
    draft.py           writes the issue
    factcheck.py
    publish.py         Substack draft + Markdown fallback
    notify.py
    main.py            runs the whole pipeline
  issues/              one Markdown file per issue
  tests/
```

## Build phases

Ship a working daily draft in Phase 1, then add sources and automation. Publish real issues from Phase 1 on, so the format improves from reader feedback, not guesses.

**Before Phase 1 (Robert):**

- [ ] Pick the brand name and create the Substack
- [ ] Get a free FRED API key and an Anthropic API key
- [ ] Write one sample issue by hand to set the voice (goes in `config/voice.md`)
- [ ] Export this brief as Markdown and save it as `BRIEF.md` in a new repo

1. **Phase 1, MVP:** RSS news + FRED rates into SQLite. Claude tags, ranks and drafts the daily issue as Markdown, then emails it to you. You paste it into Substack by hand. Done when 5 issues have gone out.
2. **Phase 2, deals and debt:** add press releases, SEC 8-Ks, debt data and the tips file. Build deal extraction, the Friday Deals of the Week issue and the fact-check step.
3. **Phase 3, automation:** weekday scheduled runs on GitHub Actions, drafts saved straight to Substack, alert when ready. Done when a draft is ready by 6:30 AM ET for 2 straight weeks.
4. **Phase 4, social:** plug in the last30days skill as the social collector, starting with Reddit and Polymarket. Run it with a one-day lookback, take its JSON output into the items table, and test signal quality on a few CRE queries first. Feed Substack open and click data back into story ranking.
5. **Phase 5, monetization:** paid tier (weekly deep dive, searchable deal database) and sponsors once the audience is large enough to sell.

## Starting prompt for Claude Code

Open a new folder with `BRIEF.md` in it, start Claude Code in plan mode, and paste this:

```text
I'm building a weekday commercial real estate newsletter for Substack. The full spec is in BRIEF.md. Read it first.

1. Write a CLAUDE.md for this project that captures the goals, issue format, guardrails and conventions from the brief.
2. Plan and build Phase 1 only:
   - Pull CRE news from the RSS feeds and Google News queries in config/sources.yaml
   - Pull 10Y, 5Y, SOFR and fed funds from the FRED API
   - Store everything in SQLite with dedupe
   - Use the Claude API to tag, rank and draft today's issue following templates/daily.md and config/voice.md
   - Save it to issues/YYYY-MM-DD.md and email it to me
3. Before relying on any feed, test that it returns recent items. Drop or replace any that fail and tell me which.
4. Rates and their changes must be inserted by code from FRED data, never written by the model.
5. Every story must link to its source. Summaries must be in our own words.

Keep it simple: Python, SQLite, no web framework. Ask me before adding any paid service or API. Show me the plan before writing code.
```

After Phase 1 works, start each later phase with: "Read BRIEF.md and CLAUDE.md, then plan Phase N."
