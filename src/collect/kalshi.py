"""Kalshi Fed-decision odds: the second source next to Polymarket.

Kalshi's public market-data API needs no key or account for reads. Series KXFEDDECISION
has one event per FOMC meeting ("Fed decision in Oct 2026?", strike_date
2026-10-28T18:00:00Z) with five markets: "Cut >25bps", "Cut 25bps", "Fed maintains rate",
"Hike 25bps", "Hike >25bps". A market's price (dollars, 0-1) is the chance of that outcome.
"""

from datetime import date

import httpx

from src.collect.polymarket import _end_et, matches_meeting
from src.models import FedOdds

EVENTS_URL = "https://api.elections.kalshi.com/trade-api/v2/events"
SERIES = "KXFEDDECISION"


def _num(raw) -> float | None:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def market_price(market: dict) -> float | None:
    """Midpoint of the yes bid and ask when both are quoted, else the last trade."""
    bid, ask = _num(market.get("yes_bid_dollars")), _num(market.get("yes_ask_dollars"))
    if bid is not None and ask is not None and ask > 0 and ask >= bid:
        return (bid + ask) / 2
    return _num(market.get("last_price_dollars"))


def parse_events(data: dict, meeting: date) -> FedOdds | None:
    """The odds for the event whose decision day (ET) is `meeting`, or None."""
    for event in data.get("events") or []:
        strike = event.get("strike_date")
        if not strike or not matches_meeting(strike, meeting):
            continue
        outcomes = []
        for m in event.get("markets") or []:
            if str(m.get("status", "active")).lower() not in ("active", "open"):
                continue
            price = market_price(m)
            label = m.get("yes_sub_title") or m.get("subtitle") or m.get("ticker", "")
            if price is not None:
                outcomes.append((label, price))
        if outcomes:
            outcomes.sort(key=lambda o: o[1], reverse=True)
            return FedOdds(meeting=event.get("title") or SERIES,
                           end_date=_end_et(strike).date(), outcomes=outcomes)
    return None


def fetch_fed_odds(client: httpx.Client, meeting: date | None) -> FedOdds | None:
    """Open KXFEDDECISION events -> odds for `meeting`. Raises on a network/HTTP error
    (the caller notes "kalshi: unavailable"); None when no event matches the meeting."""
    if meeting is None:
        return None
    resp = client.get(EVENTS_URL, params={"series_ticker": SERIES, "status": "open",
                                          "with_nested_markets": "true"})
    resp.raise_for_status()
    return parse_events(resp.json(), meeting)
