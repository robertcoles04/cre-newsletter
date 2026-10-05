# CRE Blurb website: design spec

Date: 2026-10-05. Status: design approved by Robert in chat (2026-10-05); this
document is the written version. Replaces Substack as the publishing home.

## Goal

Approved issues appear on a public website within about two minutes of Robert
adding the `approved` label to that day's GitHub Issue. Nothing reaches the site
without that label, and the guardrails below can still block it.

URL: `https://robertcoles04.github.io/cre-newsletter/` (GitHub Pages, deployed
by Actions). A custom domain is a later, optional step.

## Flow

1. The 5 AM run delivers the draft as today: `issues/<date>.md`, `.html`, a
   GitHub Issue titled `Draft: CRE Blurb <date>`, and (new) `issues/<date>.json`.
2. Robert reads the preview and optionally edits `issues/<date>.md` on github.com.
3. Robert adds the label `approved` to the Issue.
4. `.github/workflows/publish.yml` runs: gate, record, build, deploy, then
   comments the live URL on the Issue and closes it.
5. If the gate fails, the workflow comments the reason, removes the `approved`
   label (so re-adding it retries), and does not deploy.

## Pieces

### 1. Delivery saves the fact sheet (`src/deliver.py`, `src/main.py`)
`deliver()` gets an optional `factsheet` argument and writes
`issues/<date>.json` = `{"date", "day_type", "values"}`. The Market Summary is
rebuilt from these code-filled values at publish time, so the model still never
writes numbers. The daily workflow already commits all of `issues/`.

### 2. Publish gate (`src/publish.py`)
`python -m src.publish <date>`:
- Loads `issues/<date>.md` and `issues/<date>.json`. Missing either: refuse.
- Refuses if the md contains `[CHECK]`, `{{`, or the heading
  `# Claude unavailable: fact sheet only`. Each reason is listed in plain English
  with the offending line where useful.
- Strips the `> **Review before publishing:**` blockquote banner (every
  consecutive `>` line starting at that banner).
- On success, adds the date to `issues/published.json` (sorted list of ISO
  dates, no duplicates). On refusal, prints the reasons and exits 1.
- Pure functions (`check(md) -> list[str]`, `strip_banner(md) -> str`) so they
  are unit-testable without git or the network.

Published state lives only in `issues/published.json`, committed to main by the
publish workflow. The `site/` folder is build output and is gitignored.

### 3. Site build (`src/site.py`)
`python -m src.site build --out site` rebuilds the whole site from
`published.json` every time (so a later edit to an older published md shows up
on the next publish):
- `issues/YYYY-MM-DD/index.html`: the issue, rendered from the **edited** md
  (banner stripped) with `render_issue_html`, `problems=[]` (no editor-notes
  box), chart copied from `issues/img/<date>-chart.png` to `chart.png` beside
  it (relative path; skipped if absent).
- `index.html` (Home): the latest issue in full, then "Recent issues" (last 10).
- `archive/index.html`: every published issue, newest first, grouped by month.
- `about/index.html`: what CRE Blurb is, how it is made (pipeline drafts, Robert
  reviews), data sources (FRED, U.S. Treasury, Polymarket, Alpha Vantage, linked
  news outlets), and the footer disclaimer.
- `404.html`: short "page not found" with links home and to the archive.

Shared chrome on every page: a slim site nav (CRE Blurb · Archive · About) and
the existing footer. Same Offering Memo design (`DESIGN.md`); the nav is navy
text links on the sheet, no new colors. No JavaScript.

Head tags per page: `<title>`, `<meta name="description">`, canonical URL, and
Open Graph / Twitter tags (`og:title`, `og:description`, `og:url`, `og:type`,
`og:image` = the issue's chart PNG as an absolute URL when present). Issue
description = the first prose sentence of the first story section, trimmed to
~155 characters; fallback "Daily commercial real estate briefing for <date>."
The base URL lives in one constant (`SITE_URL`) so a custom domain is a
one-line change.

`render_issue_html` gains optional keyword arguments for the nav and extra head
tags; the 5 AM preview output stays byte-for-byte the same when they are unset.

### 4. Workflow (`.github/workflows/publish.yml`)
- Trigger: `issues: types: [labeled]`, plus `workflow_dispatch` (rebuild and
  redeploy without publishing anything new).
- Runs only if the label is `approved` and the title starts with
  `Draft: CRE Blurb `; the date is parsed from the title.
- Permissions: `contents: write`, `issues: write`, `pages: write`,
  `id-token: write`. Concurrency group `publish`, no cancel.
- Steps: checkout main → setup Python → `python -m src.publish <date>` (on
  failure: comment reasons, remove label, fail) → commit `issues/published.json`
  and push (pull --rebase first) → `python -m src.site build` →
  `actions/upload-pages-artifact` → `actions/deploy-pages` → comment the issue
  URL and close the Issue.
- No secrets needed: it only uses `github.token`. Only people with write/triage
  access can add labels, so strangers cannot publish once the repo is public.

### 5. Docs
README gets a "Publishing" section (how to edit, approve, what blocks a
publish, how to rebuild). `CLAUDE.md` Status updated. The Guardrails line
"Never publishes" is reworded: the 5 AM pipeline never publishes; only the
label-triggered workflow does.

### 6. Go public (last, needs Robert's explicit OK at that moment)
1. Secret scan of the full history: compare every commit's content against the
   `.env` values without printing them; grep `git log -p --all` for key-like
   strings (`sk-`, `ghp_`, `api_key=`, long hex/base64). Report counts only.
2. Make the repo public (`gh repo edit ... --visibility public
   --accept-visibility-change-consequences`). Drafts, Issues, `issues/` and the
   `data` branch (`cre.db`) become publicly visible; Robert accepted this.
3. Enable Pages with source "GitHub Actions"
   (`gh api -X POST repos/robertcoles04/cre-newsletter/pages -f build_type=workflow`).
4. Run `publish.yml` via `workflow_dispatch` once to deploy the (possibly empty)
   site and confirm the URL loads.

## Testing
- Unit: gate checks (each blocker, clean pass), banner strip, published.json
  add/dedupe, description extraction, site build into a temp dir from fixture
  issues (pages exist, links resolve, chart copied, no editor-notes box, OG tags
  present, absolute URLs use `SITE_URL`), deliver writes the json, preview
  unchanged when site kwargs are unset.
- Manual: after go-public, approve a real draft and check the live page on a
  phone and in a LinkedIn post preview.

## Out of scope (later)
Email signup and sending, custom domain, glossary, deal database, RSS feed.
