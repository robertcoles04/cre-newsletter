"""Alpha Vantage REIT quote and ETF yield collector."""

import time
from datetime import date

import httpx

from src.models import ReitQuote

API_URL = "https://www.alphavantage.co/query"
# Free tier allows 5 calls/minute, so space calls out by 13 seconds.
CALL_SPACING_SECONDS = 13


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
    return quotes, failed


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
