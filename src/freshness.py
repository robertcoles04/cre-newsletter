"""Data freshness: how old is each series' latest observation, and is that too old?

Rates come from FRED / Treasury.gov and are kept in SQLite between runs. If a source
silently stops updating, the fact sheet would keep showing the last stored value with
its old as-of date. `factsheet["as_of_dates"]` ({key: "YYYY-MM-DD"}) records each
series' latest observation date, and this module judges it.

Simplifications: business days are Mon-Fri only (US federal holidays are NOT excluded,
so a long weekend can look one day older than it is; the thresholds leave room for
that). Age is counted from the run date's PREVIOUS business day, so a Monday run with
Friday data is fresh (age 0).
"""
from datetime import date, timedelta

# key -> (display name, cadence)
SERIES = {
    "DGS10": ("10-Year", "daily"),
    "DGS5": ("5-Year", "daily"),
    "DGS2": ("2-Year", "daily"),
    "DGS30": ("30-Year", "daily"),
    "SOFR": ("SOFR", "daily"),
    "DFF": ("Fed Funds", "daily"),
    "VNQ": ("VNQ quote", "daily"),
    "REIT": ("REIT quotes", "daily"),
    "HY_OAS": ("High-yield spread", "daily"),
    "MORTGAGE30US": ("30-Year Mortgage", "weekly"),
    "BANK_CRE_LOANS": ("Bank CRE loans", "weekly"),
    "CMBS_DQ": ("CMBS delinquency", "monthly"),
    "BANK_CRE_DQ": ("Bank CRE delinquency", "quarterly"),
}
# Stale when older than this: business days for daily/weekly, calendar days otherwise.
LIMITS = {"daily": 3, "weekly": 10, "monthly": 45, "quarterly": 200}
CORE = ("DGS10", "SOFR")  # hard stop: the issue must not auto-publish if these are stale
CORE_LIMIT = 5  # business days


def is_business_day(d: date) -> bool:
    return d.weekday() < 5


def previous_business_day(d: date) -> date:
    d -= timedelta(days=1)
    while not is_business_day(d):
        d -= timedelta(days=1)
    return d


# NYSE full-day closures, so the REIT quote check knows the day after a market holiday
# expects the close from before it. Source: nyse.com holiday calendar (2026-2027).
MARKET_HOLIDAYS = frozenset(date.fromisoformat(d) for d in (
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19",
    "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18",
    "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
))


def is_trading_day(d: date) -> bool:
    return is_business_day(d) and d not in MARKET_HOLIDAYS


def previous_trading_day(d: date) -> date:
    """previous_business_day, also skipping NYSE holidays: the last close before d."""
    d = previous_business_day(d)
    while d in MARKET_HOLIDAYS:
        d = previous_business_day(d)
    return d


def business_days_old(data_date: date, run_date: date) -> int:
    """Weekdays after data_date up to the run date's previous business day (0 = fresh)."""
    ref = previous_business_day(run_date)
    n, d = 0, data_date
    while d < ref:
        d += timedelta(days=1)
        if is_business_day(d):
            n += 1
    return n


def _parse(raw) -> date | None:
    try:
        return date.fromisoformat(str(raw))
    except ValueError:
        return None


def stale_series(as_of_dates: dict, run_date: date) -> list[tuple[str, str]]:
    """[(display name, ISO date)] for every present series older than its cadence allows.
    Missing optional series are not listed (their rows already show n/a)."""
    out = []
    for key, (name, cadence) in SERIES.items():
        raw = as_of_dates.get(key)
        if raw is None:
            continue
        d = _parse(raw)
        if d is None:
            out.append((name, str(raw)))
            continue
        if cadence in ("daily", "weekly"):
            old = business_days_old(d, run_date) > LIMITS[cadence]
        else:
            old = (run_date - d).days > LIMITS[cadence]
        if old:
            out.append((name, d.isoformat()))
    return out


def core_stale(as_of_dates: dict, run_date: date) -> list[str]:
    """Hard-stop reasons: 10-Year or SOFR missing, or over CORE_LIMIT business days old.
    Each item reads like "10-Year last updated 2026-09-25" or "SOFR has no data"."""
    out = []
    for key in CORE:
        name = SERIES[key][0]
        raw = as_of_dates.get(key)
        d = _parse(raw) if raw is not None else None
        if d is None:
            out.append(f"{name} has no data")
        elif business_days_old(d, run_date) > CORE_LIMIT:
            out.append(f"{name} last updated {d.isoformat()}")
    return out
