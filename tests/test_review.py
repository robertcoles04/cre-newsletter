import json
from datetime import date

from src import llm, publish, review
from src.checks import FOOTER

D = date(2026, 10, 7)
EM, EN = chr(0x2014), chr(0x2013)

MD = f"""## The Brief

- Blackstone bought a Dallas warehouse portfolio [Bisnow](https://example.com/a)

## The Numbers

- **10Y Treasury:** {{{{DGS10}}}} ({{{{DGS10_CHG}}}})

## Top Stories

### Blackstone buys Dallas warehouses for $500 million

The deal covers 12 buildings. It is the biggest Texas industrial sale this year.

**Why it matters:** warehouse prices have bottomed nationwide.

[Bisnow](https://example.com/a)

{FOOTER}
"""

SHEET = {"date": D.isoformat(), "day_type": "weekday", "values": {"DGS10": "4.10%"},
         "top": [{"title": "Blackstone buys Dallas warehouses for $500 million",
                  "summary": "12 buildings", "url": "https://example.com/a"}]}


def reply(verdict="approve", fixes=(), reasons=()):
    return json.dumps({"verdict": verdict, "fixes": list(fixes), "reasons": list(reasons)})


def runner(text, seen=None):
    def run(prompt, model):
        if seen is not None:
            seen.append(prompt)
        return text
    return run


def go(text, tmp_path, md=MD):
    problems = []
    out = review.run_review(md, SHEET, tmp_path, D, runner(text), problems)
    return out, problems


# --- run_review ---------------------------------------------------------------

def test_approve_passes_through(tmp_path):
    out, problems = go(reply(), tmp_path)
    assert out == MD and problems == []


def test_prompt_has_draft_sources_and_recent_issues(tmp_path):
    (tmp_path / "issues").mkdir()
    (tmp_path / "issues" / "2026-10-06.md").write_text("## The Brief\n\nYesterday's issue\n",
                                                       encoding="utf-8")
    seen = []
    review.run_review(MD, SHEET, tmp_path, D, runner(reply(), seen), [])
    prompt = seen[0]
    assert "{{DGS10}}" in prompt and "Yesterday's issue" in prompt
    assert "https://example.com/a" in prompt and "4.10%" not in prompt  # no numbers
    assert EM not in prompt.split("# Draft")[0] and EN not in prompt.split("# Draft")[0]


def test_fixes_applied_with_problem_lines(tmp_path):
    fixes = [{"find": "It is the biggest Texas industrial sale this year.", "replace": "",
              "why": "not in the source"},
             {"find": "warehouse prices have bottomed nationwide.",
              "replace": "buyers are paying up for Dallas warehouses.",
              "why": "overclaims the source"}]
    out, problems = go(reply("approve_with_fixes", fixes), tmp_path)
    assert "biggest Texas" not in out and "bottomed" not in out
    assert "buyers are paying up for Dallas warehouses." in out
    assert "\n\n\n" not in out
    assert problems == ["editor: fixed: not in the source", "editor: fixed: overclaims the source"]


def test_cut_of_whole_paragraph_collapses_blank_lines(tmp_path):
    fix = {"find": "**Why it matters:** warehouse prices have bottomed nationwide.",
           "replace": "", "why": "unsupported"}
    out, _ = go(reply("approve_with_fixes", [fix]), tmp_path)
    assert "Why it matters" not in out and "\n\n\n" not in out


def _rejected(find, replace, md=MD):
    out, applied, rejected = review.apply_fixes(md, [{"find": find, "replace": replace,
                                                      "why": "x"}])
    assert out == md and applied == []
    return rejected[0][1]


def test_rejects_new_number():
    assert _rejected("The deal covers 12 buildings.",
                     "The deal covers 14 buildings.") == "adds a number"
    assert _rejected("It is the biggest Texas industrial sale this year.",
                     "It is the biggest sale since 2019.") == "adds a number"


def test_number_kept_is_fine():
    out, applied, _ = review.apply_fixes(MD, [{"find": "The deal covers 12 buildings.",
                                               "replace": "It covers 12 buildings.",
                                               "why": "x"}])
    assert applied and "It covers 12 buildings." in out


def test_rejects_new_link():
    assert _rejected("warehouse prices have bottomed nationwide.",
                     "see [this](https://evil.example/x).") == "adds a link"


def test_rejects_dropped_or_added_placeholder():
    assert _rejected("{{DGS10}} ({{DGS10_CHG}})", "{{DGS10}}") == "changes a placeholder"
    assert _rejected("- **10Y Treasury:** {{DGS10}} ({{DGS10_CHG}})", "") \
        == "changes a placeholder"
    assert _rejected("The deal covers 12 buildings.",
                     "The deal covers 12 buildings at {{DGS10}}.") == "changes a placeholder"


def test_rejects_ambiguous_or_missing_find():
    assert _rejected("[Bisnow](https://example.com/a)", "") == "text found 2 times"
    assert _rejected("not in the draft", "x") == "text not found"
    assert _rejected("", "x") == "empty find"


def test_rejects_heading_and_footer():
    assert _rejected("### Blackstone buys Dallas warehouses for $500 million",
                     "### Blackstone buys warehouses") == "touches a heading"
    assert _rejected(FOOTER, "") == "touches the footer"


def test_rejects_dashes():
    assert _rejected("The deal covers 12 buildings.",
                     f"The deal {EM} 12 buildings.") == "uses a dash"
    assert _rejected("The deal covers 12 buildings.",
                     f"The deal {EN} 12 buildings.") == "uses a dash"


def test_rejected_fix_is_reported(tmp_path):
    fix = {"find": "The deal covers 12 buildings.", "replace": "The deal covers 14 buildings.",
           "why": "wrong count"}
    out, problems = go(reply("approve_with_fixes", [fix]), tmp_path)
    assert out == MD
    assert problems == ["editor: fix not applied (adds a number): The deal covers 12 buildings."]


def test_hold_inserts_check_line_and_gate_blocks(tmp_path):
    md = MD.replace("{{DGS10}} ({{DGS10_CHG}})", "4.10% (+2 bps)")  # as after fill
    assert publish.check(md) == []
    fix = {"find": "It is the biggest Texas industrial sale this year.", "replace": "",
           "why": "not in the source"}
    out, problems = go(reply("hold", [fix], ["main story misread", "repeats yesterday"]),
                       tmp_path, md=md)
    assert out.startswith("[CHECK] Editor hold: main story misread; repeats yesterday\n\n")
    assert "biggest Texas" not in out  # valid fixes still applied
    assert problems == ["editor: hold: main story misread", "editor: hold: repeats yesterday",
                        "editor: fixed: not in the source"]
    reasons = publish.check(out)
    assert reasons and "[CHECK]" in reasons[0]
    # The owner releases the hold by deleting that line.
    released = out.split("\n", 2)[2]
    assert publish.check(released) == []


def test_hold_reason_is_sanitized(tmp_path):
    out, problems = go(reply("hold", [], [f"misread {EM} see {{{{DGS10}}}} <b>"]), tmp_path)
    first = out.split("\n", 1)[0]
    assert EM not in first and "{" not in first and "<" not in first
    assert EM not in problems[0]


def test_bad_json_means_review_unavailable(tmp_path):
    out, problems = go("## The Brief\n\nI think it looks fine.", tmp_path)
    assert out == MD
    assert len(problems) == 1 and problems[0].startswith("editor: review unavailable (")
    out, problems = go('{"verdict": "maybe"}', tmp_path)
    assert out == MD and problems[0].startswith("editor: review unavailable")


def test_llm_exception_means_review_unavailable(tmp_path):
    def dead(prompt, model):
        raise llm.LLMError("login expired")
    problems = []
    out = review.run_review(MD, SHEET, tmp_path, D, dead, problems)
    assert out == MD and problems == ["editor: review unavailable (login expired)"]


def test_parse_tolerates_prose_and_fences():
    text = 'Here you go:\n```json\n' + reply("approve_with_fixes",
                                            [{"find": "a", "replace": "b", "why": "c"},
                                             {"find": 3}]) + "\n```"
    parsed = review._parse(text)
    assert parsed["verdict"] == "approve_with_fixes"
    assert parsed["fixes"] == [{"find": "a", "replace": "b", "why": "c"}]


# --- recent_issues ------------------------------------------------------------

def test_recent_issues_last_three_before_date_banner_stripped(tmp_path):
    issues = tmp_path / "issues"
    issues.mkdir()
    for day in ("2026-10-02", "2026-10-03", "2026-10-04", "2026-10-06", "2026-10-07",
                "2026-10-08"):
        issues.joinpath(f"{day}.md").write_text(
            f"> **Review before publishing:**\n> - feed: down\n\n## The Brief\n\nIssue {day}\n",
            encoding="utf-8")
    issues.joinpath("published.json").write_text("{}", encoding="utf-8")
    issues.joinpath("2026-10-05.html").write_text("<p>x</p>", encoding="utf-8")
    got = review.recent_issues(tmp_path, D)
    assert [g.split("Issue ")[1] for g in got] == ["2026-10-06", "2026-10-04", "2026-10-03"]
    assert all("Review before publishing" not in g for g in got)
    assert review.recent_issues(tmp_path, "2026-10-07") == got


def test_recent_issues_truncated_and_missing_dir(tmp_path):
    assert review.recent_issues(tmp_path, D) == []
    (tmp_path / "issues").mkdir()
    (tmp_path / "issues" / "2026-10-06.md").write_text("x" * 20000, encoding="utf-8")
    assert len(review.recent_issues(tmp_path, D)[0]) == review.RECENT_CHARS
