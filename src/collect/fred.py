"""FRED (St. Louis Fed) series observations."""

from datetime import date

import httpx

from src.models import RatePoint

FRED_URL = "https://api.stlouisfed.org/fred/series/observations"


def fetch_series(series_id: str, api_key: str, start: date, client: httpx.Client) -> list[RatePoint]:
    """Fetch observations from `start` on. Skips the "." (no data) values.

    Raises httpx.HTTPError on HTTP failure so the caller can fall back.
    """
    resp = client.get(FRED_URL, params={
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "observation_start": start.isoformat(),
    })
    resp.raise_for_status()
    points = []
    for obs in resp.json().get("observations", []):
        if obs.get("value") in (None, "", "."):
            continue
        points.append(RatePoint(series_id, date.fromisoformat(obs["date"]), float(obs["value"])))
    points.sort(key=lambda p: p.date)
    return points
