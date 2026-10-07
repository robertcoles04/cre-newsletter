"""Debt Markets / Market Watch balance safety net (src/balance.py). No real Claude."""

from pathlib import Path

import pytest

from src import balance
from src.balance import _heights, _section, balance_debt, estimate_height

ROOT = Path(__file__).resolve().parent.parent
SPARE = {"title": "Lender X closes a loan", "source": "Trepp", "url": "https://trepp.example/x",
         "summary": "A lender closed a refinancing."}
SHEET = {"debt": [SPARE]}
GOOD = (f"A lender closed a refinancing on an office tower in Denver. The new loan replaces "
        f"debt that came due this year, says [Trepp]({SPARE['url']}).\n\n"
        "**Why it matters:** lenders still refinance good buildings, so owners with strong "
        "tenants can avoid a forced sale.")


def para(n: int, tag: str) -> str:
    return " ".join(["word"] * (n - 1) + [f"[{tag}](https://example.com/{tag})."])


def debt_items(n: int) -> str:
    sep = "\n\n"
    return sep.join(f"{para(40, f'd{i}')}{sep}**Why it matters:** {para(20, f'z{i}')}"
                    for i in range(n))


def issue(debt_words: int, distress: bool = True) -> str:
    watch = "\n\n".join(f"### Region {i}\n\n{para(40, f'w{i}')}\n\n**Why it matters:** {para(20, f'y{i}')}"
                        for i in range(3))
    dis = f"\n\n**Distress Watch:** {para(25, 'dis')}" if distress else ""
    return (f"## The Brief\n\n- One [a](https://example.com/a)\n\n"
            f"## Debt Markets\n\n{para(debt_words, 'd0') if debt_words else debt_items(3)}{dis}\n\n"
            f"## Top Stories\n\nText.\n\n## Market Watch\n\n{watch}\n\n## Quick Hits\n\n- x\n")


class Fake:
    def __init__(self, outputs):
        self.outputs, self.calls = list(outputs), []

    def __call__(self, prompt, model):
        self.calls.append((prompt, model))
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


def debt_text(md):
    a, b = _section(md, "Debt Markets")
    return md[a:b]


def test_balanced_issue_untouched():
    md = issue(0)  # three full items, like Market Watch
    run, problems = Fake([GOOD]), []
    h = _heights(md)
    assert h[0] >= 0.8 * h[1]
    assert balance_debt(md, SHEET, run, problems) == md
    assert run.calls == [] and problems == []


def test_missing_section_returns_unchanged():
    md = issue(20).replace("## Market Watch", "## Something Else")
    run, problems = Fake([GOOD]), []
    assert balance_debt(md, SHEET, run, problems) == md
    assert run.calls == [] and problems == []


def test_short_debt_gets_item_before_distress_watch():
    md = issue(20)
    run, problems = Fake([f"```\n{GOOD}\n```"]), []
    out = balance_debt(md, SHEET, run, problems, max_adds=1)
    d = debt_text(out)
    assert GOOD in d
    assert d.index(GOOD) < d.index("**Distress Watch:**")
    assert d.index("Distress Watch:**") > d.index("Why it matters")
    assert problems == []
    prompt, model = run.calls[0]
    assert SPARE["url"] in prompt and model == balance.MODEL_WRITE
    assert "```" not in out
    # everything outside Debt Markets is unchanged
    assert out.split("## Top Stories")[1] == md.split("## Top Stories")[1]


def test_item_appended_at_end_without_distress_watch():
    md = issue(20, distress=False)
    out = balance_debt(md, SHEET, Fake([GOOD]), [], max_adds=1)
    d = debt_text(out).rstrip()
    assert d.endswith("avoid a forced sale.")
    assert "## Top Stories" in out and out.index(GOOD) < out.index("## Top Stories")


def test_stops_once_balanced_and_respects_max_adds():
    second = {"title": "t2", "source": "Bisnow", "url": "https://bisnow.example/2", "summary": ""}
    good2 = GOOD.replace(SPARE["url"], second["url"]).replace("Trepp", "Bisnow")
    sheet = {"debt": [SPARE, second]}
    run = Fake([GOOD, good2])
    out = balance_debt(issue(5, distress=False), sheet, run, [], max_adds=1)
    assert len(run.calls) == 1 and SPARE["url"] in out and second["url"] not in out
    run = Fake([GOOD, good2])
    out = balance_debt(issue(5, distress=False), sheet, run, [], max_adds=2)
    assert len(run.calls) == 2 and second["url"] in out  # the second spare story is next


def test_no_spare_story_adds_problem_line():
    md = issue(20)
    used = {"debt": [{**SPARE, "url": "https://example.com/d0"}]}  # already in md
    run, problems = Fake([GOOD]), []
    assert balance_debt(md, used, run, problems) == md
    assert problems == ["layout: Debt Markets is much shorter than Market Watch and no "
                        "spare debt story was available"]
    assert run.calls == []
    assert balance_debt(md, {"debt": []}, run, []) == md


@pytest.mark.parametrize("bad", [
    GOOD.replace(SPARE["url"], "https://other.example"),   # wrong link
    "## Debt\n\n" + GOOD,                                    # heading
    GOOD.replace("A lender", "A {{VNQ}} lender"),            # placeholder
    GOOD.replace("avoid a forced sale.", f"avoid a forced sale {chr(0x2014)} for now."),  # em dash
    GOOD.replace("lenders still", f"lenders {chr(0x2013)} still"),  # en dash
    GOOD.split("\n\n")[0],                                   # no Why it matters line
])
def test_invalid_output_rejected(bad):
    md = issue(20)
    problems = []
    assert balance_debt(md, SHEET, Fake([bad]), problems) == md
    assert len(problems) == 1 and problems[0].startswith("layout:")


def test_llm_exception_handled():
    md = issue(20)
    problems = []
    assert balance_debt(md, SHEET, Fake([RuntimeError("claude timed out")]), problems) == md
    assert problems == ["layout: debt balance skipped (claude timed out)"]


def test_estimate_height_basics():
    assert estimate_height("") == 0
    one = estimate_height("word " * 18)  # 18 words = 2 lines + 1.2 spacing
    assert one == pytest.approx(3.2)
    assert estimate_height("### Heading") == pytest.approx(2.5)
    # link URLs are not counted as words
    assert estimate_height("[hi](https://example.com/" + "a" * 300 + ")") == pytest.approx(2.2)


def test_estimate_height_shows_2026_10_07_imbalance():
    md = (ROOT / "issues" / "2026-10-07.md").read_text(encoding="utf8")
    a, b = _section(md, "Debt Markets")
    c, d = _section(md, "Market Watch")
    debt, watch = estimate_height(md[a:b]), estimate_height(md[c:d])
    assert debt < 0.8 * watch  # the imbalance the screenshot showed
    debt_w, watch_w = _heights(md)
    assert debt_w < 0.6 * watch_w  # with the real column widths: about half
    # the balance step would have fired on that issue
    problems = []
    run = Fake([GOOD])
    balance_debt(md, SHEET, run, problems, max_adds=1)
    assert len(run.calls) == 1
