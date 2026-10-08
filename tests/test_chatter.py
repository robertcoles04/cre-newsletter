from src.cleanup import strip_chatter
from src.publish import check

FOOT = "For informational purposes only. Not investment advice."


def test_strip_chatter_drops_preamble_and_trailing_notes():
    md = ("For informational purposes only, here is the edited issue. I reviewed the draft.\n\n"
          "## The Brief\n\n- One. [S](https://x.com/1)\n\n" + FOOT + "\n\nLet me know if you want changes.\n")
    out = strip_chatter(md)
    assert out.startswith("## The Brief")
    assert out.rstrip().endswith(FOOT)
    assert "I reviewed" not in out and "Let me know" not in out


def test_strip_chatter_keeps_fallback_heading_and_clean_issue():
    md = "# Claude unavailable: fact sheet only\n\n## The Numbers\n\n- x\n"
    assert strip_chatter(md) == md


def test_gate_blocks_editor_commentary():
    md = "## The Brief\n\nHere is the edited issue. I reviewed it against the voice rules.\n"
    assert any("notes to the editor" in r for r in check(md))


def test_gate_allows_reader_first_person_and_banner_notes():
    md = ("> - check/long_sentence: It was already close; the only changes I...\n\n"
          "## Coffee chat\n\n- I'd point out that New York has a maturity wall. [S](https://x.com/1)\n")
    assert check(md) == []
