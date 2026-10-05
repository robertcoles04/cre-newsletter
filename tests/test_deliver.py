import json
import subprocess
from datetime import date

import pytest

from src import deliver as deliver_mod
from src.deliver import deliver, run_gh
from src.store import connect

D = date(2026, 10, 5)


class FakeGh:
    def __init__(self, fail_label=False):
        self.calls = []
        self.fail_label = fail_label

    def __call__(self, args):
        self.calls.append(args)
        if self.fail_label and "--label" in args:
            raise subprocess.CalledProcessError(
                1, ["gh", *args], stderr="could not add label: 'draft' not found")
        return "https://github.com/o/r/issues/12\n"


@pytest.fixture
def conn(tmp_path):
    return connect(str(tmp_path / "t.db"))


def _go(conn, tmp_path, gh, md="# Hi", problems=(), chart=None, term="Cap rate", dry_run=False):
    return deliver(conn, D, md, list(problems), chart, tmp_path, "weekday", term,
                   gh=gh, dry_run=dry_run)


def _row(conn):
    return conn.execute("SELECT * FROM issues WHERE date=?", (D.isoformat(),)).fetchone()


def test_writes_file_and_problems_banner(conn, tmp_path):
    chart = tmp_path / "c.png"
    chart.write_bytes(b"png")
    gh = FakeGh()
    path = _go(conn, tmp_path, gh, problems=["bad number", "no link"], chart=chart)
    assert path == tmp_path / "issues" / "2026-10-05.md"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("> **Review before publishing:**")
    assert "bad number" in text and "no link" in text and "# Hi" in text
    assert (tmp_path / "issues" / "img" / "2026-10-05-chart.png").read_bytes() == b"png"
    row = _row(conn)
    assert row["status"] == "draft" and row["day_type"] == "weekday"
    assert json.loads(row["source_problems"]) == ["bad number", "no link"]
    assert row["term"] == "Cap rate" and row["gh_issue"] == 12
    args = gh.calls[0]
    assert args[:2] == ["issue", "create"]
    assert "Draft: CRE Blurb 2026-10-05" in args and "--label" in args


def test_no_problems_no_banner(conn, tmp_path):
    path = _go(conn, tmp_path, FakeGh())
    assert "Review before publishing" not in path.read_text(encoding="utf-8")


def test_second_run_same_date_does_not_open_second_issue(conn, tmp_path):
    gh = FakeGh()
    _go(conn, tmp_path, gh)
    _go(conn, tmp_path, gh, md="# Updated", problems=["x"], term="NOI")
    assert len(gh.calls) == 1
    row = _row(conn)
    assert row["gh_issue"] == 12 and row["term"] == "NOI"
    assert "# Updated" in row["markdown"] and json.loads(row["source_problems"]) == ["x"]


def test_dry_run_never_calls_gh(conn, tmp_path):
    gh = FakeGh()
    path = _go(conn, tmp_path, gh, dry_run=True)
    assert gh.calls == [] and path.exists()
    assert _row(conn)["gh_issue"] is None


def test_label_failure_retries_without_label(conn, tmp_path):
    gh = FakeGh(fail_label=True)
    _go(conn, tmp_path, gh)
    assert len(gh.calls) == 2
    assert "--label" in gh.calls[0] and "--label" not in gh.calls[1]
    assert _row(conn)["gh_issue"] == 12


def test_non_label_failure_propagates(conn, tmp_path):
    def boom(args):
        raise subprocess.CalledProcessError(1, ["gh"], stderr="auth required")
    with pytest.raises(subprocess.CalledProcessError):
        _go(conn, tmp_path, boom)


def test_run_gh_wraps_subprocess(monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"], seen["kw"] = cmd, kw
        return subprocess.CompletedProcess(cmd, 0, stdout="out", stderr="")
    monkeypatch.setattr(deliver_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(deliver_mod.shutil, "which", lambda n: "C:/gh.exe")
    assert run_gh(["issue", "list"]) == "out"
    assert seen["cmd"] == ["C:/gh.exe", "issue", "list"]
    assert seen["kw"] == dict(capture_output=True, text=True, encoding="utf-8", check=True)


def test_html_written_next_to_md(conn, tmp_path):
    deliver(conn, D, "# Hi", [], None, tmp_path, "weekday", None, gh=FakeGh(),
            dry_run=True, html="<p>hi</p>")
    assert (tmp_path / "issues" / f"{D.isoformat()}.html").read_text(encoding="utf-8") == "<p>hi</p>"


def test_factsheet_json_written(conn, tmp_path):
    fs = {"date": D.isoformat(), "day_type": "weekday", "values": {"DGS10": "4.12%"},
          "term": {"term": "Cap rate"}, "stories": ["x"]}
    deliver(conn, D, "# Hi", [], None, tmp_path, "weekday", "Cap rate",
            gh=lambda a: "", dry_run=True, factsheet=fs)
    data = json.loads((tmp_path / "issues" / f"{D.isoformat()}.json").read_text(encoding="utf-8"))
    assert data == {"date": D.isoformat(), "day_type": "weekday", "values": {"DGS10": "4.12%"}}


def test_no_factsheet_no_json(conn, tmp_path):
    _go(conn, tmp_path, lambda a: "", dry_run=True)
    assert not (tmp_path / "issues" / f"{D.isoformat()}.json").exists()


def test_refuses_to_overwrite_published_date(conn, tmp_path):
    issues = tmp_path / "issues"
    issues.mkdir()
    (issues / "published.json").write_text(json.dumps([D.isoformat()]), encoding="utf-8")
    (issues / f"{D.isoformat()}.md").write_text("ORIGINAL", encoding="utf-8")
    gh = FakeGh()
    with pytest.raises(RuntimeError, match="already published"):
        deliver(conn, D, "# New\n", [], None, tmp_path, "weekday", None, gh=gh,
                html="<p>x</p>", factsheet={"date": "d", "day_type": "w", "values": {}})
    assert (issues / f"{D.isoformat()}.md").read_text(encoding="utf-8") == "ORIGINAL"
    assert not (issues / f"{D.isoformat()}.html").exists()
    assert not (issues / f"{D.isoformat()}.json").exists()
    assert gh.calls == []
