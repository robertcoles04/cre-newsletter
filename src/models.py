"""Data models for the CRE Blurb pipeline."""

from dataclasses import dataclass
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
    region: str = ""  # Market Watch tag from the query that found it (sun_belt...)


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
    as_of: date | None = None  # ET date the odds were read (the run time), for the chart caption


@dataclass
class ReitQuote:
    """A REIT stock quote."""
    ticker: str
    date: date
    close: float
    change_pct: float
    source: str = ""  # which price sources agreed ("AV+Stooq", "Tiingo"); "" when unknown


@dataclass
class SourceResult:
    """Result of attempting to collect from a source."""
    name: str
    ok: bool
    error: str = ""
    sources: str = ""  # provenance of the value shown ("FRED+Treasury", "Treasury", "FRED")
    match: bool | None = None  # cross-check result; None when not cross-checked
    note: str = ""  # cross-check disagreement, for the draft banner
