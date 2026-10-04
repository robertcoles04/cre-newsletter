"""RSS/Atom feed collector."""

import html
import re
import time
from datetime import datetime, timezone

import feedparser

from src.models import Item

SUMMARY_MAX_CHARS = 600
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def clean_summary(raw: str) -> str:
    """Strip HTML tags, unescape entities, collapse whitespace, truncate."""
    text = html.unescape(_TAG_RE.sub(" ", raw or ""))
    return _WS_RE.sub(" ", text).strip()[:SUMMARY_MAX_CHARS]


def _entry_datetime(entry) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    try:
        return datetime(*parsed[:6], tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def parse_items(text: str, name: str, priority: int, since: datetime,
                use_source_tag: bool = False) -> list[Item]:
    """Turn feed XML into Items: skip undated, clamp future dates, drop old ones."""
    feed = feedparser.parse(text)
    now = datetime.now(timezone.utc)
    items = []
    for entry in feed.entries:
        published = _entry_datetime(entry)
        if published is None:
            continue
        published = min(published, now)
        if published < since:
            continue
        url = entry.get("link")
        title = (entry.get("title") or "").strip()
        if not url or not title:
            continue
        source = name
        if use_source_tag:
            source = (entry.get("source") or {}).get("title") or name
        items.append(Item(
            source=source,
            url=url,
            title=title,
            published_at=published,
            summary=clean_summary(entry.get("summary", "")),
            priority=priority,
        ))
    return items


def fetch_feed(name: str, url: str, priority: int, since: datetime, client) -> list[Item]:
    """Fetch one feed. Raises on HTTP errors; a malformed body returns []."""
    response = client.get(url)
    response.raise_for_status()
    return parse_items(response.text, name, priority, since)
