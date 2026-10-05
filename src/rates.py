"""Rates: collection (FRED with Treasury fallback) and basis-point math."""

import sqlite3
from datetime import date, timedelta

import httpx

from src.collect import fred, treasury
from src.models import RatePoint, SourceResult
from src.store import save_rates

LOOKBACK_DAYS = 45
# Weekly/quarterly series need a longer window so there are always 2 observations.
SERIES_LOOKBACK_DAYS = {
    "MORTGAGE30US": 190,     # weekly (Thursday); 26 weeks feed the mortgage trend chart
    "CREACBW027SBOG": 60,   # weekly, H.8 bank CRE loans
    "DRCRELEXFACBS": 400,    # quarterly, bank CRE delinquency rate
}


def lookback_days(series: str) -> int:
    return SERIES_LOOKBACK_DAYS.get(series, LOOKBACK_DAYS)


def latest_with_change(points: list[RatePoint]) -> tuple[RatePoint, int | None]:
    """Last observation and its change in bps vs the previous *observation*.

    Compared by observation, not calendar day, so weekends and holidays never
    produce a fake 0 bps. Returns None for the change if there is one point.
    """
    if not points:
        raise ValueError("no rate points")
    ordered = sorted(points, key=lambda p: p.date)
    last = ordered[-1]
    if len(ordered) < 2:
        return last, None
    return last, round((last.value - ordered[-2].value) * 100)


def fmt_bps(n: int | None) -> str:
    if n is None:
        return "n/a"
    if n == 0:
        return "0 bps"
    return f"{n:+d} bps"


def _treasury_points(today: date, start: date, client: httpx.Client) -> list[RatePoint]:
    years = [today.year]
    if today.timetuple().tm_yday <= 45:
        years.insert(0, today.year - 1)
    points = []
    for year in years:
        points.extend(treasury.fetch_yields(year, client))
    return [p for p in points if p.date >= start]


def collect_rates(conn: sqlite3.Connection, series: list[str], api_key: str | None,
                  client: httpx.Client, today: date) -> list[SourceResult]:
    """Fetch each series from FRED (45 days, longer for weekly/quarterly series).

    DGS10/DGS5 also consult Treasury.gov (once per run): FRED posts a business day
    late, so any Treasury point newer than FRED's latest is saved too. If FRED fails,
    Treasury is the fallback. Returns one SourceResult per series (name = series id).
    """
    start = today - timedelta(days=LOOKBACK_DAYS)  # Treasury fallback window
    results = []
    treasury_cache: list[RatePoint] = []
    treasury_error = ""
    treasury_tried = False

    def treasury_points() -> list[RatePoint]:
        nonlocal treasury_cache, treasury_error, treasury_tried
        if not treasury_tried:
            treasury_tried = True
            try:
                treasury_cache = _treasury_points(today, start, client)
            except Exception as exc:
                treasury_error = type(exc).__name__
        return treasury_cache

    for sid in series:
        is_treasury_series = sid in treasury.COLUMN_TO_SERIES.values()
        try:
            if not api_key:
                raise RuntimeError("no FRED api key")
            points = fred.fetch_series(
                sid, api_key, today - timedelta(days=lookback_days(sid)), client)
            if not points:
                raise RuntimeError("no observations")
            save_rates(conn, points)
            if is_treasury_series:
                latest = max(p.date for p in points)
                newer = [p for p in treasury_points()
                         if p.series == sid and p.date > latest]
                if newer:
                    save_rates(conn, newer)
            results.append(SourceResult(sid, True))
            continue
        except Exception as exc:  # any FRED failure -> try fallback
            error = type(exc).__name__

        if is_treasury_series:
            fallback = [p for p in treasury_points() if p.series == sid]
            if fallback:
                save_rates(conn, fallback)
                results.append(SourceResult(sid, True, "fallback: treasury"))
                continue
            error = f"fred: {error}; treasury: {treasury_error or 'no data'}"
        results.append(SourceResult(sid, False, error))
    return results
