"""Alpha Vantage REIT quote and ETF yield collector."""

import time
from datetime import date

import httpx

from src.models import ReitQuote

API_URL = "https://www.alphavantage.co/query"
# Free tier allows 5 calls/minute, so space calls out by 13 seconds.
CALL_SPACING_SECONDS = 13
# A burst failure is a per-minute limit: wait a full minute, then retry once.
RETRY_WAIT_SECONDS = 60
MAX_RETRIES = 3


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
