"""Tests for the Alpha Vantage REIT collector."""

import json
from datetime import date
from pathlib import Path

import httpx
import pytest
import respx

from src.collect import reits

FIXTURES = Path(__file__).parent / "fixtures"
URL = "https://www.alphavantage.co/query"


def _load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def client():
    with httpx.Client() as c:
        yield c


@respx.mock
def test_parses_quote(client):
    respx.get(URL).mock(return_value=httpx.Response(200, json=_load("av_quote.json")))
    quotes, failed = reits.fetch_quotes(["VNQ"], "k", client, sleep=lambda s: None)
    assert failed == []
    assert len(quotes) == 1
    q = quotes[0]
    assert q.ticker == "VNQ"
    assert q.date == date(2026, 10, 2)
    assert q.close == 90.87 and isinstance(q.close, float)
    assert q.change_pct == 1.2345 and isinstance(q.change_pct, float)


@respx.mock
def test_rate_limit_note_is_failure_not_zero(client):
    respx.get(URL).mock(return_value=httpx.Response(200, json=_load("av_limit.json")))
    quotes, failed = reits.fetch_quotes(["VNQ", "O"], "k", client, sleep=lambda s: None)
    assert failed == ["VNQ", "O"]
    assert quotes == []
    assert all(q.close != 0 for q in quotes)


@respx.mock
def test_empty_global_quote_is_failure(client):
    respx.get(URL).mock(return_value=httpx.Response(200, json={"Global Quote": {}}))
    quotes, failed = reits.fetch_quotes(["ZZZ"], "k", client, sleep=lambda s: None)
    assert (quotes, failed) == ([], ["ZZZ"])


@respx.mock
def test_sleeps_13s_between_calls_only(client):
    respx.get(URL).mock(return_value=httpx.Response(200, json=_load("av_quote.json")))
    naps = []
    reits.fetch_quotes(["VNQ", "O", "PLD"], "k", client, sleep=naps.append)
    assert naps == [13, 13]


@respx.mock
def test_etf_yield_parses_string_fraction(client):
    respx.get(URL).mock(
        return_value=httpx.Response(200, json={"dividend_yield": "0.0412", "net_assets": "1"})
    )
    assert reits.fetch_etf_yield("VNQ", "k", client) == 0.0412


@respx.mock
def test_etf_yield_limit_returns_none(client):
    respx.get(URL).mock(return_value=httpx.Response(200, json=_load("av_limit.json")))
    assert reits.fetch_etf_yield("VNQ", "k", client) is None


@respx.mock
def test_etf_yield_missing_field_returns_none(client):
    respx.get(URL).mock(return_value=httpx.Response(200, json={"net_assets": "1"}))
    assert reits.fetch_etf_yield("VNQ", "k", client) is None


@respx.mock
def test_failed_ticker_retried_after_60s_and_recovers(client):
    ok = httpx.Response(200, json=_load("av_quote.json"))
    limit = httpx.Response(200, json=_load("av_limit.json"))
    respx.get(URL).mock(side_effect=[ok, limit, ok])
    sleeps = []
    quotes, failed = reits.fetch_quotes(["VNQ", "EQR"], "k", client, sleep=sleeps.append)
    assert failed == []
    assert len(quotes) == 2
    assert 60 in sleeps


@respx.mock
def test_ticker_failing_twice_stays_failed(client):
    ok = httpx.Response(200, json=_load("av_quote.json"))
    limit = httpx.Response(200, json=_load("av_limit.json"))
    respx.get(URL).mock(side_effect=[ok, limit, limit])
    quotes, failed = reits.fetch_quotes(["VNQ", "EQR"], "k", client, sleep=lambda s: None)
    assert failed == ["EQR"]
    assert len(quotes) == 1


@respx.mock
def test_retries_capped_at_three_tickers(client):
    limit = httpx.Response(200, json=_load("av_limit.json"))
    route = respx.get(URL).mock(return_value=limit)
    quotes, failed = reits.fetch_quotes(list("ABCDE"), "k", client, sleep=lambda s: None)
    assert failed == list("ABCDE")
    assert route.call_count == 5 + 3
