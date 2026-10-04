"""Pipeline orchestrator: collect, classify, draft, check, fill, deliver.

One failing source never stops the issue; every failure becomes a line in `problems`,
which deliver() prints as a banner at the top of the draft.
"""

import argparse
import sys
import tempfile
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
from src.store import connect, get_rates, save_items, save_quotes

REPO_ROOT = Path(__file__).resolve().parent.parent
COLLECT_HOURS = 78  # store keeps everything; the fact sheet applies the day's lookback
CHART_DAYS = 45
GATE_HOUR = 5


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
    lines += [f"- **{name}:** {{{{{name}}}}}" for name in values]
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
        etf = sources["reit_etf"]
        try:
            quotes, failed = reits.fetch_quotes(
                [etf] + list(sources.get("reit_tickers", [])), av_key, client)
            save_quotes(conn, quotes)
            if failed:
                problems.append(f"reits: failed {', '.join(failed)}")
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


def _write_issue(factsheet: dict, problems: list[str], claude) -> str:
    """Draft, edit, check. Falls back to a fact-sheet-only issue if Claude fails."""
    try:
        md = draft.write(factsheet, run=claude)
    except llm.LLMError as exc:
        problems.append(f"claude: {_err(exc)}")
        return fallback_markdown(factsheet)
    try:
        md = draft.edit(md, factsheet, run=claude)
    except llm.LLMError as exc:
        problems.append(f"claude: {_err(exc)}")
    try:
        for p in check_issue(md, factsheet, load_banned()):
            problems.append(f"check/{p['kind']}: {p['detail']}")
    except Exception as exc:
        problems.append(f"check: {_err(exc)}")
    return md


def run(args, *, client=None, claude=llm.run_claude, gh=deliver_mod.run_gh, now=None,
        repo_root=REPO_ROOT) -> int:
    now = (now or datetime.now(ET)).astimezone(ET)
    if not args.force and not args.date and now.hour != GATE_HOUR:
        print(f"Not 5 AM ET (it is {now:%H:%M}); skipping. Use --force to run anyway.")
        return 0
    run_date = date.fromisoformat(args.date) if args.date else now.date()

    conn = connect(str(args.db))
    own_client = client is None
    client = client or http_client()
    problems: list[str] = []
    try:
        sources = load_sources()
        odds, quotes, vnq_yield = _collect(conn, sources, run_date, client, problems)

        try:
            classify.classify(conn, run=claude)
        except Exception as exc:
            problems.append(f"classify: {_err(exc)}")

        factsheet = build_factsheet(conn, run_date, odds, quotes, problems, vnq_yield)
        md = _write_issue(factsheet, problems, claude)
        values = {**factsheet["values"], "DATE": run_date.isoformat()}
        md, missing = fill(md, values)
        problems += [f"fill: missing {name}" for name in missing]

        with tempfile.TemporaryDirectory() as tmp:
            chart = _make_chart(conn, run_date, Path(tmp), problems)
            path = deliver_mod.deliver(
                conn, run_date, md, problems, chart, repo_root, day_type(run_date),
                factsheet["term"]["term"], gh=gh, dry_run=args.dry_run)
    except Exception as exc:
        print(f"delivery failed: {_err(exc)}", file=sys.stderr)
        return 1
    finally:
        if own_client:
            client.close()
        conn.close()
    print(f"Delivered {path} ({len(problems)} problem(s))")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Draft the daily CRE Blurb issue.")
    p.add_argument("--date", help="run date YYYY-MM-DD (skips the 5 AM gate)")
    p.add_argument("--dry-run", action="store_true", help="write the file, skip the GitHub Issue")
    p.add_argument("--force", action="store_true", help="run even if it is not 5 AM ET")
    p.add_argument("--db", default=str(REPO_ROOT / "cre.db"))
    return run(p.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
