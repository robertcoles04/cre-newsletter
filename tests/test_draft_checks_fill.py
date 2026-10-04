import re
from pathlib import Path

from src import draft
from src.checks import FOOTER, check_issue, load_banned
from src.fill import fill

ROOT = Path(__file__).resolve().parent.parent
BANNED = ["delve", "game-changer", "robust"]
REQUIRED = "{{DGS10}} {{DGS10_CHG}} {{SOFR}} {{FED_TOP}} {{VNQ}}"


def story(n, url=None, title=None, summary="sum"):
    return {"id": n, "title": title or f"Title {n}", "source": "Src",
            "url": url or f"https://x.com/{n}", "summary": summary, "also_covered": []}


def sheet(day="weekday", top=(), quick=(), **extra):
    fs = {
        "date": "2026-10-06", "day_type": day, "problems": [],
        "values": {"DGS10": "4.62%", "DGS10_CHG": "+3 bps", "SOFR": "4.31%",
                   "FED_TOP": "Hold 81.0%", "VNQ": "$91.20"},
        "top": list(top), "quick_hits": list(quick), "debt": [], "ai": [],
        "term": {"term": "Cap rate", "definition_hint": "NOI / price"},
    }
    fs.update(extra)
    return fs


def issue(body, numbers=REQUIRED):
    return f"## The Numbers\n\n{numbers}\n\n{body}\n\n{FOOTER}\n"


def kinds(problems):
    return [p["kind"] for p in problems]


# --- checks: numbers -------------------------------------------------------

def test_model_number_flagged():
    md = issue("", numbers=f"{REQUIRED}\n\nThe 10Y rose to 4.6% today.")
    assert "model_number" in kinds(check_issue(md, sheet(), BANNED))


def test_placeholder_numbers_not_flagged():
    md = issue("", numbers=f"{REQUIRED}\n\nThe 10Y is {{{{DGS10}}}} ({{{{DGS10_CHG}}}}).")
    assert check_issue(md, sheet(), BANNED) == []


def test_dollar_and_bps_in_numbers_section_flagged():
    md = issue("", numbers=f"{REQUIRED}\n\nSpreads widened 12 bps and VNQ hit $90.")
    found = [p for p in check_issue(md, sheet(), BANNED) if p["kind"] == "model_number"]
    assert len(found) == 2


def test_numbers_in_urls_not_flagged():
    md = issue("", numbers=f"{REQUIRED}\n\nSee [chart](https://x.com/4.6%25-$5).")
    assert check_issue(md, sheet(), BANNED) == []


def test_unsourced_number_in_story_section_flagged():
    top = [story(1, summary="Blackstone paid $120 million for a warehouse.")]
    md = issue("## Top Stories\n\nBlackstone paid $95 million, a 5.5% cap rate. "
               "[Src](https://x.com/1)")
    ps = check_issue(md, sheet(top=top), BANNED)
    unsourced = [p for p in ps if p["kind"] == "unsourced_number"]
    assert len(unsourced) == 2
    assert "model_number" not in kinds(ps)


def test_sourced_number_in_story_section_ok():
    top = [story(1, summary="Blackstone paid $120 Million at a 5.5%  cap rate.")]
    md = issue("## Top Stories\n\nBlackstone paid $120 million at a 5.5% cap rate. "
               "[Src](https://x.com/1)")
    assert check_issue(md, sheet(top=top), BANNED) == []


def test_sourced_number_must_not_be_a_partial_match():
    top = [story(1, summary="Vacancy hit 14.6% in Q3.")]
    md = issue("## Top Stories\n\nVacancy hit 4.6%. [Src](https://x.com/1)")
    assert "unsourced_number" in kinds(check_issue(md, sheet(top=top), BANNED))


def test_term_of_the_day_numbers_ignored():
    md = issue("## Term of the Day\n\nA $10 million building with $500,000 of NOI "
               "trades at a 5% cap rate.")
    assert check_issue(md, sheet(), BANNED) == []


def test_reit_weekly_number_is_model_number():
    md = issue("## REIT Weekly\n\nO fell 3% this week.")
    assert "model_number" in kinds(check_issue(md, sheet(day="sunday"), BANNED))


# --- checks: slop, structure ----------------------------------------------

def test_missing_link_flagged():
    md = issue("## Top Stories\n\nA deal closed. [Src](https://x.com/1)")
    ps = check_issue(md, sheet(top=[story(1)], quick=[story(2)]), BANNED)
    assert {"kind": "missing_link", "detail": "https://x.com/2"} in ps
    assert kinds(ps).count("missing_link") == 1


def test_banned_phrase_flagged():
    md = issue("## Top Stories\n\nLet us DELVE into it.")
    assert "banned" in kinds(check_issue(md, sheet(), BANNED))


def test_banned_phrases_file_loaded():
    phrases = load_banned()
    assert "delve" in phrases and "seamless" in phrases and len(phrases) == 15


def test_footer_required():
    md = f"## The Numbers\n\n{REQUIRED}\n"
    assert "footer" in kinds(check_issue(md, sheet(), BANNED))


def test_footer_must_be_last_line():
    md = f"## The Numbers\n\n{REQUIRED}\n\n{FOOTER}\n\nOne more line.\n"
    assert "footer" in kinds(check_issue(md, sheet(), BANNED))


def test_missing_placeholder_weekday_only():
    md = issue("", numbers="{{DGS10}} {{SOFR}} {{FED_TOP}} {{VNQ}}")
    ps = check_issue(md, sheet(), BANNED)
    assert {"kind": "missing_placeholder", "detail": "DGS10_CHG"} in ps
    assert "missing_placeholder" not in kinds(check_issue(md, sheet(day="sunday"), BANNED))


def test_em_dash_overuse():
    ok = issue("## Top Stories\n\nOne — two — three.")
    bad = issue("## Top Stories\n\nOne — two — three — four.")
    assert "em_dash" not in kinds(check_issue(ok, sheet(), BANNED))
    assert "em_dash" in kinds(check_issue(bad, sheet(), BANNED))


def test_exclaim_ignores_images_and_urls():
    ok = issue("![Chart of the Day](img/{{DATE}}-chart.png)\n\n"
               "[Src](https://x.com/a!b) and https://y.com/!z")
    bad = issue("## Top Stories\n\nHuge news!")
    assert "exclaim" not in kinds(check_issue(ok, sheet(), BANNED))
    assert "exclaim" in kinds(check_issue(bad, sheet(), BANNED))


def test_long_sentence_flagged():
    long = " ".join(["word"] * 31) + "."
    short = " ".join(["word"] * 30) + ". " + " ".join(["word"] * 30) + "."
    assert "long_sentence" in kinds(check_issue(issue(f"## Top Stories\n\n{long}"),
                                                sheet(), BANNED))
    assert "long_sentence" not in kinds(check_issue(issue(f"## Top Stories\n\n{short}"),
                                                    sheet(), BANNED))


def test_long_sentence_skips_headings():
    md = issue("## " + " ".join(["Heading"] * 35))
    assert "long_sentence" not in kinds(check_issue(md, sheet(), BANNED))


def test_budget_over_130_percent():
    words = lambda n: " ".join(["w."] * n)  # noqa: E731
    ok = issue(f"## Quick Hits\n\n{words(130)}")
    bad = issue(f"## Quick Hits\n\n{words(131)}")
    assert "budget" not in kinds(check_issue(ok, sheet(), BANNED))
    assert "budget" in kinds(check_issue(bad, sheet(), BANNED))


def test_budget_counts_link_text_and_placeholders_as_words():
    # 65 links with 2-word text = 130 words (at the limit); one placeholder tips it over.
    links = " ".join(["[two words](https://example.com/a)."] * 65)
    at_limit = issue(f"## Quick Hits\n\n{links}")
    over = issue(f"## Quick Hits\n\n{links} {{{{VNQ}}}}")
    assert "budget" not in kinds(check_issue(at_limit, sheet(), BANNED))
    assert "budget" in kinds(check_issue(over, sheet(), BANNED))


def test_leftover_comment_flagged():
    md = issue("## Top Stories\n\n<!-- 3 to 5 stories -->\nA deal.")
    assert "leftover_comment" in kinds(check_issue(md, sheet(), BANNED))


# --- fill -----------------------------------------------------------------

def test_fill_unknown_is_na():
    assert fill("{{X}}", {}) == ("n/a", ["X"])


def test_fill_known_values():
    out, missing = fill("10Y {{DGS10}} ({{DGS10_CHG}}), {{Y}} {{Y}}",
                        {"DGS10": "4.62%", "DGS10_CHG": "+3 bps"})
    assert out == "10Y 4.62% (+3 bps), n/a n/a"
    assert missing == ["Y"]


# --- draft ----------------------------------------------------------------

def test_draft_prompt_hides_numbers():
    seen = {}

    def fake_run(prompt, model):
        seen["prompt"], seen["model"] = prompt, model
        return "draft"

    out = draft.write(sheet(top=[story(1)]), run=fake_run)
    assert out.strip() == "draft"
    assert "4.62%" not in seen["prompt"]
    assert "Hold 81.0%" not in seen["prompt"]
    assert "{{DGS10}}" in seen["prompt"]
    assert '"DGS10"' in seen["prompt"]  # listed as a placeholder name
    assert "## The Numbers" in seen["prompt"]  # weekday template
    assert "game-changer" in seen["prompt"]  # banned list
    assert FOOTER in seen["prompt"]
    assert seen["model"] == "opus"


def test_draft_uses_day_template_and_strips_fences():
    seen = {}

    def fake_run(prompt, model):
        seen["prompt"] = prompt
        return "```markdown\n## REIT Weekly\nx\n```\n"

    out = draft.write(sheet(day="sunday"), run=fake_run)
    assert "## REIT Weekly" in seen["prompt"] and "## Week Ahead" in seen["prompt"]
    assert out == "## REIT Weekly\nx\n"


def test_edit_passes_draft_and_hides_numbers():
    seen = {}

    def fake_run(prompt, model):
        seen["prompt"], seen["model"] = prompt, model
        return "edited"

    out = draft.edit("## Top Stories\nMY DRAFT {{DGS10}}", sheet(), run=fake_run)
    assert out.strip() == "edited"
    assert "MY DRAFT {{DGS10}}" in seen["prompt"]
    assert "[CHECK]" in seen["prompt"]
    assert "4.62%" not in seen["prompt"]
    assert seen["model"] == "opus"


# --- templates ------------------------------------------------------------

KNOWN = {"DATE", "DGS10", "DGS10_CHG", "DGS5", "DGS5_CHG", "SOFR", "SOFR_CHG", "DFF",
         "DFF_CHG", "RATES_ASOF", "FED_MEETING", "FED_TOP", "VNQ", "VNQ_CHG", "REIT_UP",
         "REIT_DOWN", "VNQ_YIELD", "SPREAD_10Y"} | {
    f"REITW_{s}_{i}" for s in ("BEST", "WORST") for i in (1, 2, 3)}


def test_templates_structure():
    heads = {
        "weekday": ["The Numbers", "Debt Markets", "Top Stories", "Quick Hits",
                    "AI in Real Estate", "Term of the Day"],
        "friday": ["The Numbers", "Debt Markets", "Top Stories", "Quick Hits",
                   "Deals of the Week", "AI in Real Estate", "Term of the Day"],
        "saturday": ["Week in Review", "AI in Real Estate Weekly", "Term of the Day"],
        "sunday": ["REIT Weekly", "Week Ahead", "Term of the Day"],
    }
    for day, want in heads.items():
        text = (ROOT / "templates" / f"{day}.md").read_text(encoding="utf8")
        got = re.findall(r"^## (.+)$", text, re.M)
        assert [h for h in got if h in want] == [h for h in want], day
        lines = [ln for ln in text.splitlines() if ln.strip()]
        assert lines[-1] == FOOTER, day
        assert "no market rates" in text and "made-up round-number example" in text, day
        names = set(re.findall(r"\{\{(\w+)\}\}", text))
        assert names <= KNOWN, (day, names - KNOWN)
        if day in ("weekday", "friday"):
            assert "![Chart of the Day](img/{{DATE}}-chart.png)" in text
            assert {"DGS10", "DGS10_CHG", "SOFR", "FED_TOP", "VNQ"} <= names


def test_voice_has_sample_heading():
    text = (ROOT / "config" / "voice.md").read_text(encoding="utf8")
    assert "## Robert's sample issue" in text



# --- fix round 1 regressions ---------------------------------------------

def numbers_problems(text, fs=None):
    md = issue("", numbers=f"{REQUIRED}\n\n{text}")
    return [p for p in check_issue(md, fs or sheet(), BANNED) if p["kind"] == "model_number"]


def test_unit_spellings_flagged_in_numbers():
    for text in ("The 10Y rose 4.6 percent.", "It rose 4.6 per cent.", "Up 4.6 pct.",
                 "A 25bp move.", "A 25-basis-point cut.", "A 25-bps move.",
                 "A 25  bps move.", "A 1 basis point move.", "Debt hit $1.2 trillion.",
                 "Debt hit $3 tn."):
        assert numbers_problems(text), text


def test_trillion_not_sourced_by_billion():
    top = [story(1, summary="Issuance reached $1.2 billion.")]
    ok = issue("## Top Stories\n\nIssuance reached $1.2 billion. [Src](https://x.com/1)")
    bad = issue("## Top Stories\n\nIssuance reached $1.2 trillion. [Src](https://x.com/1)")
    assert check_issue(ok, sheet(top=top), BANNED) == []
    assert "unsourced_number" in kinds(check_issue(bad, sheet(top=top), BANNED))


def test_bare_decimals_and_odds_flagged_in_market_sections():
    assert numbers_problems("The 10Y closed at 4.62.")
    assert numbers_problems("Fed odds 81 to 19.")
    assert numbers_problems("VNQ closed at 91 dollars.")
    assert not numbers_problems("The 10Y moved {{DGS10_CHG}}.")
    assert not numbers_problems("The 10Y and 5Y moved before the Oct 28 meeting.")
    sun = issue("## Week Ahead\n\nThe Fed meets Oct 28; odds are 81 to 19.")
    assert "model_number" in kinds(check_issue(sun, sheet(day="sunday"), BANNED))


def test_image_alt_text_number_flagged():
    assert numbers_problems("![10Y at 4.6%](img/{{DATE}}-chart.png)")
    assert not numbers_problems("![Chart of the Day](img/{{DATE}}-chart.png)")


def test_term_of_the_day_flags_market_rates():
    bad = issue("## Term of the Day\n\n**Spread:** the gap over a benchmark. "
                "With the 10Y at 4.6%, a loan at 6% has a 140 bps spread.")
    ok = issue("## Term of the Day\n\n**Cap rate:** income over price. "
               "A $10 million building earning $500,000 trades at a 5% cap rate.")
    fed = issue("## Term of the Day\n\nIf the Fed cuts 25 bps, floating loans get cheaper.")
    assert "model_number" in kinds(check_issue(bad, sheet(), BANNED))
    assert "model_number" in kinds(check_issue(fed, sheet(), BANNED))
    assert check_issue(ok, sheet(), BANNED) == []


def test_slop_pattern_flagged():
    bad = issue("## Top Stories\n\nThis is not just a sale, but a signal.")
    also = issue("## Top Stories\n\nIt is Not only cheaper but faster.")
    ok = issue("## Top Stories\n\nThe sale is not final. But talks continue.")
    assert "slop_pattern" in kinds(check_issue(bad, sheet(), BANNED))
    assert "slop_pattern" in kinds(check_issue(also, sheet(), BANNED))
    assert "slop_pattern" not in kinds(check_issue(ok, sheet(), BANNED))


def test_edit_prompt_names_number_forms():
    seen = {}

    def fake_run(prompt, model):
        seen["prompt"] = prompt
        return "x"

    draft.edit("x", sheet(), run=fake_run)
    for form in ("percent", "%", "bp/bps/basis points", "$ amounts", "bare decimals"):
        assert form in seen["prompt"], form
