"""Tests for the Polymarket Fed odds collector."""

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


@respx.mock
def test_picks_nearest_upcoming_meeting(client):
    _mock_fixture()
    odds = polymarket.fetch_fed_odds(client, date(2026, 10, 4))
    assert odds.meeting == "Fed Decision in October?"
    assert odds.end_date == date(2026, 10, 29)
    # Once October has passed, the December meeting is next.
    odds = polymarket.fetch_fed_odds(client, date(2026, 11, 1))
    assert odds.meeting == "Fed Decision in December?"
    assert odds.end_date == date(2026, 12, 10)
    # Nothing upcoming -> None.
    assert polymarket.fetch_fed_odds(client, date(2027, 1, 1)) is None


@respx.mock
def test_outcomes_sorted_desc(client):
    _mock_fixture()
    odds = polymarket.fetch_fed_odds(client, date(2026, 10, 4))
    probs = [p for _, p in odds.outcomes]
    assert len(probs) == 3
    assert probs == sorted(probs, reverse=True)
    assert odds.outcomes[0][0] == "No change"
    assert isinstance(probs[0], float)


@respx.mock
def test_bad_json_returns_none(client):
    respx.get(URL).mock(return_value=httpx.Response(200, text="<html>oops</html>"))
    assert polymarket.fetch_fed_odds(client, date(2026, 10, 4)) is None
