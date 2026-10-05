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


def test_banner_mid_document_not_stripped():
    md = "# T\n\n> **Review before publishing:**\n> - {{X}}\n\ntext\n"
    assert publish.strip_banner(md) == md
    assert len(publish.check(publish.strip_banner(md))) == 1


def test_banner_after_leading_blank_lines_stripped():
    md = "\n\n> **Review before publishing:**\n> - x\n\n# T\n"
    assert publish.strip_banner(md) == "# T\n"


def test_bom_does_not_hide_fallback_or_banner():
    bom = "\ufeff"
    assert len(publish.check(bom + "# Claude unavailable: fact sheet only\n")) == 1
    assert publish.strip_banner(bom + "> **Review before publishing:**\n> - x\n\n# T\n") == "# T\n"


def test_fallback_heading_variant_with_extra_spaces():
    assert len(publish.check("#  Claude  unavailable:  fact sheet only\n")) == 1


def test_gate_bom_file_blocks(tmp_path):
    d = tmp_path / "issues"
    d.mkdir()
    (d / "2026-10-05.md").write_bytes(b"\xef\xbb\xbf# Claude unavailable: fact sheet only\n")
    (d / "2026-10-05.json").write_text("{}")
    assert len(publish.gate(tmp_path, "2026-10-05")) == 1


def test_main_trailing_newline_date_rejected(tmp_path):
    assert publish.main(["2026-10-05\n", "--root", str(tmp_path)]) == 2
    assert not (tmp_path / "issues").exists()


def test_raw_html_and_unsafe_links_blocked():
    bad = ["<script>alert(1)</script>",
           'See <img src=x onerror="alert(1)"> here',
           "Click [here](javascript:alert(1))",
           "Click [here]( JavaScript:alert(1))",
           "![chart](DATA:image/png;base64,AAAA)",
           "[x](vbscript:msgbox)",
           "</div>"]
    for line in bad:
        reasons = publish.check(f"# T\n\nok\n{line}\nmore\n")
        assert len(reasons) == 1, (line, reasons)
        assert line.strip()[:40] in reasons[0]


def test_autolinks_comments_and_plain_text_allowed():
    md = ("# T\n\n<!-- a template note\nspanning lines -->\n"
          "Read it at <https://example.com/a?b=1>.\n"
          "Rates < 5% and spreads > 100 bps; A&B Realty.\n"
          "**What it means:** <!-- one sentence --> Costs rise.\n"
          "[source](https://example.com) ![Chart of the Day](img/x.png)\n")
    assert publish.check(md) == []


def test_html_inside_comment_ignored_but_after_comment_blocked():
    assert publish.check("<!-- <script> -->\nok\n") == []
    assert len(publish.check("<!-- note --> <b>bold</b>\n")) == 1
