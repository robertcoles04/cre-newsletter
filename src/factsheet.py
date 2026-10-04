"""Fact sheet: the structured input Claude writes from.

Every number is pre-formatted into `values` as a string; later code substitutes
them into {{PLACEHOLDERS}}, so the model never writes numbers. Missing data is "n/a".
"""

import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

from src.config import ET, load_sources
from src.models import FedOdds, ReitQuote
from src.rates import fmt_bps, latest_with_change
from src.store import get_quotes, get_rates, recent_items

NA = "n/a"
RATE_SERIES = ("DGS10", "DGS5", "SOFR", "DFF")
RATES_LOOKBACK_DAYS = 45
TOP_COUNT = {"weekday": 5, "friday": 3}
QUICK_HITS_COUNT = 8
DEBT_COUNT = 4
AI_MIN_IMPORTANCE = 7
WEEK_TOP_COUNT = 5
AI_WEEK_COUNT = 3
REIT_WEEK_PICKS = 3
VNQ_TICKER = "VNQ"


def day_type(d: date) -> str:
    return {4: "friday", 5: "saturday", 6: "sunday"}.get(d.weekday(), "weekday")


def _load_yaml(path: str):
    with open(Path(path), encoding="utf8") as f:
        return yaml.safe_load(f)


def _anchor(run_date: date) -> datetime:
    return datetime(run_date.year, run_date.month, run_date.day, 5, 0, tzinfo=ET)


def _story(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"], "title": row["title"], "source": row["source"],
        "url": row["url"], "summary": row["summary"] or "",
        "also_covered": json.loads(row["also_covered"] or "[]"),
        "section": row["section"],
    }


def _by_rank(rows: list[sqlite3.Row]) -> list[sqlite3.Row]:
    return sorted(rows, key=lambda r: (-(r["importance"] or 0), -(r["priority"] or 0)))


def _pct(value: float) -> str:
    return f"{round(value, 1) + 0.0:+.1f}%"  # + 0.0 turns -0.0 into 0.0


def _day(d: date) -> str:
    return f"{d:%b} {d.day}"


def _rate_values(conn: sqlite3.Connection, run_date: date) -> tuple[dict, float | None]:
    values = {}
    dgs10: float | None = None
    since = run_date - timedelta(days=RATES_LOOKBACK_DAYS)
    for series in RATE_SERIES:
        points = get_rates(conn, series, since)
        if not points:
            values[series] = values[f"{series}_CHG"] = NA
            if series == "DGS10":
                values["RATES_ASOF"] = NA
            continue
        last, chg = latest_with_change(points)
        values[series] = f"{last.value:.2f}%"
        values[f"{series}_CHG"] = fmt_bps(chg)
        if series == "DGS10":
            dgs10 = last.value
            values["RATES_ASOF"] = _day(last.date)
    return values, dgs10


def _fed_values(odds: FedOdds | None, run_date: date) -> dict:
    # Meeting date comes from the FOMC calendar, independent of the odds feed.
    nxt = next((d for d in sorted(_load_yaml("config/fomc.yaml")) if d >= run_date), None)
    values = {"FED_MEETING": _day(nxt) if nxt else NA, "FED_TOP": NA}
    if odds is not None and odds.outcomes:
        label, prob = odds.outcomes[0]
        values["FED_TOP"] = f"{label} {prob * 100:.1f}%"
    return values


def _market_values(quotes: list[ReitQuote]) -> dict:
    values = {"VNQ": NA, "VNQ_CHG": NA, "REIT_UP": NA, "REIT_DOWN": NA}
    vnq = next((q for q in quotes if q.ticker == VNQ_TICKER), None)
    if vnq:
        values["VNQ"] = f"${vnq.close:.2f}"
        values["VNQ_CHG"] = _pct(vnq.change_pct)
    others = [q for q in quotes if q.ticker != VNQ_TICKER]
    if others:
        best = max(others, key=lambda q: q.change_pct)
        worst = min(others, key=lambda q: q.change_pct)
        values["REIT_UP"] = f"{best.ticker} {_pct(best.change_pct)}"
        if len(others) > 1:
            values["REIT_DOWN"] = f"{worst.ticker} {_pct(worst.change_pct)}"
    return values


def _pick_term(conn: sqlite3.Connection, run_date: date) -> dict:
    terms = _load_yaml("config/terms.yaml")
    # Ignore today's own row so a same-day rerun picks the same term.
    rows = conn.execute(
        "SELECT date, term FROM issues WHERE term IS NOT NULL AND term != '' AND date != ?"
        " ORDER BY date", (run_date.isoformat(),)).fetchall()
    used = {r["term"] for r in rows}
    chosen = next((t for t in terms if t["term"] not in used), None)
    if chosen is None:  # every term used: wrap to the one after the most recent
        names = [t["term"] for t in terms]
        last = rows[-1]["term"]
        idx = (names.index(last) + 1) % len(terms) if last in names else 0
        chosen = terms[idx]
    return {"term": chosen["term"], "definition_hint": chosen["hint"]}


def _week_ahead(run_date: date) -> dict:
    end = run_date + timedelta(days=7)
    meetings = [d for d in _load_yaml("config/fomc.yaml") if run_date < d <= end]
    return {"fomc_dates": [_day(d) for d in meetings], "FED_TOP": "FED_TOP"}  # placeholder name


def _reit_week(conn: sqlite3.Connection, run_date: date,
               quotes: list[ReitQuote]) -> tuple[dict, dict]:
    """Best/worst non-VNQ tickers by % change, first -> last close in the window.

    The window starts 9 days back so a Sunday run's first close is the prior Friday.
    Needs at least 2 days per ticker. Returns (values, placeholder-name lists); the
    numbers live only in `values` as REITW_BEST_n / REITW_WORST_n. No ticker is in
    both lists (with <=6 tickers they split into top half and bottom half).
    """
    merged = {(q.ticker, q.date): q for q in get_quotes(conn, run_date - timedelta(days=9))}
    for q in quotes:
        merged[(q.ticker, q.date)] = q
    by_ticker: dict[str, list[ReitQuote]] = {}
    for q in merged.values():
        if q.ticker != VNQ_TICKER:
            by_ticker.setdefault(q.ticker, []).append(q)
    changes = []
    for ticker, qs in by_ticker.items():
        qs.sort(key=lambda q: q.date)
        if len(qs) >= 2 and qs[0].close:
            changes.append((ticker, (qs[-1].close / qs[0].close - 1) * 100))
    changes.sort(key=lambda c: -c[1])
    n_best = min(REIT_WEEK_PICKS, (len(changes) + 1) // 2)
    n_worst = min(REIT_WEEK_PICKS, len(changes) - n_best)
    best = changes[:n_best]
    worst = changes[len(changes) - n_worst:][::-1] if n_worst else []  # worst first
    values = {f"REITW_{side}_{i}": NA for side in ("BEST", "WORST")
              for i in range(1, REIT_WEEK_PICKS + 1)}
    names = {"best": [], "worst": []}
    for side, picks in (("best", best), ("worst", worst)):
        for i, (ticker, chg) in enumerate(picks, 1):
            key = f"REITW_{side.upper()}_{i}"
            values[key] = f"{ticker} {_pct(chg)}"
            names[side].append(key)
    return values, names


def build_factsheet(conn: sqlite3.Connection, run_date: date, odds: FedOdds | None,
                    quotes: list[ReitQuote], problems: list[str],
                    vnq_yield: float | None) -> dict:
    dtype = day_type(run_date)
    anchor = _anchor(run_date)
    hours = load_sources()["lookback_hours"]
    lookback = hours["monday"] if run_date.weekday() == 0 else hours["default"]

    values, dgs10 = _rate_values(conn, run_date)
    values.update(_fed_values(odds, run_date))
    values.update(_market_values(quotes))
    if vnq_yield is None:
        values["VNQ_YIELD"] = values["SPREAD_10Y"] = NA
    else:
        values["VNQ_YIELD"] = f"{vnq_yield * 100:.2f}%"
        values["SPREAD_10Y"] = (NA if dgs10 is None
                                else fmt_bps(round((vnq_yield * 100 - dgs10) * 100)))

    window = _by_rank(recent_items(conn, anchor - timedelta(hours=lookback)))
    news = [r for r in window if r["section"] in ("top", "deal")]
    n_top = TOP_COUNT.get(dtype, 0)
    top = news[:n_top]
    quick = news[n_top:n_top + QUICK_HITS_COUNT] if dtype in ("weekday", "friday") else []

    sheet = {
        "date": run_date.isoformat(),
        "day_type": dtype,
        "problems": list(problems),
        "values": values,
        "top": [_story(r) for r in top],
        "quick_hits": [_story(r) for r in quick],
        "debt": [_story(r) for r in window if r["section"] == "debt"][:DEBT_COUNT],
        "ai": [_story(r) for r in window
               if r["section"] == "ai" and (r["importance"] or 0) >= AI_MIN_IMPORTANCE][:1],
        "term": _pick_term(conn, run_date),
    }

    if dtype == "saturday":
        week = _by_rank(recent_items(conn, anchor - timedelta(days=7)))
        sheet["week_top"] = [_story(r) for r in week][:WEEK_TOP_COUNT]
        sheet["ai_week"] = [_story(r) for r in week if r["section"] == "ai"][:AI_WEEK_COUNT]
    elif dtype == "sunday":
        sheet["week_ahead"] = _week_ahead(run_date)
        reit_values, sheet["reit_week"] = _reit_week(conn, run_date, quotes)
        values.update(reit_values)
    return sheet
