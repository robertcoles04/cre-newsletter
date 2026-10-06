# CRE Blurb

A daily commercial real estate (CRE) briefing published as a website on GitHub Pages. Every morning a program gathers rates, debt news, deals, REIT moves and top stories, and Claude writes a draft. If the draft passes the automatic safety check it goes live on the website right away; I review it afterwards and fix anything I find. A draft that fails the check waits until I fix it and add the `approved` label to its GitHub Issue. Nothing is emailed to subscribers (see Publishing below).

The original idea is in [`BRIEF.md`](BRIEF.md); the design is in `docs/superpowers/specs/`.

## What the pipeline does
1. GitHub Actions runs it every morning (about 5-6 AM ET).
2. It pulls news feeds, rates and credit data (FRED, Trepp), Fed odds (Polymarket), REIT prices (Alpha Vantage) and, on Sundays, the Fed calendar.
3. Everything is saved to a small SQLite database and duplicate stories are merged.
4. Claude drafts the issue. The model never types a number: code fills in every rate and price, and a missing value shows as `n/a`.
5. The draft is saved to `issues/YYYY-MM-DD.md` and opened as a GitHub Issue labeled `draft`, which GitHub emails to me.

## What the Market Summary shows
The table at the top of each issue is built by code, never by Claude. Each row has a short gray line explaining it.
- **Rates:** 10-, 5- and 2-Year Treasuries, the 10Y-2Y curve, SOFR, the Fed funds target range, and the 30-year mortgage rate. A row whose data is from a different day than the 10-Year shows "(as of ...)" in its gray line.
- **Federal Reserve:** the next FOMC meeting, plus Polymarket odds of a cut, a hold and a hike.
- **REITs:** VNQ (the real estate stock fund), the day's biggest gain and drop by company name, VNQ's dividend yield and its spread to the 10-Year. Under the table, a "Why it moved" line cites a news story about the company, or says there was no company-specific news.
- **Data Room** (after Term of the Day, not in the table): the high-yield bond spread, total bank CRE loans (weekly), the bank CRE delinquency rate (quarterly, labeled e.g. "Q2 2026") and Trepp's monthly CMBS delinquency rate, read from Trepp's headline. Rows updated in the last 7 days are tagged "Updated".

Charts (all drawn by our own code from the same data, never pictures from the web): the 10-Year Treasury trend, the yield curve (today vs. a month ago), the 30-year mortgage over six months, the Fed odds as one bar, and a REIT scoreboard of every tracked REIT's daily move. Any chart without enough data is simply left out.

Sources: FRED (Treasury, Freddie Mac, ICE BofA and Federal Reserve series), U.S. Treasury, Polymarket, Alpha Vantage, Trepp. Sunday's Week Ahead list comes from the Federal Reserve calendar plus FRED's release calendar (major data releases only). The site also publishes an RSS feed at `feed.xml`. Each issue page has an "In this issue" jump list and Previous / Next issue links. Weekday issues also have a Market Watch section: one story each for the Sun Belt, the West Coast and International.

## Local setup
```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # Windows (Git Bash)
cp .env.example .env                                       # then fill in the keys
```
`.env` holds `FRED_API_KEY`, `ALPHA_VANTAGE_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN` and `GH_TOKEN`. It is never committed. You also need the Claude Code CLI installed (`npm i -g @anthropic-ai/claude-code`).

Try it without sending anything:
```bash
.venv/Scripts/python -m src.main --force --dry-run
```
`--force` bypasses only the 5-6 AM ET hour check and the "already delivered today" skip (so it also re-runs a date that already has a GitHub Issue; the issue is never duplicated); `--dry-run` writes the draft file but does not create a GitHub Issue. Run the tests with `.venv/Scripts/python -m pytest`.

## Running it on GitHub (one-time setup)
1. Make the token Claude uses in CI (uses my Max subscription, no API key): run `claude setup-token` and copy the token it prints.
2. Add the 3 secrets to the repo (each command asks you to paste the value):
   ```bash
   gh secret set FRED_API_KEY
   gh secret set ALPHA_VANTAGE_API_KEY
   gh secret set CLAUDE_CODE_OAUTH_TOKEN
   ```
3. Merge the workflow file (`.github/workflows/daily.yml`) to the default branch (`main`) first: `gh workflow run` and the cron schedule only work for workflows on the default branch. Also make sure `main` and `data` have no branch protection that blocks the bot's push (the bot pushes `issues/` to main and force-pushes `cre.db` to `data`).
4. Test it once by hand: `gh workflow run daily.yml -f force=true`, then `gh run watch`.

After that it runs on its own every morning. The database lives on a `data` branch (one file, `cre.db`) so each run remembers the last one.

## Where the drafts show up
- `issues/YYYY-MM-DD.md` in this repo (the bot commits it to main).
- A GitHub Issue titled `Draft: CRE Blurb <date>` with the `draft` label; GitHub emails it to me.

## If the draft has a "Review before publishing" banner
The automatic checks found something suspicious (for example a number the model typed itself, or a missing source link). Nothing is wrong with the pipeline; it is asking for a human look. Read the banner, check the flagged lines against the linked sources, fix or delete them. You do not need to delete the banner; it is removed automatically when you publish (see Publishing below). These notes do not block the automatic publish, so if a draft has a banner, check the flagged lines on the live issue and correct them there.

## Publishing
The website is at https://creblurb.org/ (GitHub Pages, custom domain bought at Porkbun; the old github.io address redirects).

Site pages: home (latest issue), each issue, Archive, Glossary, About, Privacy, Terms and Accessibility (the last four linked in every footer), plus `feed.xml` and `sitemap.xml`. Fonts are self-hosted from `fonts/` (SIL Open Font License, license files alongside) so readers' browsers never contact Google. The site never shows the publisher's name; the contact address everywhere is corrections@creblurb.org (set up forwarding at the registrar). The ticker has a Pause/Play button.

Issues publish automatically around 5 to 6 AM ET when they pass the safety check; otherwise they wait as a draft for the `approved` label. To fix a published issue, edit issues/<date>.md on GitHub and commit; the site rebuilds in about 2 minutes.

How the automatic publish works: after the daily run saves the draft and opens its `Draft: CRE Blurb <date>` Issue, it starts the "Publish CRE Blurb" workflow for that Issue. That runs the same safety check as the `approved` label. If it passes, the issue goes live, and GitHub comments "Published automatically: <link>" on the Issue and closes it. If it fails, GitHub comments "Not auto-published: <reasons>" and leaves the Issue open. These are AI-written issues going live without a human read first, so read each one afterwards. (GitHub's scheduled runs can start late, sometimes by 30+ minutes, so "5 to 6 AM" is approximate.)

To publish a draft that was not auto-published:
1. Open the `Draft: CRE Blurb <date>` Issue and read the comment. Fix `issues/<date>.md` on github.com (the pencil icon) and save.
2. Add the `approved` label to the Issue.
3. About 2 minutes later the issue is live. GitHub comments on the Issue with the link and closes it.

Corrections: saving any `issues/*.md` or `issues/*.json` file on `main` (for example from github.com) rebuilds and redeploys the site. This only rebuilds; it never publishes a new date, because the site shows only the dates listed in `issues/published.json`. If an edit makes a published issue fail the safety check, the rebuild leaves that issue off the site and shows a warning on the workflow run.

What blocks a publish: any leftover `[CHECK]` or `{{` in the text, the "Claude unavailable: fact sheet only" heading, raw HTML tags (like `<b>` or `<script>`), or a link or image whose address is not `http://`, `https://`, `mailto:`, a `#` anchor or a relative path (so `javascript:` and `data:` links are refused, even when disguised with HTML codes like `&#106;`). `<https://...>` links are fine. The "Review before publishing" banner is removed automatically. If a check fails on an approval, GitHub comments on the Issue with what to fix and removes the `approved` label. Fix the file, then add `approved` again.

The Market Summary numbers on the website come from `issues/<date>.json`, not from the `.md` file. To correct a number, edit the `.json` file.

If publishing fails for any other reason after the check passed, GitHub comments on the Issue with a link to the run and removes the `approved` label. Fix the problem, then add `approved` again to retry.

Once a date is published, the daily pipeline will not overwrite it, even with `--force`.

To rebuild the site without approving anything (for example after a design change): on GitHub go to Actions, pick "Publish CRE Blurb", then Run workflow.

One-time setup: the repo must be public (or on a plan that allows Pages), and in Settings -> Pages the source must be set to "GitHub Actions".
