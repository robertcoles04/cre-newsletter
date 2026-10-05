"""CMBS delinquency from Trepp's monthly headline, read from the stored news items.

Trepp's feed (config/sources.yaml) posts a monthly report titled like
"CMBS Delinquency Rate Rose 17 Basis Points in September 2026". Code pulls the
direction, the bps move and the month from that title (and the overall rate from the
summary when it says "... to 8.02%"), so these numbers are sourced, never model-written.
"""

import re
import sqlite3
from datetime import date, datetime, time, timedelta, timezone

from src.config import ET

MAX_AGE_DAYS = 45
TITLE = re.compile(r"CMBS Delinquency Rate", re.I)
UP = re.compile(r"\b(?:rose|rises|climb(?:s|ed)|jump(?:s|ed)|increas(?:es|ed)|"
                r"spik(?:es|ed)|ris(?:es|en)|ticks? up|ticked up|moves? up|moved up|"
                r"edg(?:es|ed) up|surg(?:es|ed)|up)\b", re.I)
DOWN = re.compile(r"\b(?:fell|falls|declin(?:es|ed)|drop(?:s|ped)|dip(?:s|ped)|"
                  r"decreas(?:es|ed)|slid(?:es)?|ticks? down|ticked down|"
                  r"moves? down|moved down|edg(?:es|ed) down|retreat(?:s|ed)|"
                  r"improv(?:es|ed)|down)\b", re.I)
FLAT = re.compile(r"\b(?:unchanged|flat|holds? steady|held steady|steady)\b", re.I)
BPS = re.compile(r"(\d+)\s*(?:bps?\b|basis[- ]points?\b)", re.I)
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December")
# AP-style short months, matching the brief's "Sept 2026".
SHORT = {"January": "Jan", "February": "Feb", "August": "Aug", "September": "Sept",
         "October": "Oct", "November": "Nov", "December": "Dec"}
MONTH = re.compile(r"\b(" + "|".join(m[:3] for m in MONTHS) + r")[a-z]*\.?\s+(\d{4})\b", re.I)
RATE = re.compile(r"\brate\b[^.%]*?\bto\s+(\d{1,2}\.\d{1,2})%", re.I)


def _month(text: str) -> str | None:
    m = MONTH.search(text)
    if not m:
        return None
    full = next(n for n in MONTHS if n[:3].lower() == m.group(1)[:3].lower())
    return f"{SHORT.get(full, full)} {m.group(2)}"


def parse_title(title: str) -> dict | None:
    """{"chg": "+17 bps", "month": "Sept 2026"} or None if the title is not usable.

    Unchanged/flat -> "0 bps". A move with no bps figure is not usable (no number to show).
    """
    if not TITLE.search(title):
        return None
    month = _month(title)
    if FLAT.search(title):
        return {"chg": "0 bps", "month": month}
    bps = BPS.search(title)
    if not bps:
        return None
    n = int(bps.group(1))
    after = title[TITLE.search(title).end():]  # direction word follows the subject
    up, down = UP.search(after), DOWN.search(after)
    if not up and not down:
        return None
    if down and (not up or down.start() < up.start()):  # first direction word wins
        n = -n
    return {"chg": "0 bps" if n == 0 else f"{n:+d} bps", "month": month}


def parse_rate(summary: str) -> str | None:
    """Overall rate from text like "rose 17 basis points to 8.02%" -> "8.02%"."""
    m = RATE.search(summary or "")
    return f"{m.group(1)}%" if m else None


def cmbs_values(conn: sqlite3.Connection, run_date: date) -> dict | None:
    """CMBS_DQ_CHG / CMBS_DQ_MONTH / CMBS_DQ_URL (+ CMBS_DQ when stated) from the newest
    matching stored item within 45 days of the run date, or None if there is none."""
    end = datetime.combine(run_date, time(23, 59), tzinfo=ET).astimezone(timezone.utc)
    start = end - timedelta(days=MAX_AGE_DAYS)
    rows = conn.execute(
        "SELECT title, url, summary, published_at FROM items"
        " WHERE title LIKE '%delinquency rate%' AND published_at >= ? AND published_at <= ?"
        " ORDER BY published_at DESC",
        (start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds"))).fetchall()
    for row in rows:
        parsed = parse_title(row["title"])
        if parsed is None:
            continue
        values = {"CMBS_DQ_CHG": parsed["chg"],
                  "CMBS_DQ_MONTH": parsed["month"] or "n/a",
                  "CMBS_DQ_URL": row["url"]}
        rate = parse_rate(row["summary"])
        if rate:
            values["CMBS_DQ"] = rate
        return values
    return None
