"""Extra REIT/ETF price sources (Stooq, Tiingo) and the cross-check between sources.

Alpha Vantage (src/collect/reits.py) has a ~25 calls/day free quota, so it only confirms a
few tickers a day. The other sources give daily closes ("bars"); the quote for a trading
day is that day's close plus its % change vs the bar before it.

- Stooq: free, no key, daily OHLC CSV. As of 2026-10-06 it answers with a JavaScript
  proof-of-work bot check instead of the CSV, so it is OFF (`reit_stooq: false` in
  config/sources.yaml). We never work around a bot check: a challenge page raises Blocked
  and the run skips Stooq.
- Tiingo: free tier with a key (TIINGO_API_KEY), end-of-day prices. Used only when the key
  is set.

Cross-check: closes within AGREE_PCT of each other agree. Two that disagree with no third
source to break the tie are dropped (n/a), never published.
"""

import csv
import io
from datetime import date

import httpx

from src.models import ReitQuote

STOOQ_URL = "https://stooq.com/q/d/l/"
TIINGO_URL = "https://api.tiingo.com/tiingo/daily/{ticker}/prices"
AGREE_PCT = 0.5
# Display order in a provenance string ("AV+Stooq") and which source's numbers get
# published when several agree (the free primary first, Alpha Vantage last).
ORDER = ("AV", "Stooq", "Tiingo")
PREFER = ("Stooq", "Tiingo", "AV")

Bars = list[tuple[date, float]]  # (trading day, close), oldest first


class Blocked(Exception):
    """The source answered with a bot check / HTML page instead of data."""


def parse_stooq(text: str) -> Bars:
    """Stooq CSV (Date,Open,High,Low,Close,Volume) -> bars. An HTML answer (the bot
    check) raises Blocked; "No data" or a missing Close column raises ValueError."""
    if text.lstrip().startswith("<"):
        raise Blocked("stooq answered with a bot check")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or "Date" not in reader.fieldnames or "Close" not in reader.fieldnames:
        raise ValueError("stooq: no data")
    bars = []
    for row in reader:
        try:
            bars.append((date.fromisoformat(row["Date"].strip()), float(row["Close"])))
        except (ValueError, TypeError, AttributeError):
            continue
    return sorted(bars)


def fetch_stooq(ticker: str, client: httpx.Client) -> Bars:
    resp = client.get(STOOQ_URL, params={"s": f"{ticker.lower()}.us", "i": "d"})
    resp.raise_for_status()
    return parse_stooq(resp.text)


def parse_tiingo(data) -> Bars:
    """Tiingo EOD JSON ([{"date": "2026-10-05T00:00:00.000Z", "close": 89.1, ...}]) ->
    bars. Uses the unadjusted close, like Alpha Vantage's GLOBAL_QUOTE price."""
    if not isinstance(data, list):
        raise ValueError("tiingo: unexpected response")
    bars = []
    for row in data:
        try:
            bars.append((date.fromisoformat(str(row["date"])[:10]), float(row["close"])))
        except (KeyError, ValueError, TypeError):
            continue
    return sorted(bars)


def fetch_tiingo(ticker: str, key: str, client: httpx.Client, start: date) -> Bars:
    # The key goes in a header, never the URL (URLs end up in logs).
    resp = client.get(TIINGO_URL.format(ticker=ticker.lower()),
                      params={"startDate": start.isoformat()},
                      headers={"Authorization": f"Token {key}"})
    resp.raise_for_status()
    return parse_tiingo(resp.json())


def quote_on(ticker: str, bars: Bars, day: date, source: str) -> ReitQuote | None:
    """The quote for `day`: its close and % change vs the previous bar. None when the
    source has no bar for that day or no earlier bar to compare with."""
    ordered = sorted(bars)
    for i, (d, close) in enumerate(ordered):
        if d == day and i > 0 and ordered[i - 1][1] > 0:
            prev = ordered[i - 1][1]
            return ReitQuote(ticker, day, close, round((close / prev - 1) * 100, 2), source)
    return None


def best_day(bars_by_ticker: dict[str, Bars], days: list[date]) -> date | None:
    """The accepted trading day (see reits.accepted_days, latest first) that most tickers
    have a bar for, ties to the later day; None when no ticker has any accepted day."""
    counts = {d: sum(1 for bars in bars_by_ticker.values() if any(b[0] == d for b in bars))
              for d in days}
    counts = {d: n for d, n in counts.items() if n}
    if not counts:
        return None
    return max(counts, key=lambda d: (counts[d], d))


def agree(a: ReitQuote, b: ReitQuote) -> bool:
    low = min(a.close, b.close)
    return low > 0 and abs(a.close - b.close) / low * 100 <= AGREE_PCT


def cross_check(ticker: str, cands: dict[str, ReitQuote]
                ) -> tuple[ReitQuote | None, str, str | None]:
    """(quote to publish or None, provenance like "AV+Stooq", note or None).

    One source: publish it. Two that agree: publish. Two that disagree: drop with a note.
    Three: the largest group that agrees wins when it is a majority (the third source
    breaks the tie); otherwise drop with a note."""
    names = [n for n in ORDER if n in cands]
    if not names:
        return None, "", None
    if len(names) == 1:
        q = cands[names[0]]
        return q, names[0], None
    groups = [[m for m in names if m == n or agree(cands[n], cands[m])] for n in names]
    best = max(groups, key=len)
    if len(best) >= 2 and len(best) * 2 > len(names):
        pick = next(n for n in PREFER if n in best)
        provenance = "+".join(best)
        q = cands[pick]
        return (ReitQuote(q.ticker, q.date, q.close, q.change_pct, provenance),
                provenance, None)
    detail = ", ".join(f"{n} {cands[n].close:.2f}" for n in names)
    return None, "", f"reits: {ticker} sources disagree ({detail})"
