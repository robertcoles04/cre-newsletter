"""SQLite store for items, rates, quotes and issues, with story dedupe."""

import json
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlsplit

from rapidfuzz import fuzz

from src.models import Item, RatePoint, ReitQuote

SIMILARITY_THRESHOLD = 85
WINDOW = timedelta(hours=48)
_DROP_PARAMS = ("utm_",)
_DROP_PARAM_NAMES = {"oc", "ref"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY,
    source TEXT, url TEXT, canonical_url TEXT UNIQUE, title TEXT,
    published_at TEXT, summary TEXT, priority INTEGER,
    cluster_id INTEGER, also_covered TEXT DEFAULT '[]',
    section TEXT, asset_class TEXT, market TEXT, importance INTEGER,
    used_in_issue TEXT, region TEXT
);
CREATE TABLE IF NOT EXISTS deals (
    id INTEGER PRIMARY KEY,
    item_id INTEGER, property TEXT, market TEXT, asset_class TEXT,
    buyer TEXT, seller TEXT, price_usd REAL, size REAL, size_unit TEXT,
    price_per REAL, cap_rate REAL, source_url TEXT, verified INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS rates (
    series TEXT, date TEXT, value REAL, PRIMARY KEY (series, date)
);
CREATE TABLE IF NOT EXISTS reit_quotes (
    ticker TEXT, date TEXT, close REAL, change_pct REAL, PRIMARY KEY (ticker, date)
);
CREATE TABLE IF NOT EXISTS issues (
    date TEXT PRIMARY KEY, day_type TEXT, status TEXT, markdown TEXT,
    source_problems TEXT, term TEXT, gh_issue INTEGER
);
CREATE TABLE IF NOT EXISTS used_stories (
    issue_date TEXT, canonical_url TEXT, title_key TEXT
);
CREATE INDEX IF NOT EXISTS used_stories_date ON used_stories(issue_date);
"""


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(items)")}
    if "region" not in cols:  # DBs created before Market Watch
        conn.execute("ALTER TABLE items ADD COLUMN region TEXT")
        conn.commit()
    return conn


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if parts.port:
        host = f"{host}:{parts.port}"
    path = parts.path.rstrip("/")
    kept = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith(_DROP_PARAMS) and k.lower() not in _DROP_PARAM_NAMES
    ]
    query = "&".join(f"{k}={v}" if v else k for k, v in kept)
    return host + path + (f"?{query}" if query else "")


def normalize_title(title: str) -> str:
    title = re.sub(r"\s+-\s+[^-]+$", "", title.strip())
    return re.sub(r"\s+", " ", title).lower()


def title_key(title: str) -> str:
    """Loose title for cross-day repeat matching: outlet suffix (" - The Real Deal",
    " | Bisnow") removed, lowercase, punctuation dropped."""
    t = re.sub(r"\s+[-|–—]\s+[^-|–—]+$", "", (title or "").strip())
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", t.lower())).strip()


REPEAT_DAYS = 14
_MD_LINK = re.compile(r"\]\((https?://[^)\s]+)\)")


def record_used_stories(conn: sqlite3.Connection, run_date: date, md: str,
                        factsheet: dict | None = None) -> None:
    """Remember every story linked in an issue (its URL and, when the factsheet knows
    it, its title) so later issues do not reuse it."""
    key = run_date.isoformat()
    urls = {canonical_url(u) for u in _MD_LINK.findall(md or "")}
    titles: dict[str, str] = {}
    if factsheet:
        stories = []
        for k in ("top", "quick_hits", "debt", "ai"):
            stories += factsheet.get(k) or []
        stories += [s for s in (factsheet.get("markets") or {}).values() if s]
        stories += [s for s in (factsheet.get("mover_news") or {}).values() if s]
        for st in stories:
            canon = canonical_url(st["url"])
            if canon in urls:
                titles[canon] = title_key(st["title"])
    conn.execute("DELETE FROM used_stories WHERE issue_date = ?", (key,))
    conn.executemany(
        "INSERT INTO used_stories(issue_date, canonical_url, title_key) VALUES (?,?,?)",
        [(key, u, titles.get(u)) for u in sorted(urls)])
    conn.commit()


def used_story_keys(conn: sqlite3.Connection, run_date: date,
                    days: int = REPEAT_DAYS) -> tuple[set[str], set[str]]:
    """(canonical URLs, title keys) used in issues dated within `days` before run_date."""
    rows = conn.execute(
        "SELECT canonical_url, title_key FROM used_stories"
        " WHERE issue_date >= ? AND issue_date < ?",
        ((run_date - timedelta(days=days)).isoformat(), run_date.isoformat())).fetchall()
    return ({r[0] for r in rows if r[0]}, {r[1] for r in rows if r[1]})


def drop_used(rows: list, urls: set[str], titles: set[str]) -> list:
    return [r for r in rows
            if canonical_url(r["url"]) not in urls and title_key(r["title"]) not in titles]


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _iso(dt: datetime) -> str:
    return _utc(dt).isoformat(timespec="seconds")


def save_items(conn: sqlite3.Connection, items: list[Item]) -> int:
    """Save items, merging same-story coverage. Returns the number of new clusters."""
    new_clusters = 0
    for item in items:
        canon = canonical_url(item.url)
        if conn.execute("SELECT 1 FROM items WHERE canonical_url = ?", (canon,)).fetchone():
            if item.region:  # same link found again by a regional query: keep the tag
                conn.execute("UPDATE items SET region = ? WHERE canonical_url = ?"
                             " AND (region IS NULL OR region = '')", (item.region, canon))
            continue
        pub = _utc(item.published_at)
        match = _find_match(conn, item, pub)
        if match is None:
            cur = conn.execute(
                "INSERT INTO items (source, url, canonical_url, title, published_at,"
                " summary, priority, also_covered, region) VALUES (?,?,?,?,?,?,?, '[]', ?)",
                (item.source, item.url, canon, item.title, _iso(pub), item.summary, item.priority,
                 item.region or None),
            )
            conn.execute("UPDATE items SET cluster_id = id WHERE id = ?", (cur.lastrowid,))
            new_clusters += 1
        else:
            _join_cluster(conn, item, canon, pub, match["cluster_id"])
    conn.commit()
    return new_clusters


def _find_match(conn, item: Item, pub: datetime):
    title = normalize_title(item.title)
    rows = conn.execute(
        "SELECT cluster_id, title FROM items WHERE published_at BETWEEN ? AND ?",
        (_iso(pub - WINDOW), _iso(pub + WINDOW)),
    ).fetchall()
    for row in rows:
        if fuzz.token_set_ratio(title, normalize_title(row["title"])) >= SIMILARITY_THRESHOLD:
            return row
    return None


def _join_cluster(conn, item: Item, canon: str, pub: datetime, cluster_id: int) -> None:
    rep = conn.execute("SELECT * FROM items WHERE id = ?", (cluster_id,)).fetchone()
    also = json.loads(rep["also_covered"] or "[]")
    if item.priority > rep["priority"]:
        # Newcomer takes over: old representative's source moves to also_covered.
        if rep["source"] not in also:
            also.append(rep["source"])
        also = [s for s in also if s != item.source]
        cur = conn.execute(
            "INSERT INTO items (source, url, canonical_url, title, published_at,"
            " summary, priority, also_covered) VALUES (?,?,?,?,?,?,?,?)",
            (item.source, item.url, canon, item.title, _iso(pub), item.summary,
             item.priority, json.dumps(also)),
        )
        new_id = cur.lastrowid
        conn.execute("UPDATE items SET cluster_id = ? WHERE cluster_id = ? OR id = ?",
                     (new_id, cluster_id, new_id))
        conn.execute("UPDATE items SET also_covered = '[]' WHERE id = ?", (cluster_id,))
    else:
        if item.source != rep["source"] and item.source not in also:
            also.append(item.source)
        conn.execute("UPDATE items SET also_covered = ? WHERE id = ?",
                     (json.dumps(also), cluster_id))
        conn.execute(
            "INSERT INTO items (source, url, canonical_url, title, published_at,"
            " summary, priority, cluster_id, also_covered) VALUES (?,?,?,?,?,?,?,?, '[]')",
            (item.source, item.url, canon, item.title, _iso(pub), item.summary,
             item.priority, cluster_id),
        )


def save_rates(conn: sqlite3.Connection, points: list[RatePoint]) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO rates (series, date, value) VALUES (?,?,?)",
        [(p.series, p.date.isoformat(), p.value) for p in points],
    )
    conn.commit()


def save_quotes(conn: sqlite3.Connection, quotes: list[ReitQuote]) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO reit_quotes (ticker, date, close, change_pct) VALUES (?,?,?,?)",
        [(q.ticker, q.date.isoformat(), q.close, q.change_pct) for q in quotes],
    )
    conn.commit()


def get_rates(conn: sqlite3.Connection, series: str, since: date) -> list[RatePoint]:
    rows = conn.execute(
        "SELECT series, date, value FROM rates WHERE series = ? AND date >= ? ORDER BY date",
        (series, since.isoformat()),
    ).fetchall()
    return [RatePoint(r["series"], date.fromisoformat(r["date"]), r["value"]) for r in rows]


def recent_items(conn: sqlite3.Connection, since: datetime) -> list[sqlite3.Row]:
    """Cluster representatives published at or after `since`, newest first."""
    return conn.execute(
        "SELECT * FROM items WHERE id = cluster_id AND published_at >= ?"
        " ORDER BY published_at DESC",
        (_iso(since),),
    ).fetchall()


def get_quotes(conn: sqlite3.Connection, since: date) -> list[ReitQuote]:
    rows = conn.execute(
        "SELECT ticker, date, close, change_pct FROM reit_quotes WHERE date >= ?"
        " ORDER BY ticker, date",
        (since.isoformat(),),
    ).fetchall()
    return [ReitQuote(r["ticker"], date.fromisoformat(r["date"]), r["close"], r["change_pct"])
            for r in rows]
