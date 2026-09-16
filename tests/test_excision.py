"""Cut the sentence, keep the answer -- and only if what is left stands up.

Excision publishes text that was already streamed, minus a span. If the
remainder does not verify on its own, publishing it is worse than the
retraction it replaces, so the old repair-then-fallback path takes over.

It costs no model call, which is the other half of the point: the common
failure case gets faster as well as less destructive.
"""

from __future__ import annotations

from pension_chat.agent.guardrails import excise


def test_a_span_is_removed_and_the_rest_closes_up():
    assert excise("One. Two. Three.", ["Two."]) == "One. Three."


def test_removing_everything_leaves_nothing():
    """The caller checks for this and falls through; excise does not pretend."""
    assert excise("Only this.", ["Only this."]) == ""


def test_a_span_that_is_not_there_changes_nothing():
    assert excise("One. Two.", ["Three."]) == "One. Two."


def test_paragraph_structure_survives():
    assert excise("Intro.\n\nOne. Bad. Two.\n\nEnd.", ["Bad."]) == (
        "Intro.\n\nOne. Two.\n\nEnd."
    )


def test_an_emptied_paragraph_does_not_leave_a_hole():
    assert excise("Intro.\n\nBad.\n\nEnd.", ["Bad."]) == "Intro.\n\nEnd."


def test_the_longest_span_goes_first():
    """A sentence containing another must not be left with a fragment of it."""
    answer = "Alpha beta gamma. Delta."
    assert excise(answer, ["beta", "Alpha beta gamma."]) == "Delta."


def test_nothing_to_cut_returns_the_answer():
    assert excise("One. Two.", []) == "One. Two."


# ── The decision, against the real verifier ───────────────────────

from functools import partial  # noqa: E402

from pension_chat.agent.guardrails import (  # noqa: E402
    GuardrailReport,
    downgrade,
    verify_answer,
)

SECTIONS = [
    {"section": "11-134", "text": "The annuity is computed as provided in this Section."}
]

_verify = partial(verify_answer, retrieved_sections=SECTIONS, check_numbers=False)


def _checked(answer, **kwargs):
    report = _verify(answer, **kwargs)
    return answer, report


GOOD = (
    "Your retirement annuity is computed under [40 ILCS 5/11-134], which sets "
    "out how the amount is arrived at and when it may begin. The reduction for "
    "starting early applies for each month before the age the Section names. "
)
BAD = "The service rule for this is at [40 ILCS 5/7-109]."


def test_the_bad_sentence_goes_and_the_answer_survives():
    answer, report = _checked(GOOD + BAD)
    assert not report.passed

    salvaged = downgrade(answer, report, _verify)
    assert salvaged is not None
    text, retry = salvaged
    assert retry.passed
    assert "7-109" not in text
    assert "11-134" in text


def test_an_untraceable_figure_is_never_salvaged():
    """The one failure that harms a member. Deleting the sentence around a
    figure nobody can trace does not make the answer safe."""
    report = GuardrailReport()
    report.fail("UNVERIFIED_FIGURE", "...")
    assert downgrade(GOOD, report, _verify) is None


def test_too_little_left_is_not_an_answer():
    """A member is better told plainly that the answer was withdrawn than
    handed the two sentences that happened to survive."""
    answer, report = _checked("Short one. " + BAD)
    assert downgrade(answer, report, _verify) is None


def test_a_remainder_that_does_not_verify_is_refused():
    """Publishing unverified text under a passing report would be worse than
    the retraction it replaces."""
    answer, report = _checked(GOOD + BAD)

    def verify_but_fail(text):
        failed = GuardrailReport()
        failed.fail("UNVERIFIED_FIGURE", "something else is wrong")
        return failed

    assert downgrade(answer, report, verify_but_fail) is None


def test_a_passing_report_is_left_alone():
    answer, report = _checked(GOOD)
    assert report.passed
    assert downgrade(answer, report, _verify) is None


def test_the_verifier_is_called_once_at_most():
    answer, report = _checked(GOOD + BAD)
    calls = []

    def counting(text):
        calls.append(text)
        return _verify(text)

    downgrade(answer, report, counting)
    assert len(calls) == 1, "excision costs no model call and one re-verification"
