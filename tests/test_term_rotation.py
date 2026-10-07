from datetime import date, timedelta

from src.factsheet import TERM_COOLDOWN_DAYS, _pick_term, _load_yaml
from src.store import connect

TERMS = _load_yaml("config/terms.yaml")


def _conn(tmp_path):
    return connect(str(tmp_path / "t.db"))


def _ran(conn, day, term):
    conn.execute("INSERT INTO issues(date, day_type, status, markdown, term) VALUES (?,?,?,?,?)",
                 (day.isoformat(), "weekday", "draft", "", term))
    conn.commit()


def test_pool_is_big_and_unique():
    names = [t["term"].lower() for t in TERMS]
    assert len(names) >= 120 and len(names) == len(set(names))


def test_never_repeats_until_every_term_has_run(tmp_path):
    conn = _conn(tmp_path)
    start = date(2026, 1, 1)
    seen = []
    for i in range(len(TERMS)):
        day = start + timedelta(days=i)
        term = _pick_term(conn, day, issues_dir=tmp_path)["term"]
        seen.append(term)
        _ran(conn, day, term)
    families = {t["family"] for t in TERMS if t.get("family")}
    family_terms = sum(1 for t in TERMS if t.get("family"))
    # Every term runs once before any repeat; a family counts as a single term.
    assert len(set(seen)) == len(TERMS) - family_terms + len(families)
    assert len(seen) == len(set(seen)) + (len(TERMS) - len(set(seen)))


def test_term_swapped_by_hand_in_issue_file_counts_as_used(tmp_path):
    conn = _conn(tmp_path)
    first = TERMS[0]["term"]
    (tmp_path / "2026-10-06.md").write_text(
        f"# x\n\n## Term of the Day\n\n**{first}:** a definition.\n", encoding="utf-8")
    assert _pick_term(conn, date(2026, 10, 7), issues_dir=tmp_path)["term"] != first


def test_family_members_share_a_cooldown(tmp_path):
    conn = _conn(tmp_path)
    fam = [t["term"] for t in TERMS if t.get("family") == "nnn"]
    assert len(fam) == 2
    _ran(conn, date(2026, 10, 5), fam[0])
    # Exhaust every other term so the picker must choose among used ones.
    day = date(2026, 10, 6)
    for t in TERMS:
        if t["term"] not in fam:
            _ran(conn, day, t["term"])
            day += timedelta(days=1)
    picked = _pick_term(conn, day, issues_dir=tmp_path)["term"]
    # The never-used family member must wait out the cooldown of its sibling.
    assert picked != fam[1] or (day - date(2026, 10, 5)).days >= TERM_COOLDOWN_DAYS


def test_wrap_picks_least_recent_past_cooldown(tmp_path):
    conn = _conn(tmp_path)
    start = date(2026, 1, 1)
    for i, t in enumerate(TERMS):
        _ran(conn, start + timedelta(days=i), t["term"])
    day = start + timedelta(days=len(TERMS))
    picked = _pick_term(conn, day, issues_dir=tmp_path)["term"]
    # TERMS[0] is in a family whose sibling ran last, so the oldest standalone term wins.
    assert picked == TERMS[1]["term"]


def test_every_term_has_a_topic():
    assert all(t.get("topic") in ("debt", "acquisition", "other") for t in TERMS)


def test_debt_and_acquisition_terms_run_first_alternating(tmp_path):
    conn = _conn(tmp_path)
    start = date(2026, 1, 1)
    topics = []
    first = sum(1 for t in TERMS if t["topic"] != "other")
    by_name = {t["term"]: t["topic"] for t in TERMS}
    for i in range(first):
        day = start + timedelta(days=i)
        term = _pick_term(conn, day, issues_dir=tmp_path)["term"]
        topics.append(by_name[term])
        _ran(conn, day, term)
    assert "other" not in topics
    assert topics[:4] == ["debt", "acquisition", "debt", "acquisition"]
    # Then the leasing/operations terms.
    nxt = _pick_term(conn, start + timedelta(days=first), issues_dir=tmp_path)["term"]
    assert by_name[nxt] == "other"
