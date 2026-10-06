"""Delivery: write the draft to issues/<date>.md (+ .html preview), record it, open one GitHub Issue.

Never publishes anywhere else (no Substack, no email).
"""

import json
import shutil
import subprocess
from datetime import date
from pathlib import Path

from src.publish import load_published
from src.store import record_used_stories


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
            factsheet: dict | None = None, charts: dict | None = None) -> Path:
    """`charts` maps chart names to {"path", "alt", "width", "height"}: each PNG is copied
    to issues/img/<date>-<name>.png and its alt text and size go into the JSON so the
    website can show it. `chart` (the 10-Year PNG) is the older single-chart argument."""
    key = run_date.isoformat()
    repo_root = Path(repo_root)
    if key in load_published(repo_root):
        raise RuntimeError(f"{key} is already published; not overwriting issues/{key}.md")
    issues_dir = repo_root / "issues"
    issues_dir.mkdir(parents=True, exist_ok=True)

    text = (_banner(problems) + md) if problems else md
    path = issues_dir / f"{key}.md"
    path.write_text(text, encoding="utf-8")
    if html is not None:
        (issues_dir / f"{key}.html").write_text(html, encoding="utf-8")

    charts = dict(charts or {})
    if factsheet is not None:
        data = {"date": factsheet["date"], "day_type": factsheet["day_type"],
                "values": factsheet["values"]}
        if factsheet.get("as_of_dates") is not None:
            data["as_of_dates"] = factsheet["as_of_dates"]
        if charts:
            data["charts"] = {}
            for name, c in charts.items():
                rec = {k: c[k] for k in ("alt", "width", "height") if k in c}
                if c.get("sm"):
                    rec["sm"] = {"width": c["sm"]["width"], "height": c["sm"]["height"]}
                data["charts"][name] = rec
        (issues_dir / f"{key}.json").write_text(
            json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")

    copies = {name: c["path"] for name, c in charts.items() if c.get("path")}
    copies.update({f"{name}-sm": c["sm"]["path"] for name, c in charts.items()
                   if c.get("sm") and c["sm"].get("path")})
    if chart is not None:
        copies.setdefault("chart", chart)
    if copies:
        img_dir = issues_dir / "img"
        img_dir.mkdir(exist_ok=True)
        for name, src in copies.items():
            shutil.copyfile(src, img_dir / f"{key}-{name}.png")

    conn.execute(
        """INSERT INTO issues(date, day_type, status, markdown, source_problems, term)
           VALUES (?, ?, 'draft', ?, ?, ?)
           ON CONFLICT(date) DO UPDATE SET markdown=excluded.markdown,
             source_problems=excluded.source_problems, term=excluded.term""",
        (key, day_type, text, json.dumps(problems), term))
    conn.commit()
    record_used_stories(conn, run_date, md, factsheet)

    if not dry_run:
        row = conn.execute("SELECT gh_issue FROM issues WHERE date=?", (key,)).fetchone()
        if row["gh_issue"] is None:
            number = _create_issue(gh, key, path)
            conn.execute("UPDATE issues SET gh_issue=? WHERE date=?", (number, key))
            conn.commit()
    return path
