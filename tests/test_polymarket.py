"""Tests for the Polymarket Fed odds collector."""

import json
from datetime import date
from pathlib import Path

import httpx
import pytest
import respx

from src.collect import polymarket

FIXTURES = Path(__file__).parent / "fixtures"
URL = "https://gamma-api.polymarket.com/public-search"


@pytest.fixture
def client():
    with httpx.Client() as c:
        yield c


def _mock_fixture():
    body = (FIXTURES / "pm_search.json").read_text(encoding="utf-8")
    respx.get(URL).mock(return_value=httpx.Response(200, text=body))


def _mock_events(events):
    respx.get(URL).mock(return_value=httpx.Response(200, json={"events": events}))


def _events():
    return json.loads((FIXTURES / "pm_search.json").read_text(encoding="utf-8"))["events"]


def _october():
    return next(e for e in _events() if "October" in e["title"])


@respx.mock
def test_picks_the_market_for_the_given_meeting(client):
    _mock_fixture()
    # Oct 28 meeting: the October market ends 2026-10-29T03:59Z = 11:59 PM ET Oct 28.
    odds = polymarket.fetch_fed_odds(client, date(2026, 10, 28))
    assert odds.meeting == "Fed Decision in October?"
    assert odds.end_date == date(2026, 10, 28)
    # Dec 9 meeting: the December market (ends 2026-12-10T04:59Z = 11:59 PM ET Dec 9).
    odds = polymarket.fetch_fed_odds(client, date(2026, 12, 9))
    assert odds.meeting == "Fed Decision in December?"
    assert odds.end_date == date(2026, 12, 9)


@respx.mock
def test_stale_previous_meeting_is_never_used(client):
    # Oct 29: fomc.yaml has rolled to Dec 9, but the search still lists October's market
    # (its endDate's UTC date is Oct 29, so the old date-only check picked it).
    _mock_events([_october()])
    assert polymarket.fetch_fed_odds(client, date(2026, 12, 9)) is None


@respx.mock
def test_no_market_for_the_meeting_returns_none(client):
    _mock_fixture()
    assert polymarket.fetch_fed_odds(client, date(2027, 1, 27)) is None
    assert polymarket.fetch_fed_odds(client, None) is None
    _mock_events([])
    assert polymarket.fetch_fed_odds(client, date(2026, 10, 28)) is None


@respx.mock
def test_next_morning_end_accepted_only_before_noon_et(client):
    ev = _october()
    ev["endDate"] = "2026-10-29T15:00:00Z"  # 11 AM ET the day after the meeting
    _mock_events([ev])
    assert polymarket.fetch_fed_odds(client, date(2026, 10, 28)) is not None
    ev["endDate"] = "2026-10-29T17:00:00Z"  # 1 PM ET: too late
    _mock_events([ev])
    assert polymarket.fetch_fed_odds(client, date(2026, 10, 28)) is None
    ev["endDate"] = "2026-10-27T20:00:00Z"  # the day before the meeting
    _mock_events([ev])
    assert polymarket.fetch_fed_odds(client, date(2026, 10, 28)) is None


@respx.mock
def test_closed_or_resolved_events_are_skipped(client):
    closed = {**_october(), "closed": True}
    _mock_events([closed])
    assert polymarket.fetch_fed_odds(client, date(2026, 10, 28)) is None
    resolved = _october()
    resolved["markets"] = [{**m, "umaResolutionStatus": "resolved"}
                           for m in resolved["markets"]]
    _mock_events([resolved])
    assert polymarket.fetch_fed_odds(client, date(2026, 10, 28)) is None


@respx.mock
def test_outcomes_sorted_desc(client):
    _mock_fixture()
    odds = polymarket.fetch_fed_odds(client, date(2026, 10, 28))
    probs = [p for _, p in odds.outcomes]
    assert len(probs) == 3
    assert probs == sorted(probs, reverse=True)
    assert odds.outcomes[0][0] == "No change"
    assert isinstance(probs[0], float)


@respx.mock
def test_bad_json_returns_none(client):
    respx.get(URL).mock(return_value=httpx.Response(200, text="<html>oops</html>"))
    assert polymarket.fetch_fed_odds(client, date(2026, 10, 28)) is None
