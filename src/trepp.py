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
TITLE = re.compile(r"^\s*CMBS Delinquency Rate\b", re.I)  # no sector prefix (Office...)
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
# Overall rate only: "the overall delinquency rate ... to 7.29%" or Trepp's lead sentence
# "The Trepp CMBS delinquency rate rose 17 basis points to 8.02%". Sector rates (office,
# lodging...) never match.
RATE = re.compile(
    r"(?:\boverall\b[^.%]{0,60}?\brate\b"
    r"|\bThe Trepp (?:commercial mortgage-backed securities \(CMBS\)|CMBS) delinquency rate\b)"
    r"[^.%]{0,100}?\bto\s+(\d{1,2}\.\d{1,2})%", re.I)
DIRECTION = re.compile(UP.pattern + "|" + DOWN.pattern + "|" + FLAT.pattern, re.I)
BPS_AFTER = re.compile(r"\s+(?:by\s+)?(\d+)\s*(?:bps?\b|basis[- ]points?\b)", re.I)


def _month(text: str) -> str | None:
    m = MONTH.search(text)
    if not m:
        return None
    full = next(n for n in MONTHS if n[:3].lower() == m.group(1)[:3].lower())
    return f"{SHORT.get(full, full)} {m.group(2)}"


def parse_title(title: str) -> dict | None:
    """{"chg": "+17 bps", "month": "Sept 2026"} or None if the title is not usable.

    The title must start with "CMBS Delinquency Rate" (no sector prefix). The first
    direction word decides: flat -> "0 bps"; up/down needs a bps figure right after it
    ("Rose 17 bps", "Fell by 9 basis points"), else None, so a later clause such as
    "...; Office Rate Falls 50 bps" is never read as the overall move.
    """
    head = TITLE.match(title)
    if not head:
        return None
    month = _month(title)
    rest = title[head.end():]
    word = DIRECTION.search(rest)
    if not word:
        return None
    if FLAT.fullmatch(word.group(0)):
        return {"chg": "0 bps", "month": month}
    bps = BPS_AFTER.match(rest, word.end())
    if not bps:
        return None
    n = int(bps.group(1)) * (-1 if DOWN.fullmatch(word.group(0)) else 1)
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
        " WHERE source = 'Trepp' AND title LIKE '%delinquency rate%'"
        " AND published_at >= ? AND published_at <= ?"
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
