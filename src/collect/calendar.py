"""Week Ahead calendar: scheduled events for the 7 days after the run date.

Sources:
- The Federal Reserve's public calendar JSON (keyless). Kept: FOMC meetings and
  minutes, the Beige Book, and speeches/testimony by Board members (Chair, Vice Chair,
  Governors). Routine statistical releases (H.8, G.19...) are skipped.
- FRED's release calendar (`fred/releases/dates`, free with the existing FRED key), kept
  to a short allowlist of major releases (CPI, jobs, GDP...). FRED gives dates, not
  times, so those rows have no time.

Checked 2026-10-05 and not used (see CLAUDE.md Decisions):
- BLS release calendar (bls.ics) answers 403 "Access Denied" to a descriptive
  User-Agent; we do not get around bot protection.
- Census economic-indicator RSS lists only past releases, no upcoming dates.
"""

import html
import json
import re
from datetime import date, timedelta

FED_URL = "https://www.federalreserve.gov/json/calendar.json"
FED_PAGE = "https://www.federalreserve.gov/newsevents/calendar.htm"
GOV_UA = "CRE Blurb newsletter (github.com/robertcoles04/cre-newsletter)"
WINDOW_DAYS = 7
NO_EVENTS = "No major scheduled releases this week."
FOMC_KEEP = ("FOMC Meeting", "FOMC Minutes")
BOARD_TITLE = re.compile(r"\b(?:Chair|Governor)\b")
TIME = re.compile(r"^(\d{1,2}):(\d{2})\s*([ap])\.?m\.?$", re.I)

FRED_RELEASES_URL = "https://api.stlouisfed.org/fred/releases/dates"
FRED_RELEASE_PAGE = "https://fred.stlouisfed.org/release?rid={rid}"
# Major releases worth a Week Ahead line: FRED release name (lowercase) -> display title.
MAJOR_RELEASES = {
    "consumer price index": "Consumer Price Index (CPI)",
    "employment situation": "Jobs report (Employment Situation)",
    "gross domestic product": "GDP",
    "personal income and outlays": "PCE inflation (Personal Income and Outlays)",
    "advance monthly sales for retail and food services": "Retail sales",
    "retail sales": "Retail sales",
    "producer price index": "Producer Price Index (PPI)",
    "job openings and labor turnover survey": "Job openings (JOLTS)",
    "new residential construction": "Housing starts (New Residential Construction)",
    "construction spending": "Construction spending",
    "new residential sales": "New home sales",
    "existing home sales": "Existing home sales",
    "employment cost index": "Employment Cost Index",
}


def _time(raw: str | None) -> str | None:
    m = TIME.match((raw or "").strip())
    if not m:
        return None
    return f"{int(m.group(1))}:{m.group(2)} {m.group(3).upper()}M ET"


def _title(raw: str) -> str:
    """'Speech - Governor Christopher J. Waller' -> 'Speech: Governor Christopher J. Waller'."""
    text = " ".join(html.unescape(raw).split())
    return re.sub(r"\s+-\s+", ": ", text, count=1)


def _keep(ev: dict) -> bool:
    kind, title = ev.get("type"), ev.get("title") or ""
    if kind == "FOMC":
        return title.strip() in FOMC_KEEP
    if kind == "Beige":
        return True
    if kind in ("Speeches", "Testimony"):
        return bool(BOARD_TITLE.search(title))
    return False


def _days(ev: dict) -> list[date]:
    try:
        year, month = (int(x) for x in str(ev.get("month", "")).split("-"))
        return [date(year, month, int(d)) for d in str(ev.get("days", "")).split(",")
                if d.strip().isdigit()]
    except (ValueError, TypeError):
        return []


def parse_fed(text: str) -> list[dict]:
    """Every kept event in the Fed calendar JSON (any date)."""
    data = json.loads(text.lstrip("﻿"))
    events = []
    for ev in data.get("events", []):
        if not isinstance(ev, dict) or not _keep(ev):
            continue
        url = ev.get("link") or ""
        if not url.startswith(("http://", "https://")):
            url = FED_PAGE
        for d in _days(ev):
            events.append({"date": d.isoformat(), "time": _time(ev.get("time")),
                           "title": _title(ev["title"]), "source": "Federal Reserve",
                           "url": url})
    return events


def _sort_key(ev: dict):
    t = ev.get("time")
    minutes = 24 * 60
    if t:
        m = re.match(r"(\d+):(\d+) ([AP])M", t)
        h = int(m.group(1)) % 12 + (12 if m.group(3) == "P" else 0)
        minutes = h * 60 + int(m.group(2))
    return (ev["date"], minutes, ev["title"])


def week_window(events: list[dict], run_date: date) -> list[dict]:
    """Events dated run_date+1 .. run_date+7, sorted by date then time, de-duplicated."""
    start, end = run_date + timedelta(days=1), run_date + timedelta(days=WINDOW_DAYS)
    seen, out = set(), []
    for ev in sorted(events, key=_sort_key):
        key = (ev["date"], ev["title"])
        if start.isoformat() <= ev["date"] <= end.isoformat() and key not in seen:
            seen.add(key)
            out.append(ev)
    return out


def parse_fred_releases(data: dict) -> list[dict]:
    """Major releases from a FRED releases/dates JSON reply, one event per release and day."""
    events, seen = [], set()
    for row in (data or {}).get("release_dates", []):
        if not isinstance(row, dict):
            continue
        title = MAJOR_RELEASES.get(" ".join(str(row.get("release_name", "")).split()).lower())
        day = str(row.get("date", ""))
        try:
            date.fromisoformat(day)
        except ValueError:
            continue
        if title is None or (day, title) in seen:
            continue
        seen.add((day, title))
        rid = row.get("release_id")
        url = (FRED_RELEASE_PAGE.format(rid=rid) if isinstance(rid, int)
               else "https://fred.stlouisfed.org/releases/calendar")
        events.append({"date": day, "time": None, "title": title, "source": "FRED",
                       "url": url})
    return events


def fetch_fred_releases(client, api_key: str, run_date: date) -> list[dict]:
    """Major data releases scheduled in the 7 days after run_date. Raises on HTTP errors."""
    resp = client.get(FRED_RELEASES_URL, params={
        "api_key": api_key, "file_type": "json",
        "realtime_start": (run_date + timedelta(days=1)).isoformat(),
        "realtime_end": (run_date + timedelta(days=WINDOW_DAYS)).isoformat(),
        "include_release_dates_with_no_data": "true",
        "sort_order": "asc", "limit": 1000,
    })
    resp.raise_for_status()
    return week_window(parse_fred_releases(resp.json()), run_date)


def fetch_week(client, run_date: date) -> list[dict]:
    """Fetch the Fed calendar and return next week's events. Raises on HTTP errors."""
    resp = client.get(FED_URL, headers={"User-Agent": GOV_UA})
    resp.raise_for_status()
    return week_window(parse_fed(resp.text), run_date)


def _md_text(s: str) -> str:
    # Feed text is untrusted: escape HTML and markdown link/emphasis characters.
    s = html.escape(s, quote=False)
    return re.sub(r"([\[\]*_`])", r"\\\1", s)


def week_ahead_markdown(events: list[dict]) -> str:
    """Code-built Week Ahead list, e.g.
    '- **Wed Oct 14, 2:00 PM ET:** Beige Book, [Federal Reserve](url)'."""
    if not events:
        return NO_EVENTS
    lines = []
    for ev in events:
        d = date.fromisoformat(ev["date"])
        when = f"{d:%a} {d:%b} {d.day}" + (f", {ev['time']}" if ev.get("time") else "")
        line = f"- **{when}:** {_md_text(ev['title'])}"
        url = str(ev.get("url") or "")
        if url.lower().startswith(("http://", "https://")):
            line += f", [{_md_text(ev['source'])}]({url})"
        lines.append(line)
    return "\n".join(lines)
