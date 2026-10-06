"""Alpha Vantage REIT quote and ETF yield collector (plus which tickers it confirms)."""

import time
from collections import Counter
from datetime import date, datetime
from datetime import time as clock

import httpx

from src.config import ET
from src.freshness import is_trading_day, previous_trading_day
from src.models import ReitQuote

API_URL = "https://www.alphavantage.co/query"
# Free tier allows 5 calls/minute, so space calls out by 13 seconds.
CALL_SPACING_SECONDS = 13
# A burst failure is a per-minute limit: wait a full minute, then retry once.
RETRY_WAIT_SECONDS = 60
MAX_RETRIES = 3
MARKET_CLOSE = clock(16, 30)  # ET; after this the run date's own close counts as fresh


def _is_limit_or_error(data: dict) -> bool:
    return "Note" in data or "Information" in data or "Error Message" in data


def _fetch_one(ticker: str, api_key: str, client: httpx.Client) -> ReitQuote:
    resp = client.get(
        API_URL,
        params={"function": "GLOBAL_QUOTE", "symbol": ticker, "apikey": api_key},
    )
    resp.raise_for_status()
    data = resp.json()
    if _is_limit_or_error(data):
        raise ValueError(f"alpha vantage limit/error for {ticker}")
    gq = data.get("Global Quote")
    if not gq:
        raise ValueError(f"empty Global Quote for {ticker}")
    return ReitQuote(
        ticker=ticker,
        date=date.fromisoformat(gq["07. latest trading day"]),
        close=float(gq["05. price"]),
        change_pct=float(gq["10. change percent"].strip().rstrip("%")),
    )


def fetch_quotes(
    tickers: list[str], api_key: str, client: httpx.Client, sleep=time.sleep
) -> tuple[list[ReitQuote], list[str]]:
    """Fetch a quote per ticker. Returns (quotes, failed tickers).

    A rate-limit response or empty quote is a failure, never a $0 price.
    Failed tickers are retried once after 60s (capped at 3 tickers).
    """
    quotes: list[ReitQuote] = []
    failed: list[str] = []
    for i, ticker in enumerate(tickers):
        if i > 0:
            sleep(CALL_SPACING_SECONDS)
        try:
            quotes.append(_fetch_one(ticker, api_key, client))
        except Exception:
            failed.append(ticker)

    # Retry (at most MAX_RETRIES tickers) once after the per-minute window resets.
    recovered: set[str] = set()
    for ticker in failed[:MAX_RETRIES]:
        sleep(RETRY_WAIT_SECONDS)
        try:
            quotes.append(_fetch_one(ticker, api_key, client))
            recovered.add(ticker)
        except Exception:
            pass
    return quotes, [t for t in failed if t not in recovered]


def fetch_etf_yield(etf: str, api_key: str, client: httpx.Client) -> float | None:
    """Return the ETF's dividend yield as a fraction (0.041 = 4.1%), or None."""
    try:
        resp = client.get(
            API_URL,
            params={"function": "ETF_PROFILE", "symbol": etf, "apikey": api_key},
        )
        resp.raise_for_status()
        data = resp.json()
        if _is_limit_or_error(data):
            return None
        return float(data["dividend_yield"])
    except Exception:
        return None


def accepted_days(run_date: date, now: datetime | None = None) -> list[date]:
    """Trading days a quote may be dated, latest first: the run date's previous trading
    day, plus the run date itself when it is a trading day and the run started after
    4:30 PM ET (the close is in)."""
    days = [previous_trading_day(run_date)]
    if now is not None and is_trading_day(run_date):
        close = datetime.combine(run_date, MARKET_CLOSE, tzinfo=ET)
        if now.astimezone(ET) >= close:
            days.insert(0, run_date)
    return days


def split_stale(quotes: list[ReitQuote], days: list[date]
                ) -> tuple[list[ReitQuote], list[ReitQuote], date | None]:
    """(fresh, stale, trading day). Fresh quotes all share ONE accepted trading day (the
    one most quotes have, ties to the later day), so the scoreboard never mixes closes;
    every other quote is stale. Alpha Vantage keeps returning an old "latest trading day"
    for some tickers (AVB stuck on Aug 17 with a 0.0% move): that is a data problem, not
    a rate limit, so callers report it and never retry."""
    counts = Counter(q.date for q in quotes if q.date in days)
    if not counts:
        return [], list(quotes), None
    day = max(counts, key=lambda d: (counts[d], d))
    return ([q for q in quotes if q.date == day], [q for q in quotes if q.date != day], day)


AV_CONFIRM_ROTATION = 3  # REITs Alpha Vantage double-checks each day, besides the ETF


def confirm_set(etf: str | None, tickers: list[str], run_date: date,
                n: int = AV_CONFIRM_ROTATION) -> list[str]:
    """Tickers Alpha Vantage confirms when another price source is up: the ETF (VNQ) plus
    `n` REITs rotated by date, so every REIT gets a second look every few days while the
    Alpha Vantage quota stays near 5 calls a day (4 quotes + the VNQ yield)."""
    out = [etf] if etf else []
    if tickers:
        start = run_date.toordinal() % len(tickers)
        out += [tickers[(start + i) % len(tickers)] for i in range(min(n, len(tickers)))]
    return out
