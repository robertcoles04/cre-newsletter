"""Midday refresh of the slow-posting stats in the latest published issue.

Two numbers usually post after the morning draft: the 30-year mortgage rate (Freddie Mac,
Thursdays around noon ET) and the ICE BofA high-yield spread (FRED BAMLH0A0HYM2, a day
late). This re-reads both from FRED and updates only their values in issues/<date>.json,
plus the mortgage chart. It never touches the issue's Markdown or published.json.

    python -m src.refresh            # latest published issue
    python -m src.refresh --date 2026-10-15

Prints what changed; exits 0 with no change when FRED has nothing newer or is down.
"""

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import httpx

from src import chart as charts_mod
from src.collect import fred
from src.config import env, http_client
from src.factsheet import _day
from src.models import RatePoint
from src.publish import load_published
from src.rates import fmt_bps, latest_with_change, lookback_days

ROOT = Path(__file__).resolve().parent.parent
MORTGAGE = "MORTGAGE30US"
HY_OAS, HY_SID = "HY_OAS", "BAMLH0A0HYM2"
MORTGAGE_DAYS = 190  # same window as src.main's mortgage chart (26 weekly points)


def latest_issue(root: Path = ROOT) -> str | None:
    days = load_published(root)
    return max(days) if days else None


def _upto(points: list[RatePoint], day: date) -> list[RatePoint]:
    """Observations on or before the issue date, oldest first (never newer than the issue)."""
    return sorted((p for p in points if p.date <= day), key=lambda p: p.date)


def _newer_or_same(data: dict, key: str, d: date) -> bool:
    """False when the issue already shows a later observation than `d` (never go backwards)."""
    old = (data.get("as_of_dates") or {}).get(key)
    return not old or d.isoformat() >= old


def apply_mortgage(data: dict, points: list[RatePoint]) -> bool:
    """Update MORTGAGE30US, _CHG, _ASOF, as_of_dates and the chart alt. True if changed.
    The as-of label is kept only when the date differs from the 10Y's (as the fact sheet
    does)."""
    if not points:
        return False
    last, chg = latest_with_change(points)
    if not _newer_or_same(data, MORTGAGE, last.date):
        return False
    before = json.dumps(data, sort_keys=True)
    values = data.setdefault("values", {})
    values[MORTGAGE] = f"{last.value:.2f}%"
    values[f"{MORTGAGE}_CHG"] = fmt_bps(chg)
    ten = (data.get("as_of_dates") or {}).get("DGS10")
    if ten and last.date.isoformat() != ten:
        values[f"{MORTGAGE}_ASOF"] = _day(last.date)
    else:
        values.pop(f"{MORTGAGE}_ASOF", None)
    data.setdefault("as_of_dates", {})[MORTGAGE] = last.date.isoformat()
    chart = (data.get("charts") or {}).get("mortgage")
    if chart is not None:
        chart["alt"] = charts_mod.mortgage_alt(points)
    return json.dumps(data, sort_keys=True) != before


def apply_hy_oas(data: dict, points: list[RatePoint]) -> bool:
    """Update HY_OAS, _CHG, _DATE, _ASOF (dropped when it equals RATES_ASOF) and
    as_of_dates. True if changed."""
    if not points:
        return False
    last, chg = latest_with_change(points)
    if not _newer_or_same(data, HY_OAS, last.date):
        return False
    before = json.dumps(data, sort_keys=True)
    values = data.setdefault("values", {})
    values[HY_OAS] = f"{last.value:.2f}%"
    values[f"{HY_OAS}_CHG"] = fmt_bps(chg)
    values[f"{HY_OAS}_DATE"] = last.date.isoformat()
    if _day(last.date) == values.get("RATES_ASOF"):
        values.pop(f"{HY_OAS}_ASOF", None)
    else:
        values[f"{HY_OAS}_ASOF"] = _day(last.date)
    data.setdefault("as_of_dates", {})[HY_OAS] = last.date.isoformat()
    return json.dumps(data, sort_keys=True) != before


def redraw_mortgage(data: dict, points: list[RatePoint], img: Path) -> bool:
    """Redraw issues/img/<date>-mortgage.png. Only when the issue already has that chart."""
    if "mortgage" not in (data.get("charts") or {}):
        return False
    path = charts_mod.mortgage_trend(points, img)
    if path is None:
        return False
    w, h = charts_mod.size_px(charts_mod.PAIR_FIGSIZE)
    data["charts"]["mortgage"].update(alt=charts_mod.mortgage_alt(points), width=w, height=h)
    return True


def refresh(day: str, root: Path = ROOT, api_key: str | None = None,
            client: httpx.Client | None = None) -> list[str]:
    """Refresh one issue's slow stats. Returns a list of what changed (empty = nothing)."""
    path = root / "issues" / f"{day}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    issue_day = date.fromisoformat(day)
    own = client is None
    client = client or http_client()
    try:
        mort = _upto(fred.fetch_series(
            MORTGAGE, api_key, issue_day - timedelta(days=MORTGAGE_DAYS), client), issue_day)
        hy = _upto(fred.fetch_series(
            HY_SID, api_key, issue_day - timedelta(days=lookback_days(HY_SID)), client),
            issue_day)
    finally:
        if own:
            client.close()
    changed = []
    if apply_mortgage(data, mort):
        redraw_mortgage(data, mort, root / "issues" / "img" / f"{day}-mortgage.png")
        v = data["values"]
        changed.append(f"mortgage {v[MORTGAGE]} ({mort[-1].date:%b} {mort[-1].date.day})")
    if apply_hy_oas(data, hy):
        v = data["values"]
        changed.append(f"high-yield spread {v[HY_OAS]} ({hy[-1].date:%b} {hy[-1].date.day})")
    if changed:
        path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return changed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--date", help="Issue date (default: latest published issue)")
    args = ap.parse_args(argv)
    key = env("FRED_API_KEY", required=False)
    if not key:
        print("::error::FRED_API_KEY is not set")
        return 1
    day = args.date or latest_issue()
    if not day:
        print("no published issue")
        return 0
    try:
        changed = refresh(day, api_key=key)
    except httpx.HTTPError as e:
        print(f"::warning::FRED unavailable, {day} left as is: {type(e).__name__}")
        return 0
    print(f"{day}: " + ("; ".join(changed) if changed else "no change"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
