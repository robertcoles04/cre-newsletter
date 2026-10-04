from datetime import date
from pathlib import Path

import httpx
import respx

from src import rates
from src.collect import fred
from src.models import RatePoint
from src.store import connect, get_rates

FIX = Path("tests/fixtures")
FRED_URL = "https://api.stlouisfed.org/fred/series/observations"
TREASURY_RE = r"https://home\.treasury\.gov/.*daily-treasury-rates\.csv/\d{4}/all.*"


@respx.mock
def test_fred_skips_dot_values():
    respx.get(FRED_URL).mock(return_value=httpx.Response(200, text=(FIX / "fred_dgs10.json").read_text()))
    with httpx.Client() as client:
        pts = fred.fetch_series("DGS10", "k", date(2026, 9, 1), client)
    assert [p.date for p in pts] == [date(2026, 9, 28), date(2026, 9, 30), date(2026, 10, 1), date(2026, 10, 2)]
    assert pts[-1] == RatePoint("DGS10", date(2026, 10, 2), 5.28)


def test_change_vs_prior_observation_not_calendar_day():
    pts = [
        RatePoint("DGS10", date(2026, 10, 1), 4.50),  # Thu
        RatePoint("DGS10", date(2026, 10, 2), 4.53),  # Fri, no weekend rows
    ]
    last, change = rates.latest_with_change(pts)
    assert change == 3
    assert last.date == date(2026, 10, 2)
    assert rates.latest_with_change(pts[:1])[1] is None


def test_fmt_bps():
    assert rates.fmt_bps(3) == "+3 bps"
    assert rates.fmt_bps(-12) == "-12 bps"
    assert rates.fmt_bps(0) == "unch"
    assert rates.fmt_bps(None) == "n/a"


@respx.mock
def test_fallback_to_treasury_when_fred_500(tmp_path):
    respx.get(FRED_URL).mock(return_value=httpx.Response(500))
    respx.get(url__regex=TREASURY_RE).mock(
        return_value=httpx.Response(200, text=(FIX / "treasury.csv").read_text()))
    conn = connect(str(tmp_path / "t.db"))
    with httpx.Client() as client:
        results = rates.collect_rates(conn, ["DGS10", "DGS5"], "k", client, date(2026, 10, 4))
    assert [r.name for r in results] == ["DGS10", "DGS5"]
    assert all(r.ok and "fallback" in r.error for r in results)
    saved = get_rates(conn, "DGS10", date(2026, 9, 1))
    assert saved and saved[-1] == RatePoint("DGS10", date(2026, 10, 2), 5.28)
    assert rates.latest_with_change(saved)[1] == 4  # 5.28 vs 5.24


@respx.mock
def test_total_failure_reports_not_ok(tmp_path):
    respx.get(FRED_URL).mock(return_value=httpx.Response(500))
    respx.get(url__regex=TREASURY_RE).mock(return_value=httpx.Response(503))
    conn = connect(str(tmp_path / "t.db"))
    with httpx.Client() as client:
        results = rates.collect_rates(conn, ["DGS10", "MORTGAGE30US"], "k", client, date(2026, 10, 4))
    assert [(r.name, r.ok) for r in results] == [("DGS10", False), ("MORTGAGE30US", False)]
    assert all(r.error for r in results)
    assert get_rates(conn, "DGS10", date(2026, 1, 1)) == []
