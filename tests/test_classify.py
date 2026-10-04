import json

from src.classify import classify, heuristic
from src.llm import LLMError
from src.store import connect


def _add(conn, id_, title, summary="", cluster_id=None):
    conn.execute(
        "INSERT INTO items (id, source, url, canonical_url, title, summary, cluster_id)"
        " VALUES (?, 'Bisnow', ?, ?, ?, ?, ?)",
        (id_, f"https://x.com/{id_}", f"x.com/{id_}", title, summary,
         id_ if cluster_id is None else cluster_id),
    )
    conn.commit()


def _row(conn, id_):
    return conn.execute("SELECT * FROM items WHERE id=?", (id_,)).fetchone()


def test_valid_json_applied():
    conn = connect(":memory:")
    _add(conn, 1, "Blackstone buys Dallas tower")
    reply = "Here you go:\n" + json.dumps([
        {"id": 1, "section": "deal", "asset_class": "office",
         "market": "Dallas", "importance": 8}])
    assert classify(conn, run=lambda p, m: reply) == 1
    r = _row(conn, 1)
    assert (r["section"], r["asset_class"], r["market"], r["importance"]) == (
        "deal", "office", "Dallas", 8)


def test_invalid_enum_row_dropped_then_heuristic():
    conn = connect(":memory:")
    _add(conn, 1, "Lender files foreclosure on mall")
    reply = json.dumps([{"id": 1, "section": "bogus", "asset_class": "retail",
                         "market": "Miami", "importance": 7}])
    assert classify(conn, run=lambda p, m: reply) == 1
    r = _row(conn, 1)
    assert r["section"] == "debt"
    assert r["asset_class"] == "none"
    assert r["market"] == "national"
    assert r["importance"] == 5


def test_llm_error_falls_back_to_heuristic():
    conn = connect(":memory:")
    _add(conn, 1, "CMBS delinquency rises")

    def boom(prompt, model):
        raise LLMError("down")

    assert classify(conn, run=boom) == 1
    assert _row(conn, 1)["section"] == "debt"


def test_only_unclassified_representatives_processed():
    conn = connect(":memory:")
    _add(conn, 1, "Rep story")
    _add(conn, 2, "Dup of rep", cluster_id=1)
    _add(conn, 3, "Already done")
    conn.execute("UPDATE items SET section='top' WHERE id=3")
    seen = []

    def fake(prompt, model):
        seen.append(prompt)
        return "[]"

    assert classify(conn, run=fake) == 1
    assert _row(conn, 2)["section"] is None
    assert _row(conn, 3)["section"] == "top"
    assert '"id": 2' not in seen[0] and '"id": 3' not in seen[0]


def test_importance_clamped_nonint_invalid_market_truncated():
    conn = connect(":memory:")
    _add(conn, 1, "A")
    _add(conn, 2, "B proptech launch")
    reply = json.dumps([
        {"id": 1, "section": "top", "asset_class": "none",
         "market": "M" * 100, "importance": 99},
        {"id": 2, "section": "top", "asset_class": "none",
         "market": "national", "importance": "high"},
    ])
    assert classify(conn, run=lambda p, m: reply) == 2
    assert _row(conn, 1)["importance"] == 10
    assert len(_row(conn, 1)["market"]) == 60
    assert _row(conn, 2)["section"] == "ai"  # heuristic


def test_batches_of_40():
    conn = connect(":memory:")
    for i in range(1, 46):
        _add(conn, i, f"Story {i}")
    calls = []

    def fake(prompt, model):
        calls.append(prompt)
        raise LLMError("x")

    assert classify(conn, run=fake) == 45
    assert len(calls) == 2


def test_heuristic_default():
    assert heuristic("Office tower sells", "") == {
        "section": "top", "asset_class": "none", "market": "national", "importance": 5}


def test_unhashable_reply_values_dropped_not_crash():
    conn = connect(":memory:")
    _add(conn, 1, "CMBS delinquency rises")
    _add(conn, 2, "Other story")
    reply = json.dumps([
        {"id": 1, "section": ["debt"], "asset_class": "office",
         "market": "x", "importance": 5},
        {"id": [2], "section": "top", "asset_class": "none",
         "market": "x", "importance": 5},
    ])
    assert classify(conn, run=lambda p, m: reply) == 2
    assert _row(conn, 1)["section"] == "debt"
    assert _row(conn, 2)["section"] == "top"
