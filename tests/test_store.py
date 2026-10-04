from datetime import datetime, timezone, timedelta, date

from src.models import Item, RatePoint, ReitQuote
from src.store import (
    canonical_url, normalize_title, connect, save_items,
    save_rates, save_quotes, get_rates, recent_items,
)
import json

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def test_canonical_url_strips_tracking():
    assert canonical_url("https://www.x.com/a/?utm_source=t#f") == "x.com/a"


def test_normalize_title_strips_source_suffix():
    assert normalize_title("Big Deal Closes - GlobeSt") == "big deal closes"


def test_same_story_two_outlets_clusters():
    conn = connect(":memory:")
    a = Item("Bisnow", "https://bisnow.com/1", "Blackstone buys Dallas tower for $400M",
             T0, priority=80)
    b = Item("Commercial Observer", "https://commercialobserver.com/2",
             "Blackstone buys Dallas tower for $400 million", T0 + timedelta(hours=3),
             priority=90)
    assert save_items(conn, [a, b]) == 1
    rows = recent_items(conn, T0 - timedelta(days=1))
    assert len(rows) == 1
    assert rows[0]["source"] == "Commercial Observer"
    assert json.loads(rows[0]["also_covered"]) == ["Bisnow"]


def test_duplicate_url_ignored():
    conn = connect(":memory:")
    it = Item("Bisnow", "https://www.bisnow.com/1/?utm_source=x", "Some title", T0)
    again = Item("Bisnow", "https://bisnow.com/1", "Some title", T0)
    assert save_items(conn, [it]) == 1
    assert save_items(conn, [again]) == 0
    assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1


def test_lower_priority_newcomer_keeps_representative():
    conn = connect(":memory:")
    a = Item("CO", "https://co.com/1", "Prologis acquires warehouse portfolio", T0, priority=90)
    b = Item("Bisnow", "https://bisnow.com/1", "Prologis acquires warehouse portfolio", T0, priority=80)
    assert save_items(conn, [a, b]) == 1
    rows = recent_items(conn, T0 - timedelta(days=1))
    assert [r["source"] for r in rows] == ["CO"]
    assert json.loads(rows[0]["also_covered"]) == ["Bisnow"]


def test_outside_48h_window_is_new_cluster():
    conn = connect(":memory:")
    a = Item("A", "https://a.com/1", "Prologis acquires warehouse portfolio", T0)
    b = Item("B", "https://b.com/1", "Prologis acquires warehouse portfolio", T0 + timedelta(hours=60))
    assert save_items(conn, [a, b]) == 2


def test_naive_datetime_treated_as_utc():
    conn = connect(":memory:")
    save_items(conn, [Item("A", "https://a.com/1", "t", datetime(2026, 10, 1, 12, 0))])
    row = conn.execute("SELECT published_at FROM items").fetchone()
    assert row[0].endswith("+00:00")


def test_rates_and_quotes_roundtrip():
    conn = connect(":memory:")
    save_rates(conn, [RatePoint("DGS10", date(2026, 10, 2), 4.2),
                      RatePoint("DGS10", date(2026, 10, 1), 4.1)])
    save_rates(conn, [RatePoint("DGS10", date(2026, 10, 1), 4.15)])  # upsert
    pts = get_rates(conn, "DGS10", date(2026, 10, 1))
    assert [(p.date, p.value) for p in pts] == [(date(2026, 10, 1), 4.15), (date(2026, 10, 2), 4.2)]
    assert get_rates(conn, "DGS10", date(2026, 10, 2))[0].date == date(2026, 10, 2)
    save_quotes(conn, [ReitQuote("PLD", date(2026, 10, 1), 120.0, 1.5)])
    assert conn.execute("SELECT close FROM reit_quotes").fetchone()[0] == 120.0


def test_get_quotes_filters_by_date():
    from src.store import get_quotes
    conn = connect(":memory:")
    save_quotes(conn, [ReitQuote("O", date(2026, 9, 1), 50.0, 0.1),
                       ReitQuote("O", date(2026, 10, 2), 51.0, 2.0)])
    got = get_quotes(conn, date(2026, 10, 1))
    assert [(q.ticker, q.date, q.close) for q in got] == [("O", date(2026, 10, 2), 51.0)]
