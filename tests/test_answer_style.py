"""Tests for the member-facing voice.

Fifteen of eighteen regression answers used to describe the machinery rather
than the pension: "the calculation engine", "the supplied statute text", "the
stored recipe was reused", "formula ID 11_retirement_annuity_11-134_c0d3c001".
Eight calculation answers between them used eight different table headers.

Almost none of that was the model inventing vocabulary. It was reading it off
the labels in the prompt — a block headed "CALCULATION RESULTS from the
deterministic engine" produces answers about a deterministic engine — and off
the bookkeeping fields in the JSON underneath. So these tests hold the line
where the leak actually was: what `prepare_answer` puts in front of the model,
and what the two non-model answer paths say on their own.
"""

from __future__ import annotations

import json
import re

import pytest

from pension_chat.agent.graph import GREETING_MESSAGE, OUT_OF_SCOPE_MESSAGE
from pension_chat.agent.guardrails import GuardrailReport, fallback_answer
from pension_chat.agent.nodes import (
    _ASK_INSTEAD,
    ANSWER_SYSTEM,
    _format_calculation,
    prepare_answer,
)
from pension_chat.agent.state import initial_state

# Words that describe how the answer was assembled rather than what is true of
# the member's pension. None of them belong in text a member reads.
MACHINERY = re.compile(
    r"\b(calculation engine|the engine|engine value|engine result|deterministic|"
    r"recipe|registry|formula[ _]id|knowledge[ _]status|execution[ _]policy|"
    r"risk[ _]tier|agent[ _-]derived|was supplied|were supplied|supplied "
    r"(?:statute|text|information|calculation|scenario|facts)|"
    r"retrieved for it|not among the sections|rule specification)\b",
    re.IGNORECASE,
)


def _prompt_text(state: dict) -> str:
    """What the model is shown for THIS turn, as one string.

    The static house style is excluded: it names the forbidden vocabulary in
    order to forbid it, and that list is the point. What is scanned is the
    volatile per-turn context — the labels and JSON that were the actual source
    of the leak.
    """
    return "\n".join(
        m["content"] for m in state["answer_messages"] if m["content"] != ANSWER_SYSTEM
    )


CALCULATION = {
    "retirement_annuity": {
        "formula_type": "retirement_annuity",
        "formula_name": "Minimum annuity",
        "section": "11-134",
        "subsection": "(a)",
        "execution_policy": "estimate",
        "knowledge_status": "agent_derived",
        "risk_tier": "low",
        "base": {
            "ok": True,
            "annual_amount": "32126.78",
            "monthly_amount": "2677.23",
            "steps": [{"name": "unreduced_annuity", "value": "36097.50"}],
            "statutory_values_used": {
                "accrual_rate": {"value": 0.024, "source": {"section": "11-134"}}
            },
        },
    }
}


# ── What reaches the model ────────────────────────────────────────

def test_the_figures_block_drops_bookkeeping_the_member_has_no_use_for():
    rendered = json.loads(_format_calculation(CALCULATION))["retirement_annuity"]

    assert set(rendered) == {"section", "subsection", "is_estimate", "result"}
    assert rendered["is_estimate"] is True
    # The figures themselves survive intact — this trims vocabulary, not evidence.
    assert rendered["result"]["annual_amount"] == "32126.78"
    assert rendered["result"]["steps"][0]["name"] == "unreduced_annuity"


def test_an_authoritative_recipe_is_not_labelled_an_estimate():
    promoted = {
        "retirement_annuity": {
            **CALCULATION["retirement_annuity"],
            "execution_policy": "authoritative",
        }
    }
    rendered = json.loads(_format_calculation(promoted))["retirement_annuity"]

    assert rendered["is_estimate"] is False


@pytest.mark.anyio
async def test_no_machinery_vocabulary_reaches_the_answering_model():
    state = initial_state("What would my pension be?", "s")
    state.update(
        calculation_result=CALCULATION,
        member_facts={"age": 56, "service_years": 21.5},
        recipe_provenance=[
            {
                "formula_type": "retirement_annuity",
                "source": "registry",
                "formula_id": "11_retirement_annuity_11-134_b059faa8",
                "status": "agent_derived",
                "section": "11-134",
                "reused": True,
                "alternatives_considered": ["11-130(1) — no executable conversion"],
            }
        ],
        recipes={"retirement_annuity": {"applicability_check": {"status": "verified"}}},
        retrieved_sections=[{"section": "11-134", "title": "Minimum annuities", "text": "..."}],
    )

    prompt = _prompt_text(await prepare_answer(state))
    leaks = sorted({m.group(0).lower() for m in MACHINERY.finditer(prompt)})

    assert leaks == [], f"machinery vocabulary in the answer prompt: {leaks}"


@pytest.mark.anyio
async def test_the_identifier_is_withheld_but_reuse_can_still_be_answered():
    """The member chose: provenance on request, never volunteered.

    So the fact of reuse stays available to the model — test question 10 asks
    for it outright — while the row identifier, which means nothing to anyone
    outside the registry, does not.
    """
    state = initial_state("Did you reuse the earlier calculation?", "s")
    state.update(
        recipe_provenance=[
            {
                "formula_type": "retirement_annuity",
                "formula_id": "11_retirement_annuity_11-134_b059faa8",
                "reused": True,
                "section": "11-134",
            }
        ],
        recipes={},
        retrieved_sections=[],
    )

    prompt = _prompt_text(await prepare_answer(state))

    assert "b059faa8" not in prompt
    assert "worked_out_fresh_this_time" in prompt


@pytest.mark.anyio
async def test_an_internal_block_reason_is_never_offered_as_wording():
    state = initial_state("What would my pension be?", "s")
    state.update(execution_blocked="execution_policy_no_execute", retrieved_sections=[])

    prompt = _prompt_text(await prepare_answer(state))

    # The reason is still there for the model to reason from, explicitly marked
    # as something to translate rather than repeat.
    assert "execution_policy_no_execute" in prompt
    assert "Do not repeat that phrase" in prompt


# ── The house style itself ────────────────────────────────────────

def test_the_house_style_fixes_one_table_shape():
    # Eight calculation answers previously used eight different headers
    # ("Item | Value", "Step | Amount", "Calculation item | Engine value", ...).
    assert "| Step | Amount |" in ANSWER_SYSTEM
    assert "**How that is worked out**" in ANSWER_SYSTEM


def test_a_comparison_table_carries_the_driver_not_only_the_money():
    """Four rising numbers with no reason beside them are taken on trust.

    Service is what mostly moves the figure between retirement dates, and it is
    already computed per scenario, so it belongs in the table rather than in a
    paragraph underneath.
    """
    assert (
        "| If you retire | Years of service | Annual pension | Monthly pension |"
        in ANSWER_SYSTEM
    )


def test_the_house_style_states_the_estimate_line_verbatim():
    assert (
        "This is an estimate — your retirement fund makes the binding determination."
        in ANSWER_SYSTEM
    )


# ── Answers written in Python, not by the model ───────────────────

@pytest.mark.parametrize(
    ("code", "expected_fragment"),
    [
        ("UNVERIFIED_FIGURE", "years of service"),
        ("UNVERIFIED_CITATION", "Articles 11 and 12"),
        ("EMPTY_ANSWER", "ask again more specifically"),
    ],
)
def test_the_withdrawal_message_explains_itself_without_the_codes(code, expected_fragment):
    report = GuardrailReport()
    report.fail(code, "The answer cites 40 ILCS 5/11-164, which was not among the "
                      "sections retrieved for it.")

    message = fallback_answer(report)

    assert expected_fragment in message
    assert not MACHINERY.search(message), MACHINERY.search(message).group(0)
    # The internal message is not pasted through to the member.
    assert "11-164" not in message
    assert "retirement fund" in message


@pytest.mark.parametrize("message", [GREETING_MESSAGE, OUT_OF_SCOPE_MESSAGE])
def test_the_templated_replies_speak_to_the_member(message):
    assert not MACHINERY.search(message)
    assert "pension" in message.lower()
    assert len(message.split()) < 60


def test_the_house_style_forbids_the_vocabulary_by_name():
    # Naming the words is what makes the prohibition stick, so the house style
    # is the one place they are allowed to appear.
    for word in ("calculation engines", "recipes", "registries", "supplied", "retrieved"):
        assert word in ANSWER_SYSTEM
    assert "Never describe how you work." in ANSWER_SYSTEM


# ── Asking for what a member can actually supply ──────────────────

def test_derived_quantities_are_asked_for_by_their_underlying_facts():
    """A recipe's input names are not questions a person can answer.

    An anonymous member asking what their pension would be was told: "I need
    your withdrawal date, your creditable years of service, your highest average
    annual salary for any 4 consecutive years, and the number of months or
    partial months your annuity would begin before age 60." The last of those is
    the system asking the member to perform its own derivation.
    """
    assert _ASK_INSTEAD["months_before_age_60"] == "your date of birth"
    assert "date you plan to stop working" in _ASK_INSTEAD["withdrawal_date"]
    # Each request introduces exactly one new fact. Asking for the leaving date
    # under both entries produced a numbered list that asked for it twice.
    assert len(set(_ASK_INSTEAD.values())) == len(
        {v for v in _ASK_INSTEAD.values()}
    )
    assert "stop working" not in _ASK_INSTEAD["months_before_age_60"]
    # And nothing in the member-facing phrasings reads like a variable name.
    for phrasing in _ASK_INSTEAD.values():
        assert "_" not in phrasing


def test_the_house_style_acknowledges_what_is_known_before_asking():
    assert "what they have just told you" in ANSWER_SYSTEM
    assert '"ask_for"' in ANSWER_SYSTEM


def test_the_house_style_forbids_reusing_derivation_time_facts():
    """A stored reason describes the provision, not the member it was derived for.

    One reason reads "...cannot begin before age 60, while this annuity begins
    at age 56". Reused verbatim, that told a member who had just said they were
    58 that their annuity begins at 56.
    """
    assert "as though it were this member's" in ANSWER_SYSTEM
