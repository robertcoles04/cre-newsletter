from datetime import date, datetime, timedelta, timezone

from src.config import ET
from src.factsheet import build_factsheet
from src.markets import pick
from src.store import connect, record_used_stories, title_key, used_story_keys

TUE = datetime(2026, 10, 6, 5, 0, tzinfo=ET)


def add(conn, url, title, section="top", importance=5):
    pub = (TUE - timedelta(hours=2)).astimezone(timezone.utc).isoformat(timespec="seconds")
    cur = conn.execute(
        "INSERT INTO items (source, url, canonical_url, title, published_at, summary,"
        " priority, also_covered, section, importance) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("Src", url, url.split("//")[1], title, pub, "sum", 50, "[]", section, importance))
    conn.execute("UPDATE items SET cluster_id = id WHERE id = ?", (cur.lastrowid,))
    conn.commit()


def sheet(conn):
    return build_factsheet(conn, date(2026, 10, 6), None, [], [], None)


def titles(s):
    return {x["title"] for k in ("top", "quick_hits") for x in s[k]}


def seed(conn, issue_day=date(2026, 10, 5)):
    add(conn, "https://a.com/yamaha", "Disney buys Yamaha HQ - The Real Deal", importance=9)
    add(conn, "https://a.com/other", "Other story", importance=3)
    record_used_stories(conn, issue_day, "[Disney](https://a.com/yamaha)",
                        {"top": [{"url": "https://a.com/yamaha",
                                  "title": "Disney buys Yamaha HQ - The Real Deal"}]})


def test_repeat_excluded_by_url():
    conn = connect(":memory:")
    seed(conn)
    assert titles(sheet(conn)) == {"Other story"}


def test_repeat_excluded_by_title_with_different_url():
    conn = connect(":memory:")
    seed(conn)
    add(conn, "https://news.google.com/rss/articles/zzz",
        "Disney Buys Yamaha HQ! - Bisnow", importance=9)
    assert titles(sheet(conn)) == {"Other story"}


def test_window_is_14_days():
    conn = connect(":memory:")
    seed(conn, issue_day=date(2026, 9, 20))  # 16 days earlier: expired
    assert "Disney buys Yamaha HQ - The Real Deal" in titles(sheet(conn))
    urls, _ = used_story_keys(conn, date(2026, 10, 6))
    assert urls == set()
    record_used_stories(conn, date(2026, 9, 25), "[x](https://a.com/yamaha)")  # 11 days
    assert "disney" not in "".join(titles(sheet(conn))).lower()


def test_title_key():
    assert title_key("Disney Buys Yamaha HQ! - The Real Deal") == "disney buys yamaha hq"


def row(url, title, summary="", region=None):
    return {"url": url, "title": title, "summary": summary, "region": region}


def test_investing_canada_generic_story_rejected():
    r = row("u1", "Buyers demand price cuts as deals stall", "Investing.com Canada",
            region="international")
    assert pick([r], set())["international"] is None


def test_london_story_accepted():
    r = row("u2", "London office investors return", "", region="international")
    assert pick([r], set())["international"] is r


def test_new_mexico_is_not_international():
    r = row("u3", "New Mexico apartment sale", "")
    assert pick([r], set())["international"] is None


def test_backfill_records_links_from_existing_issue_files(tmp_path):
    from datetime import date
    from src.main import _backfill_used_stories
    from src.store import connect, used_story_keys
    conn = connect(str(tmp_path / "t.db"))
    issues = tmp_path / "issues"
    issues.mkdir()
    (issues / "2026-10-05.md").write_text("- Disney deal [TRD](https://therealdeal.com/disney)\n",
                                          encoding="utf-8")
    (issues / "2026-09-01.md").write_text("[Old](https://example.com/old)\n", encoding="utf-8")
    _backfill_used_stories(conn, tmp_path, date(2026, 10, 7))
    urls, _ = used_story_keys(conn, date(2026, 10, 7))
    assert any("therealdeal.com/disney" in u for u in urls)
    assert not any("example.com/old" in u for u in urls)  # outside the 14-day window
