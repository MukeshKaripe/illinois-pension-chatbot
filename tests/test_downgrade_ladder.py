"""What a failure costs, decided by what kind of failure it is.

Today every hard failure costs the same thing: the whole answer, replaced with
"I have taken that answer back -- sorry." That is right for a figure nobody can
trace and wrong for a sentence that named the wrong section beside four that
named the right one.
"""

from __future__ import annotations

from pension_chat.agent.guardrails import (
    CONSEQUENCE,
    EXCISE,
    REPAIR,
    RETRACT,
    GuardrailReport,
)


def test_an_untraceable_figure_still_retracts_everything():
    """The one failure that harms a member."""
    assert CONSEQUENCE["UNVERIFIED_FIGURE"] == RETRACT


def test_a_bad_citation_costs_its_own_sentence():
    assert CONSEQUENCE["UNVERIFIED_CITATION"] == EXCISE
    assert CONSEQUENCE["UNSUPPORTED_COVERAGE"] == EXCISE


def test_a_missing_disclosure_is_regenerated_not_cut():
    """Nothing in the answer is wrong -- a sentence is missing. Cutting text
    cannot add one, and cannot make a flat statement conditional."""
    assert CONSEQUENCE["ASSUMPTION_NOT_DISCLOSED"] == REPAIR
    assert CONSEQUENCE["UNMARKED_HYPOTHETICAL"] == REPAIR


def test_an_unrecognised_code_retracts():
    """A check added without a consequence must fail safe. The alternative is
    a new failure silently becoming excisable."""
    report = GuardrailReport()
    report.fail("SOMETHING_NEW", "...")
    assert report.consequence == RETRACT


def test_a_clean_report_costs_nothing():
    assert GuardrailReport().consequence == ""


def test_the_worst_failure_decides():
    report = GuardrailReport()
    report.fail("UNVERIFIED_CITATION", "...", spans=["a."])
    report.fail("UNVERIFIED_FIGURE", "...")
    assert report.consequence == RETRACT


def test_repair_outranks_excision():
    report = GuardrailReport()
    report.fail("UNVERIFIED_CITATION", "...", spans=["a."])
    report.fail("ASSUMPTION_NOT_DISCLOSED", "...")
    assert report.consequence == REPAIR


def test_an_excisable_failure_with_no_span_cannot_be_excised():
    """Knowing a sentence is wrong is not knowing which sentence."""
    report = GuardrailReport()
    report.fail("UNVERIFIED_CITATION", "...")
    assert report.consequence == RETRACT


def test_every_code_the_guardrail_can_raise_has_a_consequence():
    """A bug class, not a behaviour: a check added without an entry falls to
    the RETRACT default silently, which is safe but is not a decision anybody
    made. This fails until the table is updated, so the decision gets made."""
    import re
    from pathlib import Path

    source = Path("src/pension_chat/agent/guardrails.py").read_text(encoding="utf-8")
    raised = set(re.findall(r'\.fail\(\s*"([A-Z_]+)"', source))
    assert raised, "no failure codes found -- the pattern has drifted"
    assert raised <= set(CONSEQUENCE), f"no consequence for {raised - set(CONSEQUENCE)}"


# ── Where the answer went wrong, not just that it did ─────────────
#
# A span is a verbatim substring of the answer. It has to be: excision removes
# it by string match, and a span that does not appear is a cut that would
# silently do nothing -- publishing the bad sentence under a passing report.

from pension_chat.agent.guardrails import verify_answer  # noqa: E402

SECTIONS = [{"section": "11-134", "text": "The annuity is computed as follows."}]


def test_a_bad_citation_records_the_sentence_it_is_in():
    answer = (
        "Your annuity is computed under [40 ILCS 5/11-134]. "
        "The service rule lives in [40 ILCS 5/7-109]. "
        "That is the whole of it."
    )
    report = verify_answer(answer, retrieved_sections=SECTIONS, check_numbers=False)

    spans = report.hard_failures[0]["spans"]
    assert len(spans) == 1
    assert "7-109" in spans[0]
    assert "11-134" not in spans[0], "the good sentence must survive"
    assert spans[0] in answer, "a span must be verbatim, or excision cuts nothing"


def test_two_bad_citations_in_one_sentence_record_it_once():
    answer = "Sections [40 ILCS 5/7-109] and [40 ILCS 5/7-110] apply to you."
    report = verify_answer(answer, retrieved_sections=SECTIONS, check_numbers=False)
    assert len(report.hard_failures[0]["spans"]) == 1


def test_a_bad_citation_is_excisable_once_it_has_a_span():
    answer = (
        "Your annuity is computed under [40 ILCS 5/11-134]. "
        "The service rule lives in [40 ILCS 5/7-109]."
    )
    report = verify_answer(answer, retrieved_sections=SECTIONS, check_numbers=False)
    assert report.consequence == EXCISE


def test_a_clean_answer_records_no_spans():
    report = verify_answer(
        "Your annuity is computed under [40 ILCS 5/11-134].",
        retrieved_sections=SECTIONS,
        check_numbers=False,
    )
    assert report.passed
    assert report.consequence == ""


def test_an_unsupported_coverage_claim_records_its_own_sentence():
    """The claim goes; the law around it stays. An answer explaining Article 11
    correctly must not be withdrawn because one clause called it the member's."""
    answer = (
        "Section [40 ILCS 5/11-134] sets out how the annuity is computed. "
        "You are covered by Article 11. "
        "The reduction applies before age 60."
    )
    report = verify_answer(
        answer,
        retrieved_sections=SECTIONS,
        check_numbers=False,
        coverage={
            "basis": "unresolved",
            "article": "11",
            "record_article": None,
            "described_as": "",
        },
    )
    failure = next(
        f for f in report.hard_failures if f["code"] == "UNSUPPORTED_COVERAGE"
    )
    assert failure["spans"], "an unsupported claim must say which sentence made it"
    assert all(span in answer for span in failure["spans"])
    assert "11-134" not in " ".join(failure["spans"])
