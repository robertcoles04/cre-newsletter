"""Data models for the CRE Blurb pipeline."""

from dataclasses import dataclass, field
from datetime import datetime, date


@dataclass
class Item:
    """A news item from a source feed."""
    source: str
    url: str
    title: str
    published_at: datetime
    summary: str = ""
    priority: int = 50


@dataclass
class RatePoint:
    """A single data point from a FRED time series."""
    series: str
    date: date
    value: float


@dataclass
class FedOdds:
    """Federal Reserve meeting outcome probabilities."""
    meeting: str
    end_date: date
    outcomes: list[tuple[str, float]]  # List of (outcome_name, probability) tuples where probabilities are 0-1


@dataclass
class ReitQuote:
    """A REIT stock quote."""
    ticker: str
    date: date
    close: float
    change_pct: float


@dataclass
class SourceResult:
    """Result of attempting to collect from a source."""
    name: str
    ok: bool
    error: str = ""
