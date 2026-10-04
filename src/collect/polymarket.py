"""Polymarket Fed-decision odds collector."""

import json
from datetime import date

import httpx

from src.models import FedOdds

SEARCH_URL = "https://gamma-api.polymarket.com/public-search"
TITLE_PREFIX = "fed decision in"


def fetch_fed_odds(client: httpx.Client, today: date) -> FedOdds | None:
    """Return odds for the nearest upcoming Fed meeting, or None on any failure."""
    try:
        resp = client.get(
            SEARCH_URL,
            params={"q": "fed decision", "events_status": "active"},
        )
        resp.raise_for_status()
        events = resp.json()["events"]

        upcoming = []
        for event in events:
            # Polymarket capitalises titles ("Fed Decision in October?"), so match loosely.
            if not event["title"].lower().startswith(TITLE_PREFIX):
                continue
            end = date.fromisoformat(event["endDate"][:10])
            if end >= today:
                upcoming.append((end, event))
        if not upcoming:
            return None

        end, event = min(upcoming, key=lambda pair: pair[0])
        outcomes = [
            (
                market.get("groupItemTitle") or market["question"],
                float(json.loads(market["outcomePrices"])[0]),
            )
            for market in event["markets"]
        ]
        outcomes.sort(key=lambda o: o[1], reverse=True)
        return FedOdds(meeting=event["title"], end_date=end, outcomes=outcomes)
    except Exception:
        return None
