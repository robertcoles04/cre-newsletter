"""Pipeline orchestrator: collect, classify, draft, check, fill, deliver.

One failing source never stops the issue; every failure becomes a line in `problems`,
which deliver() prints as a banner at the top of the draft.
"""

import argparse
import base64
import html
import os
import re
import sys
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from src import classify, deliver as deliver_mod, draft, llm, trepp
from src import chart as charts_mod
from src.chart import rate_chart
from src.checks import FOOTER, check_issue, load_banned
from src.collect import calendar, google_news, polymarket, reits, rss
from src.config import ET, env, http_client, load_sources
from src.factsheet import _day, build_factsheet, day_type, next_meeting
from src.cleanup import tidy
from src.fill import fill
from src.markets import HEADINGS, REGIONS
from src.rates import collect_rates
from src.render_html import SUMMARY_ROWS, has_row, render_issue_html, summary_label
from src.models import RatePoint
from src.store import (REPEAT_DAYS, connect, get_quotes, get_rates, record_used_stories,
                       save_items, save_quotes, save_rates)

REPO_ROOT = Path(__file__).resolve().parent.parent
COLLECT_HOURS = 78  # store keeps everything; the fact sheet applies the day's lookback
CHART_DAYS = 45
# Cron fires at 09:07 and 10:07 UTC (5-6 AM ET) plus backups at 09:13-11:43 UTC, but GitHub
# often starts scheduled runs hours late. Accept any start from 5 AM to 5:59 PM ET; the already-delivered check
# keeps it to one issue per day.
GATE_HOURS = range(5, 18)
CHART_LINE = re.compile(r"^[ \t]*!\[Chart of the Day\]\([^)\n]*\)[ \t]*\n?", re.M)
STRAY_BRACES = re.compile(r"\{\{.*?\}\}|\{\{|\}\}", re.S)


def _step_output(**values) -> None:
    """Hand values to later GitHub Actions steps (no-op outside Actions)."""
    out = os.environ.get("GITHUB_OUTPUT")
    if not out:
        return
    with open(out, "a", encoding="utf-8") as f:
        for key, value in values.items():
            f.write(f"{key}={value}\n")


def _err(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__


def _story_lines(stories: list[dict]) -> list[str]:
    # Feed text is untrusted: escape it so a title can never become raw HTML,
    # and keep only http(s) links.
    return [f"- [{html.escape(s['title'], quote=False)}]({s['url']}) "
            f"({html.escape(s['source'], quote=False)})" for s in stories
            if str(s.get("url", "")).lower().startswith(("http://", "https://"))]


def _market_lines(markets: dict) -> list[str]:
    out = []
    for region in REGIONS:
        story = markets.get(region)
        body = _story_lines([story]) if story else []
        if body:
            out += ["", f"### {HEADINGS[region]}", ""] + body
    return ["", "## Market Watch"] + out if out else []


def fallback_markdown(factsheet: dict) -> str:
    """Fact-sheet-only issue used when Claude is unavailable.

    Uses {{NAMES}} like the templates; run it through fill() afterwards.
    """
    lines = ["# Claude unavailable: fact sheet only", "", "## The Numbers", ""]
    values = factsheet.get("values", {})
    if "RATES_ASOF" in values:
        lines += ["*Rates as of {{RATES_ASOF}} close.*", ""]
    for label, key, chg in SUMMARY_ROWS:
        if not has_row(key, chg, values):
            continue
        label = summary_label(label, values)
        if key not in values:  # change only (CMBS delinquency without a stated rate)
            lines.append(f"- **{label}:** {{{{{chg}}}}}")
            continue
        tail = f" ({{{{{chg}}}}})" if chg and chg in values else ""
        lines.append(f"- **{label}:** {{{{{key}}}}}{tail}")
    for side in ("up", "down"):
        if side not in (factsheet.get("mover_news") or {}):
            continue
        story = factsheet["mover_news"][side]
        why = (_story_lines([story])[0][2:] if story and _story_lines([story])
               else f"{{{{MOVER_{side.upper()}_NOTE}}}}")
        lines += ["", f"**Why {{{{REIT_{side.upper()}_NAME}}}} moved:** {why}"]
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
        if key == "top":  # Market Watch sits after Top Stories
            lines += _market_lines(factsheet.get("markets") or {})
    if factsheet.get("day_type") == "sunday" and "WEEK_AHEAD" in values:
        lines += ["", "## Week Ahead", "", "{{WEEK_AHEAD}}"]
    term = factsheet.get("term") or {}
    if term.get("term"):
        lines += ["", "## Term of the Day", "",
                  f"- **{term['term']}:** {term.get('definition_hint', '')}"]
    lines += ["", FOOTER, ""]
    return "\n".join(lines)


# Alpha Vantage free tier: space calls out so the ETF call isn't rate limited.
sleep = time.sleep
AV_SPACING_SECONDS = 13


def _week_events(client, run_date: date, fred_key: str | None,
                 problems: list[str]) -> list[dict] | None:
    """Sunday Week Ahead: Fed calendar events merged with FRED's major data releases,
    de-duplicated and sorted. None only when both sources fail."""
    found: list[list[dict]] = []
    try:
        found.append(calendar.fetch_week(client, run_date))
    except Exception as exc:
        problems.append(f"calendar: {_err(exc)}")
    if fred_key:
        try:
            found.append(calendar.fetch_fred_releases(client, fred_key, run_date))
        except Exception as exc:
            problems.append(f"calendar/fred: {_err(exc)}")
    if not found:
        return None
    return calendar.week_window([ev for evs in found for ev in evs], run_date)


# The VNQ dividend yield is kept in the rates table under this name (dated with the
# quotes' trading day) so a same-day rerun can reuse it without another API call.
VNQ_YIELD_SERIES = "VNQ_YIELD"


def _stored_quotes(conn, tickers: list[str], days: list[date]):
    """(quotes, day) for the latest accepted trading day on which the DB already holds
    quotes for most (more than half) of the tickers; ([], None) otherwise."""
    stored = get_quotes(conn, min(days))
    for day in days:  # latest first
        on_day = {q.ticker: q for q in stored if q.date == day and q.ticker in tickers}
        if len(on_day) * 2 > len(tickers):
            return [on_day[t] for t in tickers if t in on_day], day
    return [], None


def _collect_reits(conn, sources: dict, run_date: date, client, problems: list[str],
                   now: datetime | None):
    """(quotes, vnq yield). Quotes must be dated the expected trading day (see
    reits.accepted_days); an older one is reported as stale and dropped, never retried.
    A --force or second run the same day reuses the stored quotes for that trading day
    instead of spending the Alpha Vantage quota again."""
    etf = sources.get("reit_etf")
    tickers = ([etf] if etf else []) + list(sources.get("reit_tickers", []))
    days = reits.accepted_days(run_date, now)
    if tickers:
        reused, day = _stored_quotes(conn, tickers, days)
        if reused:
            print(f"reits: reused stored quotes for {day.isoformat()}")
            have = {q.ticker for q in reused}
            missing = [t for t in tickers if t not in have]
            if missing:
                problems.append(f"reits: no stored quote for {', '.join(missing)}")
            stored_yield = [p for p in get_rates(conn, VNQ_YIELD_SERIES, day) if p.date == day]
            return reused, (stored_yield[-1].value if stored_yield else None)

    av_key = env("ALPHA_VANTAGE_API_KEY", required=False)
    if not av_key:
        problems.append("reits: missing ALPHA_VANTAGE_API_KEY")
        return [], None
    quotes, vnq_yield = [], None
    try:
        fetched, failed = reits.fetch_quotes(tickers, av_key, client)
        quotes, stale, day = reits.split_stale(fetched, days)
        save_quotes(conn, quotes)
        if failed:
            problems.append(f"reits: failed {', '.join(failed)}")
        problems.extend(f"reits: {q.ticker} quote is stale ({q.date.isoformat()})"
                        for q in stale)
        sleep(AV_SPACING_SECONDS)
        vnq_yield = reits.fetch_etf_yield(etf, av_key, client)
        if vnq_yield is not None and day is not None:
            save_rates(conn, [RatePoint(VNQ_YIELD_SERIES, day, vnq_yield)])
    except Exception as exc:
        problems.append(f"reits: {_err(exc)}")
    return quotes, vnq_yield


def _collect(conn, sources: dict, run_date: date, client, problems: list[str],
             now: datetime | None = None):
    """Run every collector. Returns (fed odds, reit quotes, vnq yield, extras), where
    extras holds "cmbs" (Trepp values or None) and "week_events" (Sunday only). `now` is
    the run's start time (decides whether today's close counts and dates the Fed odds)."""
    anchor = datetime(run_date.year, run_date.month, run_date.day, 5, tzinfo=ET)
    since = anchor - timedelta(hours=COLLECT_HOURS)
    extras: dict = {"cmbs": None, "week_events": None}

    for f in sources.get("feeds", []):
        # A feed may keep older items (Trepp: its monthly CMBS report); the fact sheet
        # still applies the day's lookback to stories.
        f_since = anchor - timedelta(days=f["collect_days"]) if f.get("collect_days") else since
        try:
            save_items(conn, rss.fetch_feed(f["name"], f["url"], f["priority"], f_since, client))
        except Exception as exc:
            problems.append(f"feed/{f['name']}: {_err(exc)}")

    try:
        extras["cmbs"] = trepp.cmbs_values(conn, run_date)
        if extras["cmbs"] is None:
            problems.append("trepp: no CMBS delinquency headline in the last 45 days")
    except Exception as exc:
        problems.append(f"trepp: {_err(exc)}")

    fred_key = env("FRED_API_KEY", required=False)
    if day_type(run_date) == "sunday":
        extras["week_events"] = _week_events(client, run_date, fred_key, problems)

    for g in sources.get("google_news", []):
        try:
            items = google_news.fetch(g["name"], g["query"], g["priority"], since, client,
                                      when=g.get("when", "2d"))
            for it in items:
                it.region = g.get("region", "")
            save_items(conn, items)
        except Exception as exc:
            problems.append(f"news/{g['name']}: {_err(exc)}")

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

    # Odds only from the market for the FOMC meeting the fact sheet names (fomc.yaml).
    odds = None
    meeting = next_meeting(run_date)
    try:
        odds = polymarket.fetch_fed_odds(client, meeting)
        if odds is None:
            problems.append(f"polymarket: no market for {_day(meeting)}" if meeting
                            else "polymarket: no upcoming meeting in config/fomc.yaml")
        else:
            odds.as_of = now.astimezone(ET).date() if now else run_date
    except Exception as exc:
        problems.append(f"polymarket: {_err(exc)}")

    quotes, vnq_yield = _collect_reits(conn, sources, run_date, client, problems, now)
    return odds, quotes, vnq_yield, extras


def _make_chart(conn, run_date: date, tmp: Path, problems: list[str]) -> Path | None:
    try:
        points = get_rates(conn, "DGS10", run_date - timedelta(days=CHART_DAYS))
        if not points:
            return None
        return rate_chart(points, tmp / "chart.png", "10-Year Treasury Yield")
    except Exception as exc:
        problems.append(f"chart: {_err(exc)}")
        return None


CURVE_SERIES = (("2Y", "DGS2"), ("5Y", "DGS5"), ("10Y", "DGS10"), ("30Y", "DGS30"))
CURVE_AGO_DAYS = 30
MORTGAGE_DAYS = 190


def _meta(path: Path, alt: str, figsize, sm: Path | None = None, sm_size=None) -> dict:
    """Chart record for the page: PNG path, alt text, logical size, and the optional
    phone variant ("sm") of a full-width chart."""
    w, h = charts_mod.size_px(figsize)
    meta = {"path": path, "alt": alt, "width": w, "height": h}
    if sm is not None:
        sw, sh = charts_mod.size_px(sm_size)
        meta["sm"] = {"path": sm, "width": sw, "height": sh}
    return meta


def curve_points(conn, run_date: date) -> tuple[list, date | None, date | None]:
    """([(maturity, %, % about a month before that)], month-ago date, curve date).

    Every maturity is read on the SAME date: the latest date all maturities with data
    share, so the chart never mixes days. If they share no date, each uses its own latest
    and a maturity whose date differs from the 10Y's is labeled with it ("2Y (Oct 2)").
    Month ago = the last observation at least CURVE_AGO_DAYS before the curve date (the
    month-ago date shown is the 10Y's)."""
    series = []
    for label, sid in CURVE_SERIES:
        pts = sorted(get_rates(conn, sid, run_date - timedelta(days=CHART_DAYS)),
                     key=lambda p: p.date)
        series.append((label, sid, pts))
    have = [pts for _, _, pts in series if pts]
    common = set.intersection(*({p.date for p in pts} for pts in have)) if have else set()
    now_date = max(common) if common else None
    if now_date is None:  # no shared date: the 10Y's latest names the curve
        ten = next((pts for _, sid, pts in series if sid == "DGS10" and pts), None)
        now_date = ten[-1].date if ten else (have[0][-1].date if have else None)
    rows, ago_date = [], None
    for label, sid, pts in series:
        if not pts:
            rows.append((label, None, None))
            continue
        at = [p for p in pts if p.date <= now_date] if common else pts
        last = at[-1]
        if last.date != now_date:
            label = f"{label} ({last.date:%b} {last.date.day})"
        older = [p for p in pts if p.date <= last.date - timedelta(days=CURVE_AGO_DAYS)]
        ago = older[-1] if older else None
        if ago is not None and (sid == "DGS10" or ago_date is None):
            ago_date = ago.date
        rows.append((label, last.value, ago.value if ago else None))
    return rows, ago_date, now_date


def _make_extra_charts(conn, run_date: date, factsheet: dict | None, tmp: Path,
                       problems: list[str]) -> dict:
    """Yield curve, mortgage trend, Fed odds bar and REIT scoreboard. Each is optional:
    too little data skips it quietly; an error skips it with a problem note."""
    out: dict = {}
    if factsheet is None:
        return out
    values = factsheet.get("values") or {}

    def curve():
        rows, ago_date, now_date = curve_points(conn, run_date)
        path = charts_mod.yield_curve(rows, tmp / "curve.png", now_date)
        return path and _meta(path, charts_mod.curve_alt(rows, ago_date, now_date),
                              charts_mod.PAIR_FIGSIZE)

    def mortgage():
        pts = get_rates(conn, "MORTGAGE30US", run_date - timedelta(days=MORTGAGE_DAYS))
        path = charts_mod.mortgage_trend(pts, tmp / "mortgage.png")
        return path and _meta(path, charts_mod.mortgage_alt(pts), charts_mod.PAIR_FIGSIZE)

    def fed():
        odds = {k: values.get(f"FED_{k.upper()}") for k in ("cut", "hold", "hike")}
        path = charts_mod.fed_odds_bar(odds, tmp / "fed.png")
        if path is None:
            return None
        meeting = values.get("FED_MEETING")
        meeting = meeting if meeting and meeting != "n/a" else None
        return _meta(path, charts_mod.fed_alt(odds, meeting), charts_mod.FED_FIGSIZE,
                     charts_mod.fed_odds_bar(odds, tmp / "fed-sm.png", narrow=True),
                     charts_mod.FED_NARROW)

    def reits():
        moves = factsheet.get("reit_moves") or []
        path = charts_mod.reit_scoreboard(moves, tmp / "reits.png")
        if path is None:
            return None
        asof = values.get("REIT_ASOF")
        asof = asof if asof and asof != "n/a" else None
        return _meta(path, charts_mod.reit_alt(moves, asof), charts_mod.reit_figsize(moves),
                     charts_mod.reit_scoreboard(moves, tmp / "reits-sm.png", narrow=True),
                     charts_mod.reit_figsize(moves, narrow=True))

    for name, build in (("curve", curve), ("mortgage", mortgage), ("fed", fed),
                        ("reits", reits)):
        try:
            made = build()
            if made:
                out[name] = made
        except Exception as exc:
            problems.append(f"chart/{name}: {_err(exc)}")
    return out


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
    md = ensure_week_ahead(md, factsheet)  # after the check, which flags a missing list
    return _ensure_footer(md)  # after the check, so a missing footer is still flagged


WEEK_AHEAD_LINE = "{{WEEK_AHEAD}}"


def ensure_week_ahead(md: str, factsheet: dict) -> str:
    """Sunday safety net: if the draft lost the code-filled {{WEEK_AHEAD}} list, put it back
    at the end of "## Week Ahead", or add that section before Term of the Day / the end."""
    if (factsheet.get("day_type") != "sunday" or "WEEK_AHEAD" not in factsheet.get("values", {})
            or WEEK_AHEAD_LINE in md):
        return md
    lines = md.rstrip("\n").split("\n")
    heads = [i for i, ln in enumerate(lines) if ln.startswith("## ")]
    week = next((i for i in heads if lines[i][3:].strip() == "Week Ahead"), None)
    if week is not None:
        nxt = next((i for i in heads if i > week), len(lines))
        while nxt > week + 1 and not lines[nxt - 1].strip():
            nxt -= 1
        lines[nxt:nxt] = ["", WEEK_AHEAD_LINE]
    else:
        at = next((i for i in heads if lines[i][3:].strip() == "Term of the Day"), None)
        if at is None:
            at = next((i for i in range(len(lines)) if lines[i].strip() == FOOTER), len(lines))
        lines[at:at] = ["## Week Ahead", "", WEEK_AHEAD_LINE, ""]
    return "\n".join(lines) + "\n"


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


def _backfill_used_stories(conn, repo_root: Path, run_date: date) -> None:
    """Record links from recent issues/*.md that the DB does not know yet (issues made
    before used_stories existed, or edited on github.com), so they are never repeated."""
    known = {r[0] for r in conn.execute("SELECT DISTINCT issue_date FROM used_stories")}
    for path in sorted((Path(repo_root) / "issues").glob("????-??-??.md")):
        try:
            day = date.fromisoformat(path.stem)
        except ValueError:
            continue
        if day >= run_date or (run_date - day).days > REPEAT_DAYS or path.stem in known:
            continue
        record_used_stories(conn, day, path.read_text(encoding="utf-8-sig"))


def _build_body(conn, run_date, odds, quotes, vnq_yield, problems, claude, extras=None):
    """Returns (markdown, factsheet or None)."""
    extras = extras or {}
    try:
        factsheet = build_factsheet(conn, run_date, odds, quotes, problems, vnq_yield,
                                    cmbs=extras.get("cmbs"),
                                    week_events=extras.get("week_events"))
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


def _preview(md, factsheet, problems, chart, run_date, charts=None) -> str | None:
    """HTML preview; a render failure is a problem line, never a failed delivery."""
    try:
        # Embed the charts so the preview survives being opened alone, emailed or pasted.
        chart_rel = ("data:image/png;base64," + base64.b64encode(Path(chart).read_bytes()).decode()
                     if chart is not None else None)
        def uri(path) -> str:
            return "data:image/png;base64," + base64.b64encode(Path(path).read_bytes()).decode()

        embedded = {}
        for name, c in (charts or {}).items():
            embedded[name] = {**{k: v for k, v in c.items() if k not in ("path", "sm")},
                              "src": uri(c["path"])}
            if c.get("sm"):
                embedded[name]["sm"] = {"src": uri(c["sm"]["path"]),
                                        "width": c["sm"]["width"],
                                        "height": c["sm"]["height"]}
        return render_issue_html(md, factsheet, problems, chart_rel, run_date=run_date,
                                 charts=embedded)
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
        odds, quotes, vnq_yield, extras = _collect(conn, sources, run_date, client, problems,
                                                   now=now)

        try:
            classify.classify(conn, run=claude)
        except Exception as exc:
            problems.append(f"classify: {_err(exc)}")

        try:
            _backfill_used_stories(conn, repo_root, run_date)
        except Exception as exc:
            problems.append(f"repeat check: {_err(exc)}")
        md, factsheet = _build_body(conn, run_date, odds, quotes, vnq_yield, problems, claude,
                                    extras)
        md, snippet = _scrub(md)
        if snippet:
            problems.append(f"fill: stray braces {snippet}")
        md = tidy(md)  # after fill + scrub: drop empty subheads and "Why n/a moved" lines

        with tempfile.TemporaryDirectory() as tmp:
            chart = _make_chart(conn, run_date, Path(tmp), problems)
            if chart is None:
                md = CHART_LINE.sub("", md)
            charts = _make_extra_charts(conn, run_date, factsheet, Path(tmp), problems)
            if chart is not None:
                ten = get_rates(conn, "DGS10", run_date - timedelta(days=CHART_DAYS))
                try:  # phone variant; the page falls back to the wide chart without it
                    small = rate_chart(ten, Path(tmp) / "chart-sm.png",
                                       "10-Year Treasury Yield", narrow=True)
                except Exception as exc:
                    problems.append(f"chart/chart-sm: {_err(exc)}")
                    small = None
                charts = {"chart": _meta(chart, charts_mod.rate_alt(ten),
                                         charts_mod.RATE_FIGSIZE, small,
                                         charts_mod.RATE_NARROW), **charts}
            problems[:] = [_scrub(p)[0] for p in problems]
            html = _preview(md, factsheet, problems, chart, run_date, charts)
            term = factsheet["term"]["term"] if factsheet else None
            try:
                path = deliver_mod.deliver(
                    conn, run_date, md, problems, chart, repo_root, day_type(run_date),
                    term, gh=gh, dry_run=args.dry_run, html=html,
                    factsheet=factsheet, charts=charts)
            except Exception as exc:
                print(f"delivery failed: {_err(exc)}", file=sys.stderr)
                return 1
            if not args.dry_run:
                # daily.yml reads these to ask publish.yml to auto-publish this Issue.
                row = conn.execute("SELECT gh_issue FROM issues WHERE date=?",
                                   (run_date.isoformat(),)).fetchone()
                if row is not None and row["gh_issue"] is not None:
                    _step_output(date=run_date.isoformat(), issue=int(row["gh_issue"]))
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
