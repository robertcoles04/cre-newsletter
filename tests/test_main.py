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
    monkeypatch.setattr(main, "sleep", lambda s: None)
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


def test_gate_skips_before_5am_and_after_5pm(tmp_path, fakes):
    for hour in (4, 18):
        code, path = run(tmp_path, now=datetime(2026, 10, 6, hour, 0, tzinfo=ET))
        assert code == 0 and not path.exists()


def test_gate_bypassed_by_force_and_date(tmp_path, fakes):
    code, path = run(tmp_path, now=datetime(2026, 10, 6, 9, 0, tzinfo=ET), force=True)
    assert code == 0 and path.exists()


def test_gate_runs_when_github_starts_the_cron_late(tmp_path, fakes):
    # GitHub often starts scheduled runs hours late; a 9 AM or 5 PM start still drafts.
    for hour in (9, 17):
        code, path = run(tmp_path, now=datetime(2026, 10, 6, hour, 7, tzinfo=ET))
        assert code == 0 and path.exists()
        path.unlink()


def test_already_delivered_date_skips_without_collecting(tmp_path, fakes, monkeypatch, capsys):
    run(tmp_path, dry_run=False)  # delivers, sets gh_issue
    capsys.readouterr()

    def boom(*a, **k):
        raise AssertionError("collector called")
    fake_rates, fake_feed = main.collect_rates, main.rss.fetch_feed
    monkeypatch.setattr(main, "collect_rates", boom)
    monkeypatch.setattr(main.rss, "fetch_feed", boom)
    code, _ = run(tmp_path, dry_run=False)
    assert code == 0
    assert "already delivered 2026-10-06; skipping" in capsys.readouterr().out
    # --date alone still skips; --force bypasses
    code, _ = run(tmp_path, dry_run=False, date="2026-10-06")
    assert code == 0 and "already delivered" in capsys.readouterr().out
    # restore the fakes (not monkeypatch.undo(), which would also drop the
    # network fakes and hit the real APIs)
    monkeypatch.setattr(main, "collect_rates", fake_rates)
    monkeypatch.setattr(main.rss, "fetch_feed", fake_feed)
    code, path = run(tmp_path, dry_run=False, force=True)
    assert code == 0 and path.exists()


def test_missing_footer_is_appended(tmp_path, fakes):
    code, path = run(tmp_path, claude=lambda p, m: DRAFT.replace(FOOTER, "").rstrip() + "\n")
    text = path.read_text(encoding="utf-8")
    assert code == 0 and text.rstrip().endswith(FOOTER)
    assert "check/footer" in text  # signal kept


def test_no_chart_removes_image_line(tmp_path, fakes, monkeypatch):
    monkeypatch.setattr(main, "_make_chart", lambda *a, **k: None)
    code, path = run(tmp_path)
    text = path.read_text(encoding="utf-8")
    assert code == 0 and "Chart of the Day" not in text and "-chart.png" not in text


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
    code2, path = run(tmp_path, gh=gh, dry_run=False, force=True)
    assert code1 == code2 == 0 and path.exists() and path.read_text(encoding="utf-8")
    assert first
    assert len([c for c in calls if c[:2] == ["issue", "create"]]) == 1


def test_connect_failure_returns_1(tmp_path, fakes, monkeypatch, capsys):
    def boom(path):
        raise OSError("locked")
    monkeypatch.setattr(main, "connect", boom)
    assert run(tmp_path)[0] == 1
    assert "pipeline error before delivery" in capsys.readouterr().err


def test_dry_run_writes_html_preview(tmp_path, fakes):
    code, path = run(tmp_path)
    html_path = path.with_suffix(".html")
    assert code == 0 and html_path.exists()
    html = html_path.read_text(encoding="utf-8")
    assert "The Numbers" in html and "4.20%" in html
    # chart is embedded so the preview works when pasted, emailed or opened alone
    assert 'src="data:image/png;base64,' in html and FOOTER in html


def test_dry_run_writes_factsheet_json(tmp_path, fakes):
    import json
    code, path = run(tmp_path)
    data = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    assert code == 0 and set(data) == {"date", "day_type", "values", "charts", "as_of_dates"}
    assert data["charts"]["chart"]["alt"].startswith("Line chart of the 10-Year Treasury yield, from ")


def test_fallback_and_stub_write_html(tmp_path, fakes, monkeypatch):
    code, path = run(tmp_path, claude=dead_claude)
    assert path.with_suffix(".html").exists()
    monkeypatch.setattr(main, "build_factsheet", lambda *a, **k: (_ for _ in ()).throw(KeyError("x")))
    code, path = run(tmp_path, force=True)
    html = path.with_suffix(".html").read_text(encoding="utf-8")
    assert "Pipeline error" in html and 'id="numbers"' not in html


def test_fallback_uses_friendly_labels():
    fs = {"day_type": "weekday", "values": {
        "DGS10": "4.2%", "DGS10_CHG": "+1 bps", "SOFR": "3.9%", "SOFR_CHG": "unch",
        "FED_TOP": "No change 80%", "FED_HOLD": "80.0%", "REITW_BEST_1": "O +2.0%"}}
    md = main.fallback_markdown(fs)
    assert "**10-Year Treasury:** {{DGS10}} ({{DGS10_CHG}})" in md
    assert "**SOFR:**" in md and "**Odds of a hold:** {{FED_HOLD}}" in md
    assert "DGS10:**" not in md and "REITW" not in md
    sunday = main.fallback_markdown({**fs, "day_type": "sunday"})
    assert "{{REITW_BEST_1}}" in sunday


def test_story_lines_escape_feed_html():
    from src.main import _story_lines
    line = _story_lines([{"title": "<script>x</script> A&B", "url": "https://e.com",
                          "source": "Feed"}])[0]
    assert "<script>" not in line
    assert "&lt;script&gt;x&lt;/script&gt; A&amp;B" in line


def test_story_lines_drop_non_http_urls():
    from src.main import _story_lines
    lines = _story_lines([
        {"title": "Good", "url": "https://e.com/a", "source": "F"},
        {"title": "Plain", "url": "http://e.com/b", "source": "F"},
        {"title": "Bad", "url": "javascript:alert(1)", "source": "F"},
        {"title": "Rel", "url": "/x", "source": "F"}])
    assert [l.split("]")[0] for l in lines] == ["- [Good", "- [Plain"]


def test_delivery_writes_issue_number_step_output(tmp_path, fakes, monkeypatch):
    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    code, _ = run(tmp_path, gh=lambda a: "https://github.com/o/r/issues/42\n", dry_run=False)
    assert code == 0
    assert out.read_text(encoding="utf-8").splitlines() == ["date=2026-10-06", "issue=42"]


def test_dry_run_and_skips_write_no_step_output(tmp_path, fakes, monkeypatch):
    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    code, _ = run(tmp_path)  # dry run: no GitHub Issue, so nothing to auto-publish
    assert code == 0 and not out.exists()
    code, _ = run(tmp_path, gh=lambda a: "https://github.com/o/r/issues/42\n", dry_run=False)
    out.unlink()
    code, _ = run(tmp_path, dry_run=False)  # already delivered: skipped, no dispatch
    assert code == 0 and not out.exists()


def test_step_output_noop_outside_actions(monkeypatch):
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    main._step_output(issue=1)  # must not raise


def _editor_claude(verdict, seen=None, **extra):
    import json

    def claude(prompt, model):
        if "You are the fact-check editor" in prompt:
            if seen is not None:
                seen.append(prompt)
            return json.dumps({"verdict": verdict, "fixes": extra.get("fixes", []),
                               "reasons": extra.get("reasons", [])})
        return DRAFT
    return claude


def test_editor_review_runs_before_fill_and_approve_adds_nothing(tmp_path, fakes):
    seen = []
    code, path = run(tmp_path, claude=_editor_claude("approve", seen))
    text = path.read_text(encoding="utf-8")
    assert code == 0 and len(seen) == 1
    assert "{{DGS10}}" in seen[0]  # the editor sees placeholders, not numbers
    assert "editor:" not in text and "[CHECK]" not in text


def test_editor_hold_blocks_the_publish_gate(tmp_path, fakes):
    from src import publish
    fix = {"find": "Two sentences here. Short ones.", "replace": "Two sentences here.",
           "why": "trimmed"}
    code, path = run(tmp_path, claude=_editor_claude("hold", fixes=[fix],
                                                     reasons=["main story misread"]))
    text = path.read_text(encoding="utf-8")
    assert code == 0
    assert "editor: hold: main story misread" in text and "editor: fixed: trimmed" in text
    body = publish.strip_banner(text)
    assert body.startswith("[CHECK] Editor hold: main story misread\n")
    assert "Short ones." not in body
    assert any("[CHECK]" in r for r in publish.check(body))


def test_unparseable_editor_reply_only_adds_a_problem(tmp_path, fakes):
    code, path = run(tmp_path)  # good_claude answers every prompt with markdown
    text = path.read_text(encoding="utf-8")
    assert code == 0 and "editor: review unavailable" in text and "[CHECK]" not in text
