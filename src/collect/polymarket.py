"""Polymarket Fed-decision odds collector."""

import json
from datetime import date, datetime, time, timedelta, timezone

import httpx

from src.config import ET
from src.models import FedOdds

SEARCH_URL = "https://gamma-api.polymarket.com/public-search"
TITLE_PREFIX = "fed decision in"
# A meeting's market ends on the decision day (the Oct 28 market ends 2026-10-29T03:59Z,
# 11:59 PM ET Oct 28). Allow up to noon ET the next day for a late-set end time.
NEXT_DAY_CUTOFF = time(12, 0)


def _end_et(raw: str) -> datetime:
    """Polymarket endDate ("2026-10-29T03:59:00Z") as an ET datetime."""
    end = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    return end.astimezone(ET)


def matches_meeting(end_raw: str, meeting: date) -> bool:
    """True when the market ends (in ET) on the meeting day, or the next day before noon."""
    end = _end_et(end_raw)
    if end.date() == meeting:
        return True
    return end.date() == meeting + timedelta(days=1) and end.time() < NEXT_DAY_CUTOFF


def _closed(event: dict) -> bool:
    """Closed, archived or inactive event, or one whose markets have all closed/resolved."""
    if event.get("closed") or event.get("archived") or event.get("active") is False:
        return True
    markets = event.get("markets") or []
    return bool(markets) and all(
        m.get("closed") or str(m.get("umaResolutionStatus") or "").lower() == "resolved"
        for m in markets)


def fetch_fed_odds(client: httpx.Client, meeting: date | None) -> FedOdds | None:
    """Odds for the Fed meeting on `meeting` (the FOMC decision day from config/fomc.yaml).

    Only an open "Fed Decision in ..." event whose end time falls on that meeting day
    (or the next morning, ET) counts. Anything else, or any failure, returns None, so a
    resolved previous meeting's odds are never shown against the next meeting."""
    if meeting is None:
        return None
    try:
        resp = client.get(
            SEARCH_URL,
            params={"q": "fed decision", "events_status": "active"},
        )
        resp.raise_for_status()
        events = resp.json()["events"]

        for event in events:
            # Polymarket capitalises titles ("Fed Decision in October?"), so match loosely.
            if not event["title"].lower().startswith(TITLE_PREFIX):
                continue
            if _closed(event) or not matches_meeting(event["endDate"], meeting):
                continue
            outcomes = [
                (
                    market.get("groupItemTitle") or market["question"],
                    float(json.loads(market["outcomePrices"])[0]),
                )
                for market in event["markets"]
            ]
            outcomes.sort(key=lambda o: o[1], reverse=True)
            return FedOdds(meeting=event["title"], end_date=_end_et(event["endDate"]).date(),
                           outcomes=outcomes)
        return None
    except Exception:
        return None
