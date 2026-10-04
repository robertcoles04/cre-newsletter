# CRE Blurb

A daily commercial real estate (CRE) briefing for Substack. Every morning a program gathers rates, debt news, deals, REIT moves and top stories, and Claude writes a draft. I review it and hit publish. The pipeline only ever saves a draft; it never publishes or emails subscribers.

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
The automatic checks found something suspicious (for example a number the model typed itself, or a missing source link). Nothing is wrong with the pipeline; it is asking for a human look. Read the banner, check the flagged lines against the linked sources, fix or delete them, remove the banner, then publish by hand on Substack.
