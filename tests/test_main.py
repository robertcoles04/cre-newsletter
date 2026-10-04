from datetime import date, datetime

import pytest

from src import llm, main
from src.checks import FOOTER
from src.config import ET
from src.models import Item, RatePoint, SourceResult

D = date(2026, 10, 6)  # Tuesday
NOW_5AM = datetime(2026, 10, 6, 5, 0, tzinfo=ET)

DRAFT = f"""## The Numbers

- **10Y Treasury:** {{{{DGS10}}}} ({{{{DGS10_CHG}}}})
- **Fed:** {{{{FED_TOP}}}}

![Chart of the Day](img/{{{{DATE}}}}-chart.png)

## Top Stories

### Big deal closes

Two sentences here. Short ones.

[source](https://example.com/a)

{FOOTER}
"""


class Args:
    def __init__(self, **kw):
        self.date = kw.get("date")
        self.force = kw.get("force", False)
        self.dry_run = kw.get("dry_run", True)
        self.db = kw["db"]


def good_claude(prompt, model):
    return DRAFT


def dead_claude(prompt, model):
    raise llm.LLMError("login expired")


class FakeClient:
    def close(self):
        pass


@pytest.fixture
def fakes(monkeypatch):
    """Patch every network collector so a run touches nothing external."""
    monkeypatch.setenv("FRED_API_KEY", "x")
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "x")
    item = Item("Bisnow", "https://example.com/a", "Big deal closes",
                datetime(2026, 10, 5, 20, 0, tzinfo=ET), "A $50 million sale.", 80)
    monkeypatch.setattr(main.rss, "fetch_feed", lambda *a, **k: [item])
    monkeypatch.setattr(main.google_news, "fetch", lambda *a, **k: [])
    monkeypatch.setattr(main.polymarket, "fetch_fed_odds", lambda client, today: None)
    monkeypatch.setattr(main.reits, "fetch_quotes", lambda t, k, c: ([], []))
    monkeypatch.setattr(main.reits, "fetch_etf_yield", lambda e, k, c: None)

    def rates(conn, series, key, client, today):
        from src.store import save_rates
        save_rates(conn, [RatePoint("DGS10", date(2026, 10, 2), 4.1),
                          RatePoint("DGS10", date(2026, 10, 5), 4.2)])
        return [SourceResult(s, True) for s in series]
    monkeypatch.setattr(main, "collect_rates", rates)


def run(tmp_path, claude=good_claude, now=NOW_5AM, gh=lambda a: "x/1", **kw):
    args = Args(db=str(tmp_path / "t.db"), **kw)
    code = main.run(args, client=FakeClient(), claude=claude, gh=gh,
                    now=now, repo_root=tmp_path)
    return code, tmp_path / "issues" / f"{D.isoformat()}.md"


def test_gate_skips_outside_5am(tmp_path, fakes):
    code, path = run(tmp_path, now=datetime(2026, 10, 6, 9, 0, tzinfo=ET))
    assert code == 0 and not path.exists()


def test_gate_bypassed_by_force_and_date(tmp_path, fakes):
    code, path = run(tmp_path, now=datetime(2026, 10, 6, 9, 0, tzinfo=ET), force=True)
    assert code == 0 and path.exists()


def test_source_failure_recorded_and_run_continues(tmp_path, fakes, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("fred down")
    monkeypatch.setattr(main, "collect_rates", boom)
    code, path = run(tmp_path)
    assert code == 0 and path.exists()
    assert "fred: RuntimeError: fred down" in path.read_text(encoding="utf-8")


def test_feed_failure_does_not_stop_run(tmp_path, fakes, monkeypatch):
    def boom(*a, **k):
        raise ValueError("bad xml")
    monkeypatch.setattr(main.rss, "fetch_feed", boom)
    monkeypatch.setattr(main, "load_sources", lambda: {
        "feeds": [{"name": "Test Feed", "url": "http://x", "priority": 1}],
        "fred_series": ["DGS10"], "reit_etf": "VNQ", "reit_tickers": []})
    code, path = run(tmp_path)
    assert code == 0 and "feed/Test Feed" in path.read_text(encoding="utf-8")


def test_missing_keys_become_problems(tmp_path, fakes, monkeypatch):
    monkeypatch.setattr(main, "env", lambda name, required=True: None)
    code, path = run(tmp_path)
    text = path.read_text(encoding="utf-8")
    assert "fred: missing FRED_API_KEY" in text
    assert "reits: missing ALPHA_VANTAGE_API_KEY" in text


def test_rate_result_problems(tmp_path, fakes, monkeypatch):
    monkeypatch.setattr(main, "collect_rates", lambda c, s, k, cl, t: [
        SourceResult("SOFR", False, "HTTPError"), SourceResult("DGS10", True, "HTTPError")])
    code, path = run(tmp_path)
    text = path.read_text(encoding="utf-8")
    assert "rates/SOFR: HTTPError" in text and "rates/DGS10: fallback treasury" in text


def test_failed_reits_one_line(tmp_path, fakes, monkeypatch):
    monkeypatch.setattr(main.reits, "fetch_quotes", lambda t, k, c: ([], ["PLD", "O"]))
    code, path = run(tmp_path)
    assert "reits: failed PLD, O" in path.read_text(encoding="utf-8")


def test_llm_failure_delivers_factsheet_fallback(tmp_path, fakes):
    code, path = run(tmp_path, claude=dead_claude)
    text = path.read_text(encoding="utf-8")
    assert code == 0
    assert "# Claude unavailable: fact sheet only" in text
    assert "claude: LLMError: login expired" in text
    assert "- [Big deal closes](https://example.com/a) (Bisnow)" in text
    assert "4.20%" in text and "{{" not in text
    assert text.rstrip().endswith(FOOTER)


def test_end_to_end_dry_run_with_fakes_produces_filled_issue(tmp_path, fakes):
    code, path = run(tmp_path)
    text = path.read_text(encoding="utf-8")
    assert code == 0 and path.exists()
    assert "{{" not in text
    assert "4.20%" in text and "2026-10-06-chart.png" in text
    assert FOOTER in text
    assert (tmp_path / "issues" / "img" / "2026-10-06-chart.png").exists()


def test_delivery_failure_returns_1(tmp_path, fakes, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(main.deliver_mod, "deliver", boom)
    assert run(tmp_path)[0] == 1


def test_factsheet_failure_delivers_stub(tmp_path, fakes, monkeypatch):
    def boom(*a, **k):
        raise KeyError("lookback_hours")
    monkeypatch.setattr(main, "build_factsheet", boom)
    code, path = run(tmp_path)
    text = path.read_text(encoding="utf-8")
    assert code == 0
    assert "# Pipeline error: fact sheet unavailable" in text
    assert "factsheet: KeyError" in text and text.rstrip().endswith(FOOTER)


@pytest.mark.parametrize("which", ["write", "edit"])
def test_non_llm_error_in_draft_is_handled(tmp_path, fakes, monkeypatch, which):
    def boom(*a, **k):
        raise ValueError("odd")
    monkeypatch.setattr(main.draft, which, boom)
    code, path = run(tmp_path)
    text = path.read_text(encoding="utf-8")
    assert code == 0 and "claude: ValueError: odd" in text
    if which == "write":
        assert "# Claude unavailable: fact sheet only" in text
    else:
        assert "Big deal closes" in text and "4.20%" in text


def test_fill_and_check_failures_continue(tmp_path, fakes, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("nope")
    monkeypatch.setattr(main, "check_issue", boom)
    monkeypatch.setattr(main, "fill", boom)
    code, path = run(tmp_path)
    text = path.read_text(encoding="utf-8")
    assert code == 0 and "check: RuntimeError" in text and "fill: RuntimeError" in text
    assert "{{" not in text and FOOTER in text


def test_stray_braces_are_scrubbed(tmp_path, fakes):
    bad = DRAFT.replace("{{DGS10_CHG}}", "{{ DGS10 }}").replace(
        "{{FED_TOP}}", "{{DGS-10}}") + "\nstray }} and {{\n"
    code, path = run(tmp_path, claude=lambda p, m: bad)
    text = path.read_text(encoding="utf-8")
    assert code == 0
    assert "{{" not in text and "}}" not in text
    assert "fill: stray braces {{" not in text and "fill: stray braces" in text


def test_rerun_same_date_writes_twice_creates_one_gh_issue(tmp_path, fakes):
    calls = []

    def gh(args):
        calls.append(args)
        return "https://github.com/o/r/issues/7\n"
    code1, path = run(tmp_path, gh=gh, dry_run=False)
    first = path.read_text(encoding="utf-8")
    path.unlink()
    code2, path = run(tmp_path, gh=gh, dry_run=False)
    assert code1 == code2 == 0 and path.exists() and path.read_text(encoding="utf-8")
    assert first
    assert len([c for c in calls if c[:2] == ["issue", "create"]]) == 1


def test_connect_failure_returns_1(tmp_path, fakes, monkeypatch, capsys):
    def boom(path):
        raise OSError("locked")
    monkeypatch.setattr(main, "connect", boom)
    assert run(tmp_path)[0] == 1
    assert "pipeline error before delivery" in capsys.readouterr().err
