"""Fact sheet: the structured input Claude writes from.

Every number is pre-formatted into `values` as a string; later code substitutes
them into {{PLACEHOLDERS}}, so the model never writes numbers. Missing data is "n/a".
"""

import json
import re
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

from src.collect.calendar import week_ahead_markdown
from src.config import ET, load_sources
from src.markets import pick as pick_markets
from src.models import FedOdds, ReitQuote
from src.rates import fmt_bps, latest_with_change, lookback_days
from src.store import get_quotes, get_rates, recent_items

NA = "n/a"
CALENDAR_DOWN = "Fed calendar unavailable today."
RATE_SERIES = ("DGS10", "DGS5", "SOFR", "DFF")
# Extra FRED series: (value key, FRED id, value format, as-of format or None).
#   pct: "4.83%" with a bps change; spread: "45 bps" with a bps change;
#   billions: "$2,981B" with a % change vs the prior observation.
#   As-of "day" -> KEY_ASOF "Oct 1"; "quarter" -> KEY_ASOF "Q1 2026".
EXTRA_SERIES = (
    ("DGS2", "DGS2", "pct", None),
    ("T10Y2Y", "T10Y2Y", "spread", None),
    ("HY_OAS", "BAMLH0A0HYM2", "pct", None),
    ("MORTGAGE30US", "MORTGAGE30US", "pct", "day"),
    ("BANK_CRE_LOANS", "CREACBW027SBOG", "billions", "day"),
    ("BANK_CRE_DQ", "DRCRELEXFACBS", "pct", "quarter"),
)
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


# AI section: tool and use-case stories go ahead of data center / power-grid stories.
AI_USE_CASE = re.compile(
    r"\b(?:tools?|platforms?|AI-powered|chatbots?|assistants?|agents?|software|apps?|"
    r"launch(?:es|ed)?|underwrit\w*|lease abstract\w*|valuations?|apprais\w*|"
    r"property management|leasing|broker\w*|proptech|automat\w*|adopt\w*|startups?|"
    r"raises?)\b", re.I)
AI_INFRA = re.compile(r"\b(?:data cent(?:er|re)s?|power grid|grid|electricity|megawatts?|"
                      r"hyperscale\w*|power)\b", re.I)


def _ai_bucket(row) -> int:
    text = f"{row['title']} {row['summary'] or ''}"
    if AI_INFRA.search(text) and not AI_USE_CASE.search(row["title"]):
        return 1
    return 0


def _ai_rank(rows: list) -> list:
    """Stable: tool/use-case stories first, then infrastructure, each kept in rank order."""
    return sorted(rows, key=_ai_bucket)


def _pct(value: float) -> str:
    return f"{round(value, 1) + 0.0:+.1f}%"  # + 0.0 turns -0.0 into 0.0


def _day(d: date) -> str:
    return f"{d:%b} {d.day}"


def _quarter(d: date) -> str:
    return f"Q{(d.month - 1) // 3 + 1} {d.year}"


def fmt_spread(value_pct: float) -> str:
    """A spread in percentage points as bps: 0.45 -> "45 bps", -0.12 -> "-12 bps"."""
    n = round(value_pct * 100)
    return f"{n} bps"


def _extra_values(conn: sqlite3.Connection, run_date: date) -> dict:
    values = {}
    for key, sid, kind, asof in EXTRA_SERIES:
        points = get_rates(conn, sid, run_date - timedelta(days=lookback_days(sid)))
        if not points:
            values[key] = values[f"{key}_CHG"] = NA
            if asof:
                values[f"{key}_ASOF"] = NA
            continue
        last, chg = latest_with_change(points)
        if kind == "billions":
            values[key] = f"${last.value:,.0f}B"
            prev = sorted(points, key=lambda p: p.date)[-2].value if len(points) > 1 else None
            values[f"{key}_CHG"] = _pct((last.value / prev - 1) * 100) if prev else NA
        else:
            values[key] = fmt_spread(last.value) if kind == "spread" else f"{last.value:.2f}%"
            values[f"{key}_CHG"] = fmt_bps(chg)
        if asof:
            values[f"{key}_ASOF"] = _quarter(last.date) if asof == "quarter" else _day(last.date)
    return values


def _rate_values(conn: sqlite3.Connection, run_date: date) -> tuple[dict, float | None]:
    values = {}
    dgs10: float | None = None
    for series in RATE_SERIES:
        points = get_rates(conn, series, run_date - timedelta(days=lookback_days(series)))
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
    values.update(_extra_values(conn, run_date))
    return values, dgs10


FED_HOLD_WORDS = re.compile(r"no change|hold|unchanged|pause", re.I)
FED_CUT_WORDS = re.compile(r"decrease|cut|lower", re.I)
FED_HIKE_WORDS = re.compile(r"increase|hike|raise", re.I)


def fed_bucket(label: str) -> str | None:
    """'25 bps decrease' -> 'cut', 'No change' -> 'hold', '25+ bps increase' -> 'hike'."""
    if FED_HOLD_WORDS.search(label):
        return "hold"
    if FED_CUT_WORDS.search(label):
        return "cut"
    if FED_HIKE_WORDS.search(label):
        return "hike"
    return None


def _fed_values(odds: FedOdds | None, run_date: date) -> tuple[dict, list[str]]:
    """Fed values plus the names of any outcomes that fit none of cut/hold/hike."""
    # Meeting date comes from the FOMC calendar, independent of the odds feed.
    nxt = next((d for d in sorted(_load_yaml("config/fomc.yaml")) if d >= run_date), None)
    values = {"FED_MEETING": _day(nxt) if nxt else NA, "FED_TOP": NA,
              "FED_CUT": NA, "FED_HOLD": NA, "FED_HIKE": NA}
    unmapped: list[str] = []
    if odds is not None and odds.outcomes:
        label, prob = odds.outcomes[0]
        values["FED_TOP"] = f"{label} {prob * 100:.1f}%"
        sums = {"cut": 0.0, "hold": 0.0, "hike": 0.0}
        for label, prob in odds.outcomes:
            bucket = fed_bucket(label)
            if bucket is None:
                unmapped.append(label)
            else:
                sums[bucket] += prob
        for bucket, total in sums.items():
            values[f"FED_{bucket.upper()}"] = f"{total * 100:.1f}%"
    return values, unmapped


MOVER_NOTE = "No company-specific news today; it may have moved with other {group} REITs."


def _reit_names(values: dict, info: dict) -> None:
    """REIT_UP_NAME "NNN REIT (NNN)" and REIT_UP_TYPE "Owns single-tenant retail..." (same
    for REIT_DOWN) from config/sources.yaml `reit_info`, for the Market Summary rows."""
    for key in ("REIT_UP", "REIT_DOWN"):
        raw = values.get(key, NA)
        ticker = raw.split()[0] if raw != NA else None
        meta = (info or {}).get(ticker) or {}
        values[f"{key}_NAME"] = (f"{meta['name']} ({ticker})" if meta.get("name")
                                 else ticker or NA)
        values[f"{key}_TYPE"] = meta.get("type") or NA


def _mentions(row, name: str | None, ticker: str) -> bool:
    text = f"{row['title']} {row['summary'] or ''}"
    if name and re.search(r"\b" + re.escape(name) + r"\b", text, re.I):
        return True
    # Tickers: case-sensitive, and not one or two letters ("O" would match everything).
    return len(ticker) >= 3 and re.search(r"\b" + re.escape(ticker) + r"\b", text) is not None


def _mover_news(values: dict, info: dict, window: list) -> dict:
    """{"up": story|None, "down": story|None} for movers that exist. A story is the
    best-ranked item in the window naming the company or ticker. With no story, code
    fills MOVER_UP_NOTE / MOVER_DOWN_NOTE so the model never guesses a reason."""
    news = {}
    for side, key in (("up", "REIT_UP"), ("down", "REIT_DOWN")):
        raw = values.get(key, NA)
        if raw == NA:
            values[f"MOVER_{side.upper()}_NOTE"] = NA
            continue
        ticker = raw.split()[0]
        meta = (info or {}).get(ticker) or {}
        row = next((r for r in window if _mentions(r, meta.get("name"), ticker)), None)
        news[side] = _story(row) if row is not None else None
        values[f"MOVER_{side.upper()}_NOTE"] = MOVER_NOTE.format(
            group=meta.get("group") or "real estate")
    return news


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


def _week_ahead(run_date: date, events: list[dict] | None) -> dict:
    end = run_date + timedelta(days=7)
    meetings = [d for d in _load_yaml("config/fomc.yaml") if run_date < d <= end]
    return {"fomc_dates": [_day(d) for d in meetings], "FED_TOP": "FED_TOP",  # placeholder name
            "events": list(events or []), "list": "WEEK_AHEAD"}  # placeholder name


def _cmbs_values(cmbs: dict | None) -> dict:
    """Trepp CMBS delinquency values (see src/trepp.py). CMBS_DQ only when Trepp states it."""
    if not cmbs:
        return {"CMBS_DQ_CHG": NA, "CMBS_DQ_MONTH": NA}
    return dict(cmbs)


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
                    vnq_yield: float | None, *, cmbs: dict | None = None,
                    week_events: list[dict] | None = None) -> dict:
    dtype = day_type(run_date)
    anchor = _anchor(run_date)
    sources = load_sources()
    hours = sources["lookback_hours"]
    lookback = hours["monday"] if run_date.weekday() == 0 else hours["default"]

    values, dgs10 = _rate_values(conn, run_date)
    fed, unmapped = _fed_values(odds, run_date)
    values.update(fed)
    problems.extend(f"polymarket: unmapped outcome {name!r}" for name in unmapped)
    values.update(_market_values(quotes))
    _reit_names(values, sources.get("reit_info") or {})
    values.update(_cmbs_values(cmbs))
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
        "ai": [_story(r) for r in _ai_rank(
            [r for r in window
             if r["section"] == "ai" and (r["importance"] or 0) >= AI_MIN_IMPORTANCE])][:1],
        "term": _pick_term(conn, run_date),
    }

    if dtype in ("weekday", "friday"):
        sheet["mover_news"] = _mover_news(values, sources.get("reit_info") or {}, window)
        # Market Watch: one story per region, never one already used above.
        taken = {s["url"] for key in ("top", "quick_hits", "debt", "ai") for s in sheet[key]}
        picks = pick_markets([r for r in window if r["section"] not in ("other", "ai")], taken)
        sheet["markets"] = {region: (_story(row) if row is not None else None)
                            for region, row in picks.items()}

    if dtype == "saturday":
        week = _by_rank(recent_items(conn, anchor - timedelta(days=7)))
        sheet["week_top"] = [_story(r) for r in week][:WEEK_TOP_COUNT]
        sheet["ai_week"] = [_story(r) for r in _ai_rank(
            [r for r in week if r["section"] == "ai"])][:AI_WEEK_COUNT]
    elif dtype == "sunday":
        sheet["week_ahead"] = _week_ahead(run_date, week_events)
        values["WEEK_AHEAD"] = (CALENDAR_DOWN if week_events is None
                                else week_ahead_markdown(week_events))
        reit_values, sheet["reit_week"] = _reit_week(conn, run_date, quotes)
        values.update(reit_values)
    return sheet
