"""Fact sheet: the structured input Claude writes from.

Every number is pre-formatted into `values` as a string; later code substitutes
them into {{PLACEHOLDERS}}, so the model never writes numbers. Missing data is "n/a".
"""

import json
import re
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

from src.collect.calendar import week_ahead_markdown
from src.config import ET, display_source, load_sources
from src.markets import pick as pick_markets
from src.models import FedOdds, ReitQuote
from src.rates import fmt_bps, latest_with_change, lookback_days
from src.store import drop_used, get_quotes, get_rates, recent_items, used_story_keys

NA = "n/a"
CALENDAR_DOWN = "Calendar unavailable today."
RATE_SERIES = ("DGS10", "DGS5", "SOFR", "DFF")
FED_TARGET = ("DFEDTARL", "DFEDTARU")  # Fed funds target range: lower, upper bound
# Rates rows that get their own as-of (KEY_ASOF, shown in the row hint) when their latest
# observation is not RATES_ASOF's day (SOFR posts a day late; mortgage is weekly).
ROW_ASOF_KEYS = ("DGS5", "DGS2", "T10Y2Y", "SOFR", "DFF", "MORTGAGE30US")
# Data Room rows: KEY_DATE (ISO) lets the page tag rows updated in the last 7 days.
DATED_KEYS = ("HY_OAS", "BANK_CRE_LOANS", "BANK_CRE_DQ")
# Extra FRED series: (value key, FRED id, value format, as-of format or None).
#   pct: "4.83%" with a bps change; spread: "45 bps" with a bps change;
#   billions: "$2,981B" with a % change vs the prior observation.
#   As-of "day" -> KEY_ASOF "Oct 1"; "quarter" -> KEY_ASOF "Q1 2026".
EXTRA_SERIES = (
    ("DGS2", "DGS2", "pct", None),
    ("T10Y2Y", "T10Y2Y", "spread", None),
    ("HY_OAS", "BAMLH0A0HYM2", "pct", "day"),  # as-of kept only if != RATES_ASOF
    ("MORTGAGE30US", "MORTGAGE30US", "pct", "day"),
    ("BANK_CRE_LOANS", "CREACBW027SBOG", "billions", "day"),
    ("BANK_CRE_DQ", "DRCRELEXFACBS", "pct", "quarter"),
)
TOP_COUNT = {"weekday": 5, "friday": 3}
QUICK_HITS_COUNT = 6
DEBT_COUNT = 5
AI_MIN_IMPORTANCE = 7
WEEK_TOP_COUNT = 5
AI_WEEK_COUNT = 3
REIT_WEEK_PICKS = 3
VNQ_TICKER = "VNQ"


def day_type(d: date) -> str:
    return {4: "friday", 5: "saturday", 6: "sunday"}.get(d.weekday(), "weekday")


def _load_yaml(path: str):
    with open(Path(path), encoding="utf8") as f:
        return yaml.safe_load(f)


def _anchor(run_date: date) -> datetime:
    return datetime(run_date.year, run_date.month, run_date.day, 5, 0, tzinfo=ET)


def _story(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"], "title": row["title"],
        "url": row["url"], "summary": row["summary"] or "",
        "source": display_source(row["source"]),
        "also_covered": json.loads(row["also_covered"] or "[]"),
        "section": row["section"],
    }


def _by_rank(rows: list[sqlite3.Row]) -> list[sqlite3.Row]:
    return sorted(rows, key=lambda r: (-(r["importance"] or 0), -(r["priority"] or 0)))


# AI section: tool and use-case stories go ahead of data center / power-grid stories.
AI_USE_CASE = re.compile(
    r"\b(?:tools?|platforms?|AI-powered|chatbots?|assistants?|agents?|software|apps?|"
    r"launch(?:es|ed)?|underwrit\w*|lease abstract\w*|valuations?|apprais\w*|"
    r"property management|leasing|broker\w*|proptech|automat\w*|adopt\w*|startups?|"
    r"raises?)\b", re.I)
AI_INFRA = re.compile(r"\b(?:data cent(?:er|re)s?|power grid|grid|electricity|megawatts?|"
                      r"hyperscale\w*|power)\b", re.I)


def _ai_bucket(row) -> int:
    text = f"{row['title']} {row['summary'] or ''}"
    if AI_INFRA.search(text) and not AI_USE_CASE.search(row["title"]):
        return 1
    return 0


def _ai_rank(rows: list) -> list:
    """Stable: tool/use-case stories first, then infrastructure, each kept in rank order."""
    return sorted(rows, key=_ai_bucket)


# Stock-market recaps ("Real estate stocks slide as ...") go stale fast: a recap from
# yesterday can contradict today's REIT numbers, so recaps older than RECAP_MAX_HOURS
# are dropped from the day's stories.
RECAP_MAX_HOURS = 24
RECAP_SUBJECT = re.compile(
    r"\b(?:REITs?|REIT (?:stocks|shares|index)|real estate (?:stocks|shares|equities|sector)|"
    r"property (?:stocks|shares)|stock market|stocks|Wall Street|Dow|S&P 500|Nasdaq)\b", re.I)
RECAP_MOVE = re.compile(
    r"\b(?:slid|slides?|sliding|f[ae]ll|falls|falling|drop(?:s|ped|ping)?|sank|sinks?|sunk|"
    r"tumbl\w*|declin\w*|slump\w*|retreat\w*|rise|rises|rising|rose|rall\w*|climb\w*|"
    r"gain\w*|jump\w*|surg\w*|soar\w*|rebound\w*)\b", re.I)


def is_recap(row) -> bool:
    title = row["title"] or ""
    return bool(RECAP_SUBJECT.search(title) and RECAP_MOVE.search(title))


def _published(row) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(row["published_at"]))
    except (TypeError, ValueError, KeyError, IndexError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=ET)


def drop_stale_recaps(rows: list, anchor: datetime) -> list:
    """Remove REIT/stock-market recap stories published more than RECAP_MAX_HOURS before
    the run's 5 AM anchor."""
    cutoff = anchor - timedelta(hours=RECAP_MAX_HOURS)
    out = []
    for r in rows:
        pub = _published(r)
        if is_recap(r) and pub is not None and pub < cutoff:
            continue
        out.append(r)
    return out


# Politically charged government-agency deals (immigration detention, prisons) are real
# news but not market-moving: they rank below other stories for Top Stories and stay
# eligible for Quick Hits. "ICE" is matched case-sensitively and not as the exchange
# (ICE Mortgage Technology, ICE BofA indexes).
CHARGED = re.compile(
    r"\b(?:Immigration and Customs Enforcement|immigration detention|detention (?:center|"
    r"facility|facilities|complex|beds?)|deportation|prisons?|jails?|correctional|"
    r"incarcerat\w*)\b", re.I)
CHARGED_ICE = re.compile(r"\bICE\b(?!\s+(?:Mortgage|Data|Futures|Benchmark|BofA))")


def is_charged(row) -> bool:
    text = f"{row['title'] or ''} {row['summary'] or ''}"
    return bool(CHARGED.search(text) or CHARGED_ICE.search(text))


def pick_top(news: list, n: int) -> tuple[list, list]:
    """(top stories, the rest in rank order). Charged stories go to Top Stories only when
    there are not enough other stories."""
    calm = [r for r in news if not is_charged(r)]
    top = (calm + [r for r in news if is_charged(r)])[:n]
    taken = {id(r) for r in top}
    return top, [r for r in news if id(r) not in taken]


DISTRESS = re.compile(
    r"\b(?:default\w*|foreclos\w*|special servic\w*|delinquen\w*|distress\w*|bankrupt\w*|"
    r"receivership|non-?performing|missed payments?|loan losses?|workouts?|"
    r"deed[- ]in[- ]lieu|note sales?|maturity wall)\b", re.I)


def pick_distress(window: list, exclude: set[str]):
    """The Distress Watch story: the best-ranked distress story whose URL is not in
    `exclude` (the Debt Markets and Top Stories picks), so the line never repeats a story
    told above. None when there is none."""
    for r in window:
        if r["url"] in exclude or r["section"] not in ("debt", "top", "deal"):
            continue
        if DISTRESS.search(f"{r['title']} {r['summary'] or ''}"):
            return r
    return None


def _pct(value: float) -> str:
    """"+1.2%", "-0.4%"; a change that rounds to zero has no sign: "0.0%"."""
    r = round(value, 1) + 0.0  # + 0.0 turns -0.0 into 0.0
    return "0.0%" if r == 0 else f"{r:+.1f}%"


def _day(d: date) -> str:
    return f"{d:%b} {d.day}"


def _quarter(d: date) -> str:
    return f"Q{(d.month - 1) // 3 + 1} {d.year}"


def fmt_spread(value_pct: float) -> str:
    """A spread in percentage points as bps: 0.45 -> "45 bps", -0.12 -> "-12 bps"."""
    n = round(value_pct * 100)
    return f"{n} bps"


def _extra_values(conn: sqlite3.Connection, run_date: date) -> tuple[dict, dict]:
    """Values for EXTRA_SERIES, plus {key: date of its latest observation}."""
    values, dates = {}, {}
    for key, sid, kind, asof in EXTRA_SERIES:
        points = get_rates(conn, sid, run_date - timedelta(days=lookback_days(sid)))
        if not points:
            values[key] = values[f"{key}_CHG"] = NA
            if asof:
                values[f"{key}_ASOF"] = NA
            continue
        last, chg = latest_with_change(points)
        dates[key] = last.date
        if kind == "billions":
            values[key] = f"${last.value:,.0f}B"
            prev = sorted(points, key=lambda p: p.date)[-2].value if len(points) > 1 else None
            values[f"{key}_CHG"] = _pct((last.value / prev - 1) * 100) if prev else NA
        else:
            values[key] = fmt_spread(last.value) if kind == "spread" else f"{last.value:.2f}%"
            values[f"{key}_CHG"] = fmt_bps(chg)
        if asof:
            values[f"{key}_ASOF"] = _quarter(last.date) if asof == "quarter" else _day(last.date)
    return values, dates


def fed_target(conn: sqlite3.Connection, run_date: date) -> tuple[str, str] | None:
    """Fed funds target range ("3.75% to 4.00%") and the upper bound's change vs the prior
    day in bps, from FRED DFEDTARL/DFEDTARU. None if either series is missing."""
    latest = {}
    for sid in FED_TARGET:
        points = get_rates(conn, sid, run_date - timedelta(days=lookback_days(sid)))
        if not points:
            return None
        latest[sid] = latest_with_change(points)
    (lo, _), (hi, chg) = latest["DFEDTARL"], latest["DFEDTARU"]
    return f"{lo.value:.2f}% to {hi.value:.2f}%", fmt_bps(chg)


CURVE_LEGS = ("DGS10", "DGS2")  # the 10Y-2Y curve is computed from these, same date
BIG_MOVE_BPS = 15  # a rates row moving this much is a "big mover" for "What it means"
BIG_MOVE_ROWS = (("DGS10", "10-Year Treasury"), ("DGS5", "5-Year Treasury"),
                 ("DGS2", "2-Year Treasury"), ("T10Y2Y", "10Y-2Y curve"), ("SOFR", "SOFR"),
                 ("DFF", "Fed Funds"), ("MORTGAGE30US", "30-Year Mortgage"))


def same_date_curve(conn: sqlite3.Connection, run_date: date):
    """The 10Y-2Y curve from the 10Y and 2Y on the SAME date: (date, bps, change in bps vs
    the previous date both exist, or None). None when they share no date (then the FRED
    T10Y2Y series is the fallback)."""
    legs = {}
    for sid in CURVE_LEGS:
        pts = get_rates(conn, sid, run_date - timedelta(days=lookback_days(sid)))
        legs[sid] = {p.date: p.value for p in pts}
    common = sorted(set(legs["DGS10"]) & set(legs["DGS2"]))
    if not common:
        return None
    spread = {d: legs["DGS10"][d] - legs["DGS2"][d] for d in common}
    last = common[-1]
    chg = round((spread[last] - spread[common[-2]]) * 100) if len(common) > 1 else None
    return last, round(spread[last] * 100), chg


def big_movers(values: dict) -> list[str]:
    """Labels of rates rows whose change is at least BIG_MOVE_BPS either way."""
    out = []
    for key, label in BIG_MOVE_ROWS:
        m = re.match(r"\s*([+-]?\d+)\s*bps", str(values.get(f"{key}_CHG", "")))
        if m and abs(int(m.group(1))) >= BIG_MOVE_BPS:
            out.append(label)
    return out


def move_size(chg_bps: int | None) -> str | None:
    """How big the 10Y move was: "unchanged" (under 3 bps), "small" (3 to 9), "notable"
    (10 or more). None when there is no change to measure."""
    if chg_bps is None:
        return None
    size = abs(chg_bps)
    if size < 3:
        return "unchanged"
    return "small" if size < 10 else "notable"


def _rate_values(conn: sqlite3.Connection,
                 run_date: date) -> tuple[dict, float | None, int | None]:
    """(values, 10Y level, 10Y change in bps)."""
    values, dates = {}, {}
    dgs10: float | None = None
    dgs10_chg: int | None = None
    for series in RATE_SERIES:
        points = get_rates(conn, series, run_date - timedelta(days=lookback_days(series)))
        if not points:
            values[series] = values[f"{series}_CHG"] = NA
            if series == "DGS10":
                values["RATES_ASOF"] = NA
            continue
        last, chg = latest_with_change(points)
        values[series] = f"{last.value:.2f}%"
        values[f"{series}_CHG"] = fmt_bps(chg)
        dates[series] = last.date
        if series == "DGS10":
            dgs10, dgs10_chg = last.value, chg
            values["RATES_ASOF"] = _day(last.date)
    extra, extra_dates = _extra_values(conn, run_date)
    values.update(extra)
    dates.update(extra_dates)
    curve = same_date_curve(conn, run_date)
    if curve is not None:  # same-date legs replace FRED's T10Y2Y (which can mix dates)
        d, bps, chg = curve
        values["T10Y2Y"], values["T10Y2Y_CHG"] = f"{bps} bps", fmt_bps(chg)
        dates["T10Y2Y"] = d
    target = fed_target(conn, run_date)
    if target is not None:  # the target range replaces the effective rate (DFF fallback)
        values["DFF"], values["DFF_CHG"] = target
        dates.pop("DFF", None)  # a policy setting, valid every day: no as-of needed
    if values.get("HY_OAS_ASOF") in (values.get("RATES_ASOF"), NA):
        values.pop("HY_OAS_ASOF", None)  # same day as the other rates: no label needed
    asof = dates.get("DGS10")
    for key in ROW_ASOF_KEYS:
        d = dates.get(key)
        if d is not None and asof is not None and d != asof:
            values[f"{key}_ASOF"] = _day(d)
        else:
            values.pop(f"{key}_ASOF", None)
    for key in DATED_KEYS + ("DGS10", "DGS2", "T10Y2Y"):  # curve legs: consistency check
        if key in dates:
            values[f"{key}_DATE"] = dates[key].isoformat()
    return values, dgs10, dgs10_chg


FED_HOLD_WORDS = re.compile(r"no change|hold|unchanged|pause|maintain", re.I)
FED_CUT_WORDS = re.compile(r"decrease|cut|lower", re.I)
FED_HIKE_WORDS = re.compile(r"increase|hike|raise", re.I)


def fed_bucket(label: str) -> str | None:
    """'25 bps decrease' -> 'cut', 'No change' -> 'hold', '25+ bps increase' -> 'hike'."""
    if FED_HOLD_WORDS.search(label):
        return "hold"
    if FED_CUT_WORDS.search(label):
        return "cut"
    if FED_HIKE_WORDS.search(label):
        return "hike"
    return None


def _to_100(sums: dict[str, float]) -> dict[str, float]:
    """Scale to percentages with one decimal that add up to exactly 100.0 (largest
    remainder: leftover tenths go to the buckets that lost the most to rounding)."""
    total = sum(sums.values())
    if total <= 0:
        return {}
    tenths = {k: v / total * 1000 for k, v in sums.items()}
    floors = {k: int(t) for k, t in tenths.items()}
    short = 1000 - sum(floors.values())
    for k in sorted(tenths, key=lambda k: floors[k] - tenths[k])[:short]:
        floors[k] += 1
    return {k: n / 10 for k, n in floors.items()}


def next_meeting(run_date: date) -> date | None:
    """The next FOMC decision day on or after run_date (config/fomc.yaml), or None."""
    return next((d for d in sorted(_load_yaml("config/fomc.yaml")) if d >= run_date), None)


def _fed_values(odds: FedOdds | None, run_date: date) -> tuple[dict, list[str]]:
    """Fed values plus the names of any outcomes that fit none of cut/hold/hike."""
    # Meeting date comes from the FOMC calendar, independent of the odds feed.
    nxt = next_meeting(run_date)
    values = {"FED_MEETING": _day(nxt) if nxt else NA, "FED_TOP": NA,
              "FED_CUT": NA, "FED_HOLD": NA, "FED_HIKE": NA}
    unmapped: list[str] = []
    if odds is not None and odds.outcomes:
        label, prob = odds.outcomes[0]
        values["FED_TOP"] = f"{label} {prob * 100:.1f}%"
        if odds.as_of:  # Fed chart caption: "Odds as of Oct 6"
            values["FED_ASOF"] = _day(odds.as_of)
        sums: dict[str, float] = {}
        for label, prob in odds.outcomes:
            bucket = fed_bucket(label)
            if bucket is None:
                unmapped.append(label)
            else:
                sums[bucket] = sums.get(bucket, 0.0) + prob
        for bucket, pct in _to_100(sums).items():  # buckets with no market stay n/a
            values[f"FED_{bucket.upper()}"] = f"{pct:.1f}%"
    return values, unmapped


FED_DISAGREE_PP = 10  # Polymarket vs Kalshi "hold" gap (percentage points) worth a note


def bucket_odds(odds: FedOdds | None) -> dict[str, float]:
    """{"cut"/"hold"/"hike": percent} summing to 100, from any odds source."""
    if odds is None or not odds.outcomes:
        return {}
    sums: dict[str, float] = {}
    for label, prob in odds.outcomes:
        bucket = fed_bucket(label)
        if bucket is not None:
            sums[bucket] = sums.get(bucket, 0.0) + prob
    return _to_100(sums)


def kalshi_check(values: dict, kalshi: FedOdds | None) -> tuple[str | None, str | None]:
    """Sets KALSHI_HOLD ("84.0%", the hint line under the Fed odds) and returns
    (provenance for the Fed odds, note). Polymarket stays the published number; a hold gap
    over FED_DISAGREE_PP points gets a note."""
    k = bucket_odds(kalshi)
    if "hold" not in k:
        return ("Polymarket" if values.get("FED_HOLD", NA) != NA else None), None
    values["KALSHI_HOLD"] = f"{k['hold']:.1f}%"
    poly = values.get("FED_HOLD", NA)
    if poly == NA:
        return None, None
    gap = abs(float(poly.rstrip("%")) - k["hold"])
    if gap > FED_DISAGREE_PP:
        return ("Polymarket (Kalshi differs)",
                f"fed: Polymarket hold {poly} vs Kalshi hold {k['hold']:.1f}%")
    return "Polymarket+Kalshi", None


TREASURY_IDS = ("DGS2", "DGS5", "DGS10", "DGS30")
TREASURY_NAMES = {"DGS2": "2-Year", "DGS5": "5-Year", "DGS10": "10-Year", "DGS30": "30-Year"}


def data_checks_line(quotes: list[ReitQuote], rate_checks: dict, fed_sources: str | None
                     ) -> str:
    """The plain-text "Data checks" line at the bottom of the Data Room, built by code,
    e.g. "Prices confirmed by 2 sources for 4 of 12 REITs. Treasury yields matched across
    FRED and Treasury.gov." Empty when nothing was checked."""
    parts = []
    if quotes:
        both = sum(1 for q in quotes if "+" in (q.source or ""))
        parts.append(f"Prices confirmed by 2 sources for {both} of {len(quotes)} REITs."
                     if both else "REIT prices came from a single source today.")
    checked = {k: v for k, v in (rate_checks or {}).items() if k in TREASURY_IDS}
    if checked:
        differ = [TREASURY_NAMES[k] for k in TREASURY_IDS if checked.get(k) is False]
        if differ:
            parts.append(f"Treasury.gov used for the {', '.join(differ)} "
                         "(FRED showed a different number).")
        else:
            parts.append("Treasury yields matched across FRED and Treasury.gov.")
    if fed_sources == "Polymarket+Kalshi":
        parts.append("Fed odds in line with Kalshi.")
    elif fed_sources == "Polymarket (Kalshi differs)":
        parts.append("Fed odds differ between Polymarket and Kalshi; both shown.")
    return " ".join(parts)


MOVER_NOTE = "No company-specific news today; it may have moved with other {group} REITs."


def _reit_names(values: dict, info: dict) -> None:
    """REIT_UP_NAME "NNN REIT (NNN)" and REIT_UP_TYPE "Owns single-tenant retail..." (same
    for REIT_DOWN) from config/sources.yaml `reit_info`, for the Market Summary rows."""
    for key in ("REIT_UP", "REIT_DOWN"):
        raw = values.get(key, NA)
        ticker = raw.split()[0] if raw != NA else None
        meta = (info or {}).get(ticker) or {}
        name = meta.get("name")
        # "NNN REIT (NNN)", but just "UDR" when the company name is the ticker itself.
        values[f"{key}_NAME"] = (f"{name} ({ticker})" if name and name != ticker
                                 else ticker or NA)
        values[f"{key}_TYPE"] = meta.get("type") or NA


def _mentions(row, name: str | None, ticker: str) -> bool:
    text = row["title"]  # title only: a passing mention in a summary is not "the reason"
    if name and re.search(r"\b" + re.escape(name) + r"\b", text, re.I):
        return True
    # Tickers: case-sensitive, and not one or two letters ("O" would match everything).
    return len(ticker) >= 3 and re.search(r"\b" + re.escape(ticker) + r"\b", text) is not None


def _mover_news(values: dict, info: dict, window: list) -> dict:
    """{"up": story|None, "down": story|None} for movers that exist. A story is the
    best-ranked item in the window naming the company or ticker. With no story, code
    fills MOVER_UP_NOTE / MOVER_DOWN_NOTE so the model never guesses a reason."""
    news = {}
    for side, key in (("up", "REIT_UP"), ("down", "REIT_DOWN")):
        raw = values.get(key, NA)
        if raw == NA:
            values[f"MOVER_{side.upper()}_NOTE"] = NA
            continue
        ticker = raw.split()[0]
        meta = (info or {}).get(ticker) or {}
        row = next((r for r in window if _mentions(r, meta.get("name"), ticker)), None)
        news[side] = _story(row) if row is not None else None
        values[f"MOVER_{side.upper()}_NOTE"] = MOVER_NOTE.format(
            group=meta.get("group") or "real estate")
    return news


def _market_values(quotes: list[ReitQuote]) -> dict:
    values = {"VNQ": NA, "VNQ_CHG": NA, "REIT_UP": NA, "REIT_DOWN": NA}
    vnq = next((q for q in quotes if q.ticker == VNQ_TICKER), None)
    if vnq:
        values["VNQ"] = f"${vnq.close:.2f}"
        values["VNQ_CHG"] = _pct(vnq.change_pct)
    others = [q for q in quotes if q.ticker != VNQ_TICKER]
    if others:
        best = max(others, key=lambda q: q.change_pct)
        worst = min(others, key=lambda q: q.change_pct)
        values["REIT_UP"] = f"{best.ticker} {_pct(best.change_pct)}"
        if len(others) > 1:
            values["REIT_DOWN"] = f"{worst.ticker} {_pct(worst.change_pct)}"
    if quotes:  # the trading day these closes are from (main keeps one day only)
        values["REIT_ASOF"] = _day(max(q.date for q in quotes))
    return values


def reit_moves(quotes: list[ReitQuote], info: dict) -> list[dict]:
    """Every tracked REIT's daily move for the REIT scoreboard chart, best first.
    VNQ (the fund) is left out; tickers whose quote failed are simply absent. `type` is
    the short property type (`group` in reit_info), e.g. "warehouse"."""
    rows = []
    for q in quotes:
        if q.ticker == VNQ_TICKER:
            continue
        meta = (info or {}).get(q.ticker) or {}
        rows.append({"ticker": q.ticker, "name": meta.get("name") or q.ticker,
                     "type": meta.get("group") or "", "chg_pct": round(q.change_pct, 2)})
    return sorted(rows, key=lambda r: -r["chg_pct"])


TERM_COOLDOWN_DAYS = 120  # a term (or one in the same family) never returns sooner
TERM_FIRST_TOPICS = ("debt", "acquisition")  # unused terms in these run first, alternating
ISSUES_DIR = Path("issues")  # published issue files; tests point this elsewhere
_TERM_IN_MD = re.compile(r"^## Term of the Day\s*\n+\s*\*\*(.+?):?\*\*", re.M)


def _terms_used(conn: sqlite3.Connection, run_date: date,
                issues_dir: Path | None = None) -> dict[str, date]:
    """Last date each term ran, lowercased. Reads the DB and the issue files, so a term
    swapped by hand in a published issue counts too. Today's own issue is ignored, so a
    same-day rerun picks the same term."""
    last: dict[str, date] = {}

    def note(name: str, day: date) -> None:
        key = name.strip().rstrip(":").strip().lower()
        if key and (key not in last or day > last[key]):
            last[key] = day

    for r in conn.execute("SELECT date, term FROM issues WHERE term IS NOT NULL AND term != ''"
                          " AND date != ?", (run_date.isoformat(),)):
        note(r["term"], date.fromisoformat(r["date"]))
    for path in sorted(Path(issues_dir or ISSUES_DIR).glob("????-??-??.md")):
        try:
            day = date.fromisoformat(path.stem)
        except ValueError:
            continue
        if day >= run_date:
            continue
        m = _TERM_IN_MD.search(path.read_text(encoding="utf-8-sig"))
        if m:
            note(m.group(1), day)
    return last


def _term_order(terms: list[dict]):
    """Sort key for unused terms: debt and acquisition first, alternating (1st debt,
    1st acquisition, 2nd debt...), then every other topic in file order."""
    keys, seen = {}, {}
    for i, t in enumerate(terms):
        topic = t.get("topic")
        if topic in TERM_FIRST_TOPICS:
            n = seen[topic] = seen.get(topic, -1) + 1
            keys[t["term"]] = (0, n, TERM_FIRST_TOPICS.index(topic))
        else:
            keys[t["term"]] = (1, i, 0)
    return lambda t: keys[t["term"]]


def _pick_term(conn: sqlite3.Connection, run_date: date,
               issues_dir: Path | None = None) -> dict:
    terms = _load_yaml("config/terms.yaml")
    last = _terms_used(conn, run_date, issues_dir)

    def last_use(t: dict) -> date | None:
        # A term is as "recent" as the latest use of any term in its family.
        family = t.get("family")
        names = [x["term"] for x in terms if family and x.get("family") == family] or [t["term"]]
        days = [last[n.lower()] for n in names if n.lower() in last]
        return max(days) if days else None

    fresh = [t for t in terms if last_use(t) is None]
    if fresh:
        chosen = min(fresh, key=_term_order(terms))
    else:  # every term has run: the least recently used one, past the cooldown if possible
        rested = [t for t in terms if (run_date - last_use(t)).days >= TERM_COOLDOWN_DAYS]
        chosen = min(rested or terms, key=last_use)
    return {"term": chosen["term"], "definition_hint": chosen["hint"]}


def _week_ahead(run_date: date, events: list[dict] | None) -> dict:
    end = run_date + timedelta(days=7)
    meetings = [d for d in _load_yaml("config/fomc.yaml") if run_date < d <= end]
    return {"fomc_dates": [_day(d) for d in meetings], "FED_TOP": "FED_TOP",  # placeholder name
            "events": list(events or []), "list": "WEEK_AHEAD"}  # placeholder name


def _cmbs_values(cmbs: dict | None) -> dict:
    """Trepp CMBS delinquency values (see src/trepp.py). CMBS_DQ only when Trepp states it."""
    if not cmbs:
        return {"CMBS_DQ_CHG": NA, "CMBS_DQ_MONTH": NA}
    return dict(cmbs)


def _reit_week(conn: sqlite3.Connection, run_date: date,
               quotes: list[ReitQuote]) -> tuple[dict, dict]:
    """Best/worst non-VNQ tickers by % change, first -> last close in the window.

    The window starts 9 days back so a Sunday run's first close is the prior Friday.
    Needs at least 2 days per ticker. Returns (values, placeholder-name lists); the
    numbers live only in `values` as REITW_BEST_n / REITW_WORST_n. No ticker is in
    both lists (with <=6 tickers they split into top half and bottom half).
    """
    merged = {(q.ticker, q.date): q for q in get_quotes(conn, run_date - timedelta(days=9))}
    for q in quotes:
        merged[(q.ticker, q.date)] = q
    by_ticker: dict[str, list[ReitQuote]] = {}
    for q in merged.values():
        if q.ticker != VNQ_TICKER:
            by_ticker.setdefault(q.ticker, []).append(q)
    changes = []
    for ticker, qs in by_ticker.items():
        qs.sort(key=lambda q: q.date)
        if len(qs) >= 2 and qs[0].close:
            changes.append((ticker, (qs[-1].close / qs[0].close - 1) * 100))
    changes.sort(key=lambda c: -c[1])
    n_best = min(REIT_WEEK_PICKS, (len(changes) + 1) // 2)
    n_worst = min(REIT_WEEK_PICKS, len(changes) - n_best)
    best = changes[:n_best]
    worst = changes[len(changes) - n_worst:][::-1] if n_worst else []  # worst first
    values = {f"REITW_{side}_{i}": NA for side in ("BEST", "WORST")
              for i in range(1, REIT_WEEK_PICKS + 1)}
    names = {"best": [], "worst": []}
    for side, picks in (("best", best), ("worst", worst)):
        for i, (ticker, chg) in enumerate(picks, 1):
            key = f"REITW_{side.upper()}_{i}"
            values[key] = f"{ticker} {_pct(chg)}"
            names[side].append(key)
    return values, names


def as_of_dates(conn: sqlite3.Connection, run_date: date, values: dict,
                quotes: list[ReitQuote]) -> dict:
    """{key: "YYYY-MM-DD"} of each series' latest observation (see src/freshness.py).
    Uses the KEY_DATE values when the fact sheet already has them; otherwise reads the
    stored series. Series with no data are left out."""
    out = {}

    def latest(sid: str) -> date | None:
        pts = get_rates(conn, sid, run_date - timedelta(days=lookback_days(sid)))
        return max(p.date for p in pts) if pts else None

    ids = {"HY_OAS": "BAMLH0A0HYM2", "BANK_CRE_LOANS": "CREACBW027SBOG",
           "BANK_CRE_DQ": "DRCRELEXFACBS"}
    for key in ("DGS10", "DGS5", "DGS2", "DGS30", "SOFR", "HY_OAS", "MORTGAGE30US",
                "BANK_CRE_LOANS", "BANK_CRE_DQ"):
        d = values.get(f"{key}_DATE") or latest(ids.get(key, key))
        if d:
            out[key] = d.isoformat() if isinstance(d, date) else str(d)
    # Fed funds: the target range (DFEDTARU) when present, else the effective rate (DFF).
    d = latest(FED_TARGET[1]) or latest("DFF")
    if d:
        out["DFF"] = d.isoformat()
    vnq = [q.date for q in quotes if q.ticker == VNQ_TICKER]
    reits = [q.date for q in quotes if q.ticker != VNQ_TICKER]
    if vnq:
        out["VNQ"] = max(vnq).isoformat()
    if reits:
        out["REIT"] = max(reits).isoformat()
    if values.get("CMBS_DQ_DATE"):
        out["CMBS_DQ"] = str(values["CMBS_DQ_DATE"])[:10]
    return out


def build_factsheet(conn: sqlite3.Connection, run_date: date, odds: FedOdds | None,
                    quotes: list[ReitQuote], problems: list[str],
                    vnq_yield: float | None, *, cmbs: dict | None = None,
                    week_events: list[dict] | None = None,
                    kalshi: FedOdds | None = None, provenance: dict | None = None,
                    rate_checks: dict | None = None) -> dict:
    dtype = day_type(run_date)
    anchor = _anchor(run_date)
    sources = load_sources()
    hours = sources["lookback_hours"]
    lookback = hours["monday"] if run_date.weekday() == 0 else hours["default"]

    values, dgs10, dgs10_chg = _rate_values(conn, run_date)
    fed, unmapped = _fed_values(odds, run_date)
    values.update(fed)
    problems.extend(f"polymarket: unmapped outcome {name!r}" for name in unmapped)
    fed_sources, fed_note = kalshi_check(values, kalshi)
    if fed_note:
        problems.append(fed_note)
    data_sources = dict(provenance or {})
    if fed_sources:
        data_sources["FED"] = fed_sources
    checks_line = data_checks_line(quotes, rate_checks or {}, fed_sources)
    if checks_line:
        values["DATA_CHECKS"] = checks_line
    values.update(_market_values(quotes))
    _reit_names(values, sources.get("reit_info") or {})
    values.update(_cmbs_values(cmbs))
    if vnq_yield is None:
        values["VNQ_YIELD"] = values["SPREAD_10Y"] = NA
    else:
        values["VNQ_YIELD"] = f"{vnq_yield * 100:.2f}%"
        values["SPREAD_10Y"] = (NA if dgs10 is None
                                else fmt_bps(round((vnq_yield * 100 - dgs10) * 100)))

    # Stories already in an issue from the last 14 days are never offered again.
    used_urls, used_titles = used_story_keys(conn, run_date)
    window = drop_stale_recaps(
        _by_rank(drop_used(recent_items(conn, anchor - timedelta(hours=lookback)),
                           used_urls, used_titles)), anchor)
    news = [r for r in window if r["section"] in ("top", "deal")]
    top, rest = pick_top(news, TOP_COUNT.get(dtype, 0))
    debt = [r for r in window if r["section"] == "debt"][:DEBT_COUNT]
    distress = None
    if dtype in ("weekday", "friday"):  # never a story already in Debt Markets or Top Stories
        distress = pick_distress(window, {r["url"] for r in debt + top})
        rest = [r for r in rest if distress is None or r["url"] != distress["url"]]
    quick = rest[:QUICK_HITS_COUNT] if dtype in ("weekday", "friday") else []

    sheet = {
        "date": run_date.isoformat(),
        "day_type": dtype,
        "problems": list(problems),
        "values": values,
        # Latest observation date per series; checks + the publish gate read it.
        "as_of_dates": as_of_dates(conn, run_date, values, quotes),
        # 10Y move size, so "What it means" is scaled to the move (see the template).
        "move_size": move_size(dgs10_chg),
        # Rates rows that moved >= 15 bps (names only): "What it means" must acknowledge them.
        "big_movers": big_movers(values),
        "top": [_story(r) for r in top],
        "quick_hits": [_story(r) for r in quick],
        "debt": [_story(r) for r in debt],
        "ai": [_story(r) for r in _ai_rank(
            [r for r in window
             if r["section"] == "ai" and (r["importance"] or 0) >= AI_MIN_IMPORTANCE])][:1],
        "term": _pick_term(conn, run_date),
        # Chart data only (REIT scoreboard); kept out of the model's copy of the sheet.
        "reit_moves": reit_moves(quotes, sources.get("reit_info") or {}),
        # Which sources back each value ("AV+Stooq", "FRED+Treasury", "Polymarket+Kalshi").
        "sources": data_sources,
    }

    if dtype in ("weekday", "friday"):
        # Distress Watch: a distress story not used in Debt Markets (null: omit the line).
        sheet["distress"] = _story(distress) if distress is not None else None
        sheet["mover_news"] = _mover_news(values, sources.get("reit_info") or {}, window)
        # Market Watch: one story per region, never one already used above.
        taken = {s["url"] for key in ("top", "quick_hits", "debt", "ai") for s in sheet[key]}
        if sheet["distress"]:
            taken.add(sheet["distress"]["url"])
        picks = pick_markets([r for r in window if r["section"] not in ("other", "ai")], taken)
        sheet["markets"] = {region: (_story(row) if row is not None else None)
                            for region, row in picks.items()}

    if dtype == "saturday":
        week = _by_rank(recent_items(conn, anchor - timedelta(days=7)))
        sheet["week_top"] = [_story(r) for r in week][:WEEK_TOP_COUNT]
        sheet["ai_week"] = [_story(r) for r in _ai_rank(
            [r for r in week if r["section"] == "ai"])][:AI_WEEK_COUNT]
    elif dtype == "sunday":
        sheet["week_ahead"] = _week_ahead(run_date, week_events)
        values["WEEK_AHEAD"] = (CALENDAR_DOWN if week_events is None
                                else week_ahead_markdown(week_events))
        reit_values, sheet["reit_week"] = _reit_week(conn, run_date, quotes)
        values.update(reit_values)
    return sheet
