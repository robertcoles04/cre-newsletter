"""Pipeline orchestrator: collect, classify, draft, check, fill, deliver.

One failing source never stops the issue; every failure becomes a line in `problems`,
which deliver() prints as a banner at the top of the draft.
"""

import argparse
import base64
import re
import sys
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from src import classify, deliver as deliver_mod, draft, llm
from src.chart import rate_chart
from src.checks import FOOTER, check_issue, load_banned
from src.collect import google_news, polymarket, reits, rss
from src.config import ET, env, http_client, load_sources
from src.factsheet import build_factsheet, day_type
from src.fill import fill
from src.rates import collect_rates
from src.render_html import SUMMARY_ROWS, render_issue_html
from src.store import connect, get_rates, save_items, save_quotes

REPO_ROOT = Path(__file__).resolve().parent.parent
COLLECT_HOURS = 78  # store keeps everything; the fact sheet applies the day's lookback
CHART_DAYS = 45
GATE_HOURS = (5, 6)  # cron fires at 09:07 and 10:07 UTC: 5-6 AM ET in either DST state
CHART_LINE = re.compile(r"^[ \t]*!\[Chart of the Day\]\([^)\n]*\)[ \t]*\n?", re.M)
STRAY_BRACES = re.compile(r"\{\{.*?\}\}|\{\{|\}\}", re.S)


def _err(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__


def _story_lines(stories: list[dict]) -> list[str]:
    return [f"- [{s['title']}]({s['url']}) ({s['source']})" for s in stories]


def fallback_markdown(factsheet: dict) -> str:
    """Fact-sheet-only issue used when Claude is unavailable.

    Uses {{NAMES}} like the templates; run it through fill() afterwards.
    """
    lines = ["# Claude unavailable: fact sheet only", "", "## The Numbers", ""]
    values = factsheet.get("values", {})
    if "RATES_ASOF" in values:
        lines += ["*Rates as of {{RATES_ASOF}} close.*", ""]
    for label, key, chg in SUMMARY_ROWS:
        if key in values:
            tail = f" ({{{{{chg}}}}})" if chg and chg in values else ""
            lines.append(f"- **{label}:** {{{{{key}}}}}{tail}")
    if factsheet.get("day_type") == "sunday":
        for side, word in (("BEST", "Best"), ("WORST", "Worst")):
            keys = sorted(k for k in values if k.startswith(f"REITW_{side}_"))
            lines += [f"- **{word} REIT this week ({k.rsplit('_', 1)[1]}):** {{{{{k}}}}}"
                      for k in keys]
    sections = [("Top Stories", "top"), ("Quick Hits", "quick_hits"),
                ("Debt Markets", "debt"), ("AI in Real Estate", "ai"),
                ("Week in Review", "week_top"), ("AI in Real Estate Weekly", "ai_week")]
    for heading, key in sections:
        stories = factsheet.get(key) or []
        if stories:
            lines += ["", f"## {heading}", ""] + _story_lines(stories)
    term = factsheet.get("term") or {}
    if term.get("term"):
        lines += ["", "## Term of the Day", "",
                  f"- **{term['term']}:** {term.get('definition_hint', '')}"]
    lines += ["", FOOTER, ""]
    return "\n".join(lines)


# Alpha Vantage free tier: space calls out so the ETF call isn't rate limited.
sleep = time.sleep
AV_SPACING_SECONDS = 13


def _collect(conn, sources: dict, run_date: date, client, problems: list[str]):
    """Run every collector. Returns (fed odds, reit quotes, vnq yield)."""
    since = (datetime(run_date.year, run_date.month, run_date.day, 5, tzinfo=ET)
             - timedelta(hours=COLLECT_HOURS))

    for f in sources.get("feeds", []):
        try:
            save_items(conn, rss.fetch_feed(f["name"], f["url"], f["priority"], since, client))
        except Exception as exc:
            problems.append(f"feed/{f['name']}: {_err(exc)}")

    for g in sources.get("google_news", []):
        try:
            save_items(conn, google_news.fetch(
                g["name"], g["query"], g["priority"], since, client,
                when=g.get("when", "2d")))
        except Exception as exc:
            problems.append(f"news/{g['name']}: {_err(exc)}")

    fred_key = env("FRED_API_KEY", required=False)
    if not fred_key:
        problems.append("fred: missing FRED_API_KEY")
    try:
        for r in collect_rates(conn, sources["fred_series"], fred_key or None, client, run_date):
            if not r.ok:
                problems.append(f"rates/{r.name}: {r.error}")
            elif r.error:
                problems.append(f"rates/{r.name}: fallback treasury")
    except Exception as exc:
        problems.append(f"fred: {_err(exc)}")

    odds = None
    try:
        odds = polymarket.fetch_fed_odds(client, run_date)
        if odds is None:
            problems.append("polymarket: no fed odds")
    except Exception as exc:
        problems.append(f"polymarket: {_err(exc)}")

    quotes, vnq_yield = [], None
    av_key = env("ALPHA_VANTAGE_API_KEY", required=False)
    if not av_key:
        problems.append("reits: missing ALPHA_VANTAGE_API_KEY")
    else:
        try:
            etf = sources["reit_etf"]
            quotes, failed = reits.fetch_quotes(
                [etf] + list(sources.get("reit_tickers", [])), av_key, client)
            save_quotes(conn, quotes)
            if failed:
                problems.append(f"reits: failed {', '.join(failed)}")
            sleep(AV_SPACING_SECONDS)
            vnq_yield = reits.fetch_etf_yield(etf, av_key, client)
        except Exception as exc:
            problems.append(f"reits: {_err(exc)}")
    return odds, quotes, vnq_yield


def _make_chart(conn, run_date: date, tmp: Path, problems: list[str]) -> Path | None:
    try:
        points = get_rates(conn, "DGS10", run_date - timedelta(days=CHART_DAYS))
        if not points:
            return None
        return rate_chart(points, tmp / "chart.png", "10-Year Treasury Yield")
    except Exception as exc:
        problems.append(f"chart: {_err(exc)}")
        return None


def _ensure_footer(md: str) -> str:
    lines = [ln.strip() for ln in md.splitlines() if ln.strip()]
    if lines and lines[-1] == FOOTER:
        return md
    return md.rstrip("\n") + "\n\n" + FOOTER + "\n"


def _write_issue(factsheet: dict, problems: list[str], claude) -> str:
    """Draft, edit, check. Falls back to a fact-sheet-only issue if drafting fails."""
    try:
        md = draft.write(factsheet, run=claude)
    except Exception as exc:  # LLMError or anything unexpected: same fallback
        problems.append(f"claude: {_err(exc)}")
        return fallback_markdown(factsheet)
    try:
        md = draft.edit(md, factsheet, run=claude)
    except Exception as exc:
        problems.append(f"claude: {_err(exc)}")
    try:
        for p in check_issue(md, factsheet, load_banned()):
            problems.append(f"check/{p['kind']}: {p['detail']}")
    except Exception as exc:
        problems.append(f"check: {_err(exc)}")
    return _ensure_footer(md)  # after the check, so a missing footer is still flagged


def _scrub(text: str) -> tuple[str, str | None]:
    """Replace any leftover {{...}} or stray double braces with n/a. Returns (text, snippet)."""
    snippet = None

    def sub(m: re.Match) -> str:
        nonlocal snippet
        snippet = snippet or m.group(0)[:40]
        return "n/a"

    return STRAY_BRACES.sub(sub, text), snippet


def _stub_markdown(problems: list[str]) -> str:
    lines = ["# Pipeline error: fact sheet unavailable", ""]
    lines += [f"- {p}" for p in problems]
    return "\n".join(lines + ["", FOOTER, ""])


def _build_body(conn, run_date, odds, quotes, vnq_yield, problems, claude):
    """Returns (markdown, factsheet or None)."""
    try:
        factsheet = build_factsheet(conn, run_date, odds, quotes, problems, vnq_yield)
    except Exception as exc:
        problems.append(f"factsheet: {_err(exc)}")
        return _stub_markdown(problems), None
    md = _write_issue(factsheet, problems, claude)
    values = {**factsheet["values"], "DATE": run_date.isoformat()}
    try:
        md, missing = fill(md, values)
        problems += [f"fill: missing {name}" for name in missing]
    except Exception as exc:
        problems.append(f"fill: {_err(exc)}")
    return md, factsheet


def _preview(md, factsheet, problems, chart, run_date) -> str | None:
    """HTML preview; a render failure is a problem line, never a failed delivery."""
    try:
        # Embed the chart so the preview survives being opened alone, emailed or pasted.
        chart_rel = ("data:image/png;base64," + base64.b64encode(Path(chart).read_bytes()).decode()
                     if chart is not None else None)
        return render_issue_html(md, factsheet, problems, chart_rel, run_date=run_date)
    except Exception as exc:
        problems.append(f"html: {_err(exc)}")
        return None


def run(args, *, client=None, claude=llm.run_claude, gh=deliver_mod.run_gh, now=None,
        repo_root=REPO_ROOT) -> int:
    now = (now or datetime.now(ET)).astimezone(ET)
    if not args.force and not args.date and now.hour not in GATE_HOURS:
        print(f"Not 5-6 AM ET (it is {now:%H:%M}); skipping. Use --force to run anyway.")
        return 0
    run_date = date.fromisoformat(args.date) if args.date else now.date()

    own_client = client is None
    conn = None
    problems: list[str] = []
    try:
        client = client or http_client()
        conn = connect(str(args.db))
        if not args.force:
            row = conn.execute("SELECT gh_issue FROM issues WHERE date=?",
                               (run_date.isoformat(),)).fetchone()
            if row is not None and row["gh_issue"] is not None:
                print(f"already delivered {run_date.isoformat()}; skipping")
                return 0
        sources = load_sources()
        odds, quotes, vnq_yield = _collect(conn, sources, run_date, client, problems)

        try:
            classify.classify(conn, run=claude)
        except Exception as exc:
            problems.append(f"classify: {_err(exc)}")

        md, factsheet = _build_body(conn, run_date, odds, quotes, vnq_yield, problems, claude)
        md, snippet = _scrub(md)
        if snippet:
            problems.append(f"fill: stray braces {snippet}")

        with tempfile.TemporaryDirectory() as tmp:
            chart = _make_chart(conn, run_date, Path(tmp), problems)
            if chart is None:
                md = CHART_LINE.sub("", md)
            problems[:] = [_scrub(p)[0] for p in problems]
            html = _preview(md, factsheet, problems, chart, run_date)
            term = factsheet["term"]["term"] if factsheet else None
            try:
                path = deliver_mod.deliver(
                    conn, run_date, md, problems, chart, repo_root, day_type(run_date),
                    term, gh=gh, dry_run=args.dry_run, html=html)
            except Exception as exc:
                print(f"delivery failed: {_err(exc)}", file=sys.stderr)
                return 1
    except Exception as exc:
        print(f"pipeline error before delivery: {_err(exc)}", file=sys.stderr)
        return 1
    finally:
        if own_client and client is not None:
            client.close()
        if conn is not None:
            conn.close()
    print(f"Delivered {path} ({len(problems)} problem(s))")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Draft the daily CRE Blurb issue.")
    p.add_argument("--date", help="run date YYYY-MM-DD (skips the 5 AM gate)")
    p.add_argument("--dry-run", action="store_true", help="write the file, skip the GitHub Issue")
    p.add_argument("--force", action="store_true", help="ignore the 5-6 AM ET hour check and the already-delivered skip")
    p.add_argument("--db", default=str(REPO_ROOT / "cre.db"))
    return run(p.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
