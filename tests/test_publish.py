import json

from src import publish

BANNER_MD = "> **Review before publishing:**\n> - {{X}} left\n\n# T\n\n## Top Stories\nText.\n"


def _write(root, day, md, with_json=True):
    d = root / "issues"
    d.mkdir(exist_ok=True)
    (d / f"{day}.md").write_text(md, encoding="utf-8", newline="")
    if with_json:
        (d / f"{day}.json").write_text("{}", encoding="utf-8")


def test_clean_md_passes():
    assert publish.check("# T\n\n## Top Stories\nText.\n") == []


def test_each_blocker():
    for line in ["[CHECK]", "[check]", "{{DGS10}}", "# Claude unavailable: fact sheet only"]:
        reasons = publish.check(f"# T\n\nok\n{line}\nmore\n")
        assert len(reasons) == 1
        assert line in reasons[0]


def test_blockers_on_crlf():
    assert len(publish.check("# T\r\n[CHECK] x\r\n")) == 1


def test_strip_banner_crlf():
    md = "> **Review before publishing:**\r\n> - x\r\n\r\n# Title\r\n"
    assert publish.strip_banner(md) == "# Title\n"


def test_strip_banner_absent_unchanged():
    assert publish.strip_banner("# A\r\n\r\ntext\r\n") == "# A\n\ntext\n"


def test_banner_problems_do_not_block(tmp_path):
    _write(tmp_path, "2026-10-05", BANNER_MD)
    assert publish.gate(tmp_path, "2026-10-05") == []


def test_gate_missing_files(tmp_path):
    reasons = publish.gate(tmp_path, "2026-10-05")
    assert "issues/2026-10-05.md not found" in reasons
    assert ("issues/2026-10-05.json not found "
            "(drafts from before 2026-10-05 cannot be published)") in reasons


def test_add_published_dedupes_and_sorts(tmp_path):
    publish.add_published(tmp_path, "2026-10-06")
    publish.add_published(tmp_path, "2026-10-05")
    out = publish.add_published(tmp_path, "2026-10-06")
    assert out == ["2026-10-05", "2026-10-06"]
    assert publish.load_published(tmp_path) == out
    assert json.loads((tmp_path / "issues" / "published.json").read_text()) == out


def test_load_published_missing(tmp_path):
    assert publish.load_published(tmp_path) == []


def test_main_bad_date_exit_2(tmp_path, capsys):
    assert publish.main(["../etc", "--root", str(tmp_path)]) == 2
    assert "not a valid date: ../etc" in capsys.readouterr().out
    assert not (tmp_path / "issues").exists()
    assert publish.main(["2026-13-40", "--root", str(tmp_path)]) == 2
    assert publish.main(["2026-1-5", "--root", str(tmp_path)]) == 2


def test_main_refusal_exit_1_and_not_recorded(tmp_path, capsys):
    _write(tmp_path, "2026-10-05", "# T\n[CHECK] verify\n")
    assert publish.main(["2026-10-05", "--root", str(tmp_path)]) == 1
    assert "[CHECK] verify" in capsys.readouterr().out
    assert publish.load_published(tmp_path) == []


def test_main_success_records(tmp_path, capsys):
    _write(tmp_path, "2026-10-05", "# T\n\nfine\n")
    assert publish.main(["2026-10-05", "--root", str(tmp_path)]) == 0
    assert "published 2026-10-05" in capsys.readouterr().out
    assert publish.load_published(tmp_path) == ["2026-10-05"]
