"""The coverage node on a turn that talks about employment status.

"If I retired at the end of this year" is a remark about employment status, not
about which Article governs, and it used to leave a first turn with no coverage
verdict at all -- the status branch returned before anything set one. Every
case in `evals/fund_smoke.yaml` found it, because every one asks that question.

The status branch settles by looking at the row, never the corpus, so these run
offline.
"""

from __future__ import annotations

from pension_chat.agent.nodes import assess_coverage

RETIRING = "Roughly what would my pension be if I retired at the end of this year?"


async def test_a_supposition_about_retiring_leaves_coverage_on_the_record() -> None:
    update = await assess_coverage(
        {
            "question": RETIRING,
            "turn_index": 0,
            "employee_record": {"article_number": 3, "employment_status": "active"},
        }
    )
    assert update["coverage"]["article"] == "3"
    assert update["coverage"]["basis"] == "record"
    # A supposition is not a claim about the file, so there is nothing to ask.
    assert "clarification" not in update


async def test_a_status_claim_that_disagrees_still_asks_and_keeps_coverage() -> None:
    update = await assess_coverage(
        {
            "question": "I'm retired now. What is my pension?",
            "turn_index": 0,
            "employee_record": {"article_number": 11, "employment_status": "active"},
        }
    )
    assert update["clarification"]["about"] == "employment_status"
    assert update["coverage"]["article"] == "11"


async def test_a_status_remark_does_not_overwrite_settled_coverage() -> None:
    settled = {"article": "12", "basis": "member_confirmed", "record_article": "11"}
    update = await assess_coverage(
        {
            "question": RETIRING,
            "turn_index": 2,
            "employee_record": {"article_number": 11, "employment_status": "active"},
            "coverage": settled,
        }
    )
    assert "coverage" not in update
