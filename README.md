# CRE Blurb

A daily commercial real estate (CRE) briefing published as a website on GitHub Pages. Every morning a program gathers rates, debt news, deals, REIT moves and top stories, and Claude writes a draft. I review it and approve it by adding the `approved` label to its GitHub Issue. The 5 AM pipeline only ever saves a draft; it never publishes anything or emails subscribers. Publishing to the website is a separate step that I trigger myself (see Publishing below).

The original idea is in [`BRIEF.md`](BRIEF.md); the design is in `docs/superpowers/specs/`.

## What the pipeline does
1. GitHub Actions runs it every morning (about 5-6 AM ET).
2. It pulls news feeds, Treasury/SOFR rates (FRED), Fed odds (Polymarket) and REIT prices (Alpha Vantage).
3. Everything is saved to a small SQLite database and duplicate stories are merged.
4. Claude drafts the issue. The model never types a number: code fills in every rate and price, and a missing value shows as `n/a`.
5. The draft is saved to `issues/YYYY-MM-DD.md` and opened as a GitHub Issue labeled `draft`, which GitHub emails to me.

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
The automatic checks found something suspicious (for example a number the model typed itself, or a missing source link). Nothing is wrong with the pipeline; it is asking for a human look. Read the banner, check the flagged lines against the linked sources, fix or delete them. You do not need to delete the banner; it is removed automatically when you publish (see Publishing below).

## Publishing
The website is at https://robertcoles04.github.io/cre-newsletter/ (GitHub Pages).

1. Open the `Draft: CRE Blurb <date>` Issue and read the draft. To change it, edit `issues/<date>.md` on github.com (the pencil icon) and save.
2. Add the `approved` label to the Issue.
3. About 2 minutes later the issue is live. GitHub comments on the Issue with the link and closes it.

What blocks a publish: any leftover `[CHECK]` or `{{` in the text, the "Claude unavailable: fact sheet only" heading, raw HTML tags (like `<b>` or `<script>`), or a link or image whose address is not `http://`, `https://`, `mailto:`, a `#` anchor or a relative path (so `javascript:` and `data:` links are refused, even when disguised with HTML codes like `&#106;`). `<https://...>` links are fine. The "Review before publishing" banner is removed automatically. If a check fails, GitHub comments on the Issue with what to fix and removes the `approved` label. Fix the file, then add `approved` again.

The Market Summary numbers on the website come from `issues/<date>.json`, not from the `.md` file. To correct a number, edit the `.json` file.

If publishing fails for any other reason after the check passed, GitHub comments on the Issue with a link to the run and removes the `approved` label. Fix the problem, then add `approved` again to retry.

Once a date is published, the daily pipeline will not overwrite it, even with `--force`.

To rebuild the site without approving anything (for example after a design change): on GitHub go to Actions, pick "Publish CRE Blurb", then Run workflow.

One-time setup: the repo must be public (or on a plan that allows Pages), and in Settings -> Pages the source must be set to "GitHub Actions".
