"""Delivery: write the draft to issues/<date>.md (+ .html preview), record it, open one GitHub Issue.

Never publishes anywhere else (no Substack, no email).
"""

import json
import shutil
import subprocess
from datetime import date
from pathlib import Path


def run_gh(args: list[str]) -> str:
    cmd = [shutil.which("gh") or "gh", *args]
    result = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", check=True)
    return result.stdout


def _banner(problems: list[str]) -> str:
    lines = ["> **Review before publishing:**"] + [f"> - {p}" for p in problems]
    return "\n".join(lines) + "\n\n"


def _create_issue(gh, run_date: str, body_file: Path) -> int:
    args = ["issue", "create", "--title", f"Draft: CRE Blurb {run_date}",
            "--body-file", str(body_file)]
    try:
        out = gh([*args, "--label", "draft"])
    except subprocess.CalledProcessError as e:
        detail = f"{e.stderr or ''} {e.stdout or ''}"
        if "label" not in detail.lower():
            raise
        out = gh(args)
    return int(out.strip().rstrip("/").rsplit("/", 1)[-1])


def deliver(conn, run_date: date, md: str, problems: list[str], chart: Path | None,
            repo_root: Path, day_type: str, term: str | None,
            gh=run_gh, dry_run: bool = False, html: str | None = None,
            factsheet: dict | None = None) -> Path:
    key = run_date.isoformat()
    repo_root = Path(repo_root)
    issues_dir = repo_root / "issues"
    issues_dir.mkdir(parents=True, exist_ok=True)

    text = (_banner(problems) + md) if problems else md
    path = issues_dir / f"{key}.md"
    path.write_text(text, encoding="utf-8")
    if html is not None:
        (issues_dir / f"{key}.html").write_text(html, encoding="utf-8")

    if factsheet is not None:
        data = {"date": factsheet["date"], "day_type": factsheet["day_type"],
                "values": factsheet["values"]}
        (issues_dir / f"{key}.json").write_text(
            json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")

    if chart is not None:
        img_dir = issues_dir / "img"
        img_dir.mkdir(exist_ok=True)
        shutil.copyfile(chart, img_dir / f"{key}-chart.png")

    conn.execute(
        """INSERT INTO issues(date, day_type, status, markdown, source_problems, term)
           VALUES (?, ?, 'draft', ?, ?, ?)
           ON CONFLICT(date) DO UPDATE SET markdown=excluded.markdown,
             source_problems=excluded.source_problems, term=excluded.term""",
        (key, day_type, text, json.dumps(problems), term))
    conn.commit()

    if not dry_run:
        row = conn.execute("SELECT gh_issue FROM issues WHERE date=?", (key,)).fetchone()
        if row["gh_issue"] is None:
            number = _create_issue(gh, key, path)
            conn.execute("UPDATE issues SET gh_issue=? WHERE date=?", (number, key))
            conn.commit()
    return path
