# CRE Newsletter: Design Spec

Date: 2026-10-04 · Owner: Robert (the user) · Status: awaiting owner review

Supersedes `BRIEF.md` where they differ. The brief remains the background document.

## 1. Goal

A 7-day-a-week commercial real estate (CRE) briefing on Substack. A scheduled pipeline drafts each issue. Robert reviews it and publishes it.

- **Readers:** students through analysts, brokers and investors.
- **Promise:** know what moved in rates, debt, deals, REITs and news today, and learn the market along the way.
- **Owner time:** about 15 min each weekday and about 5 min on weekends (roughly 1.5 to 2 hours a week).
- **Success for v1:** a draft is waiting by 6:30 AM ET every day. Phase 1 is done when 5 real issues have been published.
- **Business:** everything is free for now. A paid tier and sponsors come later (Phase 5).
- **Platform:** Substack, chosen for its discovery network and built-in email. The subscriber list is exportable, so there's no lock-in. A companion website comes in Phase 5.

## 2. Issue format

| Day | Length | Sections |
|---|---|---|
| Mon to Thu | ~1,000 words | The Numbers, Debt Markets, Top Stories (3 to 5), AI in Real Estate (only when there's real news), Term of the Day |
| Fri | ~1,000 words | Same as Mon to Thu, but Top Stories drops to 2 or 3, plus Deals of the Week |
| Sat | ~500 words | Week in Review, AI in Real Estate Weekly, Market Spotlight |
| Sun | ~500 words | REIT Weekly, Week Ahead, Careers Corner |

Monday's issue also covers weekend news.

### Section contents

- **The Numbers** (~100 words plus a chart):
  - 10Y and 5Y Treasury, SOFR, fed funds, each with the change in bps.
  - Polymarket odds for the next Fed meeting.
  - A one-line "what it means", translating the move into deal math.
  - **REIT strip:** VNQ plus 1 or 2 notable movers with the reason.
  - **Spread to 10Y:** REIT dividend yield minus the 10Y.
  - **Chart of the Day:** a PNG drawn by code, for example the 10Y over 30 days.
  - Data is labelled "as of <date> close". At 6 AM the latest data is the prior business day; SOFR lags one more day.
- **Debt Markets** (~200 words): financings, refis, CMBS and CRE CLO issuance, lender moves, spreads.
  - **Distress Watch** line: defaults, foreclosures, special servicing, and monthly delinquency figures when released (for example Trepp's CMBS rate).
- **Top Stories:** each story is 2 sentences, plus a "why it matters" line with a concrete consequence, plus a source link. Smaller deals ($1M to $10M) are explicitly in scope.
- **AI in Real Estate:** one item, and only when something real happened. The Saturday weekly covers tools, proptech funding and adoption.
- **Term of the Day** (~50 words): one term, explained with an example from today's issue. It's drawn from `config/terms.yaml`, which is seeded with terms readers actually ask about: NNN vs absolute NNN, CAM, cap rate, leasing commissions, rate swaps, GP fees, entitlements.
- **Deals of the Week** (Fri, ~400 words): 1 or 2 deals per asset class (multifamily, industrial, office, retail, hospitality, alternatives). Each deal lists property, market, buyer, seller, price, $/SF or $/unit, cap rate if disclosed, and source.
- **Week in Review** (Sat): the top 5 stories of the week, one sentence each.
- **Market Spotlight** (Sat): one metro per week, rotating through `config/markets.yaml`. Covers vacancy, rents, big deals and pipeline.
- **REIT Weekly** (Sun):
  - Weekly sector performance.
  - A scorecard for 3 names: price, dividend yield, P/AFFO, NAV discount when available, and a one-line dividend-safety note.
  - A rotating sector spotlight: net lease, data centers, office and mortgage REITs, residential, industrial.
  - REITs readers care about: O, ADC, NNN, WPC, EPRT, VICI, PLD, STAG, MAA, AVB, EQR, EQIX, DLR, ARE, AGNC, ABR.
- **Week Ahead** (Sun): Fed meetings and speeches, economic data releases, REIT earnings and ex-dividend dates.
- **Careers Corner** (Sun): CRE internships and analyst roles, reusing `~/Projects/cre-internship-search`.

**Footer on every issue:** "For informational purposes only. Not investment advice."

## 3. Voice and writing quality

**Voice:** plain and confident, like a sharp analyst briefing a friend. Jargon is explained the first time it appears. No hype words, and no predictions stated as fact.

How the drafts avoid AI slop:
1. **Facts first.** Code builds a structured fact sheet: stories, links, numbers, and placeholders. Claude writes only from that fact sheet.
2. **Voice examples.** `config/voice.md` holds Robert's hand-written sample issue and 2 or 3 style references.
3. **Slop checker** (`src/checks.py`, deterministic). It flags:
   - phrases from `config/banned_phrases.txt`
   - em-dash overuse, "not just X but Y" patterns, hype words and exclamation points
   - sentences over 30 words
   - sections over their word budget
4. **Editor pass.** A separate Claude call cuts vague or unsupported lines against a rubric and marks shaky claims `[CHECK]`.
5. **Specificity rule.** Every story needs a number, a name or a place, plus a concrete "why it matters".
6. **Learning from edits** (Phase 3). The published issue is diffed against the draft. Recurring edits become new examples and new banned phrases.
7. **Models.** A cheaper model classifies and scores stories; the top model writes and edits.

## 4. Architecture

A Python pipeline runs on GitHub Actions. The Claude steps run through Claude Code in headless mode, authenticated with Robert's **Max subscription** token from `claude setup-token`, stored as the GitHub secret `CLAUDE_CODE_OAUTH_TOKEN`. There is no Anthropic API key and no API bill. If plan limits ever become a problem, switching to an API key is a one-setting change.

```
05:00 ET  GitHub Actions cron
 1 collect     (Python)  feeds, Google News, FRED, Treasury, Polymarket, Alpha Vantage, Exa*, EDGAR*, careers*
 2 store       (Python)  SQLite, dedupe by canonical URL + title similarity, keep best source, "also covered by"
 3 classify    (Claude)  section, asset class, market, importance score
 4 factsheet   (Python)  select by day type; compute bps and REIT moves; draw chart
 5 draft       (Claude)  write from the fact sheet + voice + template, with {{PLACEHOLDERS}} for every number
 6 check       (Python)  slop, budgets, links, deal numbers vs source text
   edit        (Claude)  editor pass
 7 fill        (Python)  replace placeholders with real values ("n/a" if missing, never a guess)
 8 deliver     (Python)  write issues/YYYY-MM-DD.md, commit, open a GitHub Issue (GitHub emails Robert)
~06:00    Robert reviews, pastes into Substack, publishes
* = Phase 2
```

### Scheduling

GitHub's cron runs in UTC only, so two triggers are set: 09:00 UTC and 10:00 UTC. The script runs only when the time in America/New_York is 5 AM, which covers both daylight and standard time. `workflow_dispatch` allows manual runs.

### Persistence

GitHub's runners start fresh every time. The SQLite DB is restored from and committed to a separate `data` branch at the start and end of each run. Issues are committed to `main` under `issues/`.

### Failure handling

- **One source fails:** the issue is still built. A "Source problems" note goes at the top of the draft.
- **Claude step fails** (for example, the token expired): the fact sheet is delivered as the issue body, so Robert can still write by hand.
- **Workflow fails:** GitHub emails Robert.
- **`doctor`:** a CLI that checks every source and reports OK, broken, or using its fallback.

## 5. Data sources

These were verified on 2026-10-04. Every feed is re-verified during the build, and a feed only goes into `config/sources.yaml` after a test shows it returns recent items.

| Source | Access | Phase | Fallback |
|---|---|---|---|
| Commercial Observer, Bisnow, Connect CRE, REBusiness Online, Trepp, PR Newswire real estate | Direct RSS (browser User-Agent) | 1 | Google News `site:` query |
| The Real Deal, GlobeSt, Multi-Housing News, CPE | Blocked (403). Google News `site:<domain> when:2d`, headline and public summary only | 1 | none |
| Google News topic queries (CRE sale, CMBS, CRE refinance, proptech/AI) | RSS with `when:2d` | 1 | none |
| FRED: DGS10, DGS5, SOFR, DFF | FRED API (free key) | 1 | Treasury.gov daily yield CSV (yields only) |
| Polymarket Fed odds | Gamma API, no key. Events are discovered by search because slugs change. | 1 | omit the line |
| REIT quotes and dividends | Alpha Vantage (free key; the daily request limit is confirmed during the build) | 1 | FMP |
| REIT Weekly fundamentals, earnings and dividend calendar | FMP and Alpha Vantage | 2 | omit metric |
| Monthly debt data (Trepp delinquency, MBA, Fed loan officer survey), deal and AI discovery | Exa search API (free tier) | 2 | Google News |
| Public article full text | Jina Reader (`r.jina.ai`), public pages only, never paywalled | 2 | feed summary |
| REIT 8-Ks (acquisitions, dispositions, financings) | SEC EDGAR API | 2 | none |
| Careers | `cre-internship-search` scraper | 2 | omit section |
| Robert's tips | `tips.md` in the repo | 2 | none |
| Reddit (r/CommercialRealEstate, r/REITs, r/RealEstateDevelopment) | Official Reddit API (free app). Anonymous JSON is blocked. | 4 | none |
| last30days skill | weekly "what CRE is talking about" item | 4 | none |

**Never used:**
- LinkedIn scraping
- X or Reddit through browser cookies
- anything that bypasses bot protection

## 6. Data model (SQLite)

| Table | Key fields |
|---|---|
| items | id, source, url, canonical_url, title, published_at, summary, content_hash, cluster_id, section, asset_class, market, importance_score, used_in_issue |
| deals | item_id, property, market, asset_class, buyer, seller, price_usd, size, size_unit, price_per, cap_rate, source_url, verified |
| rates | date, series, value |
| reit_quotes | date, ticker, close, change_pct, dividend_yield |
| issues | date, day_type, status (draft/approved/published), markdown, published_markdown, source_problems |

## 7. Guardrails

- The pipeline only saves drafts. It never sends anything to subscribers.
- The model never writes a rate, a bps change, a price or a set of odds. Code fills every number.
- Deal price, size and cap rate must appear in the source text. If not, the field is blank or flagged `[CHECK]`.
- Summaries are in our own words. At most one short quote per source, and every claim is linked.
- Paywalled and blocked outlets contribute the headline and public summary only.
- Secrets live in GitHub Secrets and in `.env` locally, never in git. The DB stays off `main`.
- Robert approves any paid service before it is added.

## 8. Repo structure

```
.github/workflows/daily.yml
config/   sources.yaml voice.md banned_phrases.txt terms.yaml markets.yaml
templates/ weekday.md friday.md saturday.md sunday.md
src/collect/ rss.py google_news.py fred.py treasury.py polymarket.py reits.py exa.py edgar.py careers.py
src/ store.py classify.py factsheet.py chart.py draft.py checks.py edit.py fill.py deliver.py doctor.py main.py
issues/  tips.md  tests/  .env.example
```

## 9. Testing

- **Collectors:** tested against saved sample responses in `tests/fixtures/`. Tests don't hit the network.
- **Unit tests** cover dedupe, bps math, placeholder filling (including the missing-data case), the slop checker, and deal-number verification.
- **End to end:** `python -m src.main --dry-run --date YYYY-MM-DD` runs locally. Phase 1 acceptance means several consecutive real drafts, graded by Robert.

## 10. Phases

0. **Robert's setup:**
   - brand name + Substack
   - free FRED, Alpha Vantage and Exa keys
   - `claude setup-token` saved as a GitHub secret
   - one hand-written sample issue
1. **MVP:**
   - collectors: RSS, Google News, FRED/Treasury, Polymarket, REIT strip
   - store, dedupe, classify, fact sheet, chart, draft, checks, editor pass, fill
   - templates for weekday, Saturday (Week in Review) and Sunday (Week Ahead + simple REIT recap)
   - Actions schedule and GitHub Issue delivery
   - **Done:** 5 issues published.
2. **Depth:**
   - Exa debt data and Distress Watch
   - deals pipeline and Friday Deals of the Week
   - full REIT Weekly, Careers Corner, Market Spotlight, AI Weekly
   - tips.md, Jina Reader, `doctor`
3. **Hands-off:**
   - Substack drafts, via the Newsletter Creator Tools plugin if it tests safely, otherwise Markdown
   - the learning-from-edits loop
   - **Done:** a draft is ready by 6:30 AM for 2 straight weeks.
4. **Social:** Reddit API, last30days weekly item, Substack open/click data feeding back into ranking.
5. **Grow:**
   - GitHub Pages site (deal database, rate charts, glossary)
   - sponsors and a paid tier

## 11. Open items (decided later, not blocking Phase 1)

- Brand name: **CRE Vantage** (chosen 2026-10-04). crevantage.substack.com was free; crevantage.com appears registered, check .co or alternatives.
- Exact Alpha Vantage, FMP and Exa free-tier limits, confirmed during the build.
