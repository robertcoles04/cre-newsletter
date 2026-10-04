"""Google News RSS collector (used for outlets that block direct scraping)."""

from datetime import datetime
from urllib.parse import quote_plus

from src.collect.rss import parse_items
from src.models import Item


def build_url(query: str, when: str = "2d") -> str:
    """Build a Google News RSS search URL limited to the last `when` period."""
    q = quote_plus(f"{query} when:{when}")
    return f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"


def fetch(name: str, query: str, priority: int, since: datetime, client,
          when: str = "2d") -> list[Item]:
    """Fetch a Google News query. Raises on HTTP errors; malformed body returns []."""
    response = client.get(build_url(query, when))
    response.raise_for_status()
    return parse_items(response.text, name, priority, since, use_source_tag=True)
