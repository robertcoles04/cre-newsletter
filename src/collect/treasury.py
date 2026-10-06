"""Treasury.gov daily par yield curve CSV (fallback for FRED)."""

import csv
import io
from datetime import datetime

import httpx

from src.models import RatePoint

TREASURY_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
    "daily-treasury-rates.csv/{year}/all?type=daily_treasury_yield_curve"
    "&field_tdr_date_value={year}&page&_format=csv"
)
# 2Y and 30Y too, so the curve legs and the yield-curve chart get same-day Treasury data
# when FRED posts a day late.
COLUMN_TO_SERIES = {"10 Yr": "DGS10", "5 Yr": "DGS5", "2 Yr": "DGS2", "30 Yr": "DGS30"}


def fetch_yields(year: int, client: httpx.Client) -> list[RatePoint]:
    """Fetch one year of daily yields, mapped to FRED series ids. Sorted by date."""
    resp = client.get(TREASURY_URL.format(year=year))
    resp.raise_for_status()
    points = []
    for row in csv.DictReader(io.StringIO(resp.text)):
        try:
            day = datetime.strptime((row.get("Date") or "").strip(), "%m/%d/%Y").date()
        except ValueError:
            continue
        for column, series in COLUMN_TO_SERIES.items():
            raw = (row.get(column) or "").strip()
            if not raw:
                continue
            try:
                points.append(RatePoint(series, day, float(raw)))
            except ValueError:
                continue
    points.sort(key=lambda p: (p.series, p.date))
    return points
