"""Tests for the RSS and Google News collectors."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import respx

from src.collect import google_news, rss

FIXTURES = Path(__file__).parent / "fixtures"
SINCE = datetime(2026, 10, 1, tzinfo=timezone.utc)
FEED_URL = "https://example.com/feed"


@pytest.fixture
def client():
    with httpx.Client() as c:
        yield c


@respx.mock
def test_fetch_feed_filters_old_and_undated(client):
    body = (FIXTURES / "feed_co.xml").read_text(encoding="utf-8")
    respx.get(FEED_URL).mock(return_value=httpx.Response(200, text=body))
    items = rss.fetch_feed("Fixture", FEED_URL, 80, SINCE, client)
    assert len(items) == 1
    assert items[0].url == "https://example.com/fresh"
    assert items[0].source == "Fixture"
    assert items[0].priority == 80
    assert items[0].published_at.tzinfo is not None
    assert "<" not in items[0].summary
    assert "Buyer & seller" in items[0].summary


@respx.mock
def test_future_date_clamped(client):
    future = datetime.now(timezone.utc) + timedelta(days=3)
    pub = future.strftime("%a, %d %b %Y %H:%M:%S +0000")
    body = (
        '<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>'
        f"<item><title>Future</title><link>https://example.com/f</link>"
        f"<pubDate>{pub}</pubDate></item></channel></rss>"
    )
    respx.get(FEED_URL).mock(return_value=httpx.Response(200, text=body))
    items = rss.fetch_feed("Fixture", FEED_URL, 50, SINCE, client)
    assert len(items) == 1
    assert items[0].published_at <= datetime.now(timezone.utc)


@respx.mock
def test_malformed_feed_returns_empty(client):
    respx.get(FEED_URL).mock(return_value=httpx.Response(200, text="<html>nope"))
    assert rss.fetch_feed("Fixture", FEED_URL, 50, SINCE, client) == []


def test_build_url_encodes_when():
    url = google_news.build_url("site:globest.com")
    assert "when%3A2d" in url
    assert url.startswith("https://news.google.com/rss/search?q=")
    assert url.endswith("&hl=en-US&gl=US&ceid=US:en")


@respx.mock
def test_gnews_source_from_tag(client):
    body = (FIXTURES / "gnews.xml").read_text(encoding="utf-8")
    respx.get(url__startswith="https://news.google.com/rss/search").mock(
        return_value=httpx.Response(200, text=body)
    )
    items = google_news.fetch("GlobeSt Query", "site:globest.com", 75, SINCE, client)
    assert len(items) == 2
    assert items[0].source == "GlobeSt"
