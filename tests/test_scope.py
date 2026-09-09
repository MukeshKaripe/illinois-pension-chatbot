"""Tests for what the assistant will and will not take on.

Reported from live use: "i want an ice cream" was answered with "Go for it —
you've earned one", "can you help me sleep" returned a page of sleep-hygiene
advice ending in a referral to a healthcare professional, and a request to
order food got a polite explanation of how to order food.

The cause was the default. Scope classification refused an explicit blocklist,
treated three words or fewer as a greeting, and passed **everything else**
through to the full pipeline:

    if any(term in lowered for term in _PENSION_TERMS): return in_scope
    if len(lowered.split()) <= 3:                       return greeting
    return ScopeVerdict(True)                           # <- anything at all

The default is now refusal. The interesting half of that change is not the
refusals — it is that flipping a default is the easiest way to build a system
that refuses real questions, so most of what follows guards the other side:
members do not ask about their pension in statutory vocabulary.
"""

from __future__ import annotations

import pytest

from pension_chat.agent.guardrails import classify_scope

# ── Things it must not take on ────────────────────────────────────

@pytest.mark.parametrize(
    "question",
    [
        "i want an ice cream",
        "i want to order some food can you order it for me?",
        "can you help me sleep",
        "what's a good movie tonight",
        "help me write a cover letter",
        "what is the capital of France",
        "can you book me a flight to Denver next week",
        "write a python function to sort a list",
        "give me a recipe for banana bread",
        "what will the weather be tomorrow",
    ],
)
def test_unrelated_requests_are_refused(question):
    assert classify_scope(question).in_scope is False


def test_an_aside_mid_conversation_is_still_refused():
    """Having talked about pensions does not open the door to everything else."""
    assert classify_scope("i want an ice cream", has_history=True).in_scope is False


@pytest.mark.parametrize("question", ["hi", "hello there", "thanks"])
def test_greetings_are_answered_as_greetings_not_refusals(question):
    verdict = classify_scope(question)

    assert verdict.in_scope is False
    assert verdict.reason == "greeting"


# ── Things it must take on ────────────────────────────────────────

@pytest.mark.parametrize(
    "question",
    [
        # Statutory vocabulary
        "What does Section 11-116 define as salary?",
        "Am I eligible to retire?",
        "Explain the survivor annuity",
        "what does my widow get if I die",
        "can I get a refund of my contributions",
        # A bare section number and nothing else
        "Explain 11-130",
        # No statutory vocabulary whatsoever — the over-correction cases
        "How much will I get when I stop working?",
        "what happens if I wait 2 more years?",
        "and what about at age 60?",
        "I'm 56 with 21.5 years of service. Calculate my benefit.",
        "Compare my benefit if I retire now, at 57 and at 60.",
    ],
)
def test_pension_questions_are_answered(question):
    assert classify_scope(question).in_scope is True


@pytest.mark.parametrize(
    "question",
    ["why not?", "what about that?", "and then?", "ok explain that"],
)
def test_bare_follow_ups_are_answered_only_as_follow_ups(question):
    """"why not?" is a pension question in context and nothing out of it."""
    assert classify_scope(question, has_history=True).in_scope is True
    assert classify_scope(question, has_history=False).in_scope is False


def test_an_empty_message_is_not_treated_as_a_greeting():
    verdict = classify_scope("   ")

    assert verdict.in_scope is False
    assert verdict.reason != "greeting"


def test_the_retire_or_keep_working_question_is_in_scope():
    """Found by probing query shapes the suite never covered.

    "is it worth it to keep working" is the comparison question in a member's
    own words — no statutory vocabulary, no age, no section number — and the
    default-deny flip refused it. That is the failure mode flipping a default
    invites, so it is pinned here.
    """
    for question in (
        "is it worth it to keep working",
        "should i retire",
        "is it worth it to retire now",
    ):
        assert classify_scope(question).in_scope is True, question


# -- Refusals found by the fourteen-turn end-to-end case -----------

class TestMidConversationRefusals:
    """The deny-by-default kept turning members away mid-conversation.

    Three separate refusals inside one fourteen-turn case, each of them a
    question this assistant exists to answer, and each phrased without a single
    word from the benefit vocabulary:

      * "Which provisions apply to me?" — names no benefit, age or section.
      * "Now show me the same at ages 57, 59 and 60" — the timing pattern
        matched "age 60" but not the plural "ages 60".
      * "Re-run the applicability analysis" — the member replying in the words
        the assistant had just used at them.

    The default is right for a stranger's first question and wrong as a way to
    police a conversation already under way. What it needed was the vocabulary
    of the *question*, not more vocabulary of the benefit.
    """

    @pytest.mark.parametrize(
        "question",
        [
            "Which provisions apply to me?",
            "Which provisions could you calculate, and which actually govern my case?",
            "Now show me the same at ages 57, 59 and 60, keeping everything unchanged.",
            "I think the pathway you picked was wrong. Re-run the applicability analysis.",
            "Am I eligible to retire?",
            "What is your determination, and what assumptions did it rest on?",
        ],
    )
    def test_a_member_continuing_the_conversation_is_not_turned_away(self, question):
        assert classify_scope(question, has_history=True).in_scope

    @pytest.mark.parametrize(
        "question",
        [
            "write me a poem about it",
            "i want an ice cream",
            "can you help me sleep",
            "what is the weather like today",
            "who won the game last night",
        ],
    )
    def test_and_the_widening_did_not_let_these_back_in(self, question):
        """The complaint that started the deny-by-default, still answered."""
        assert not classify_scope(question, has_history=True).in_scope


def test_with_model_routing_only_the_blatant_cases_are_refused_deterministically() -> None:
    from pension_chat.agent.guardrails import classify_scope

    # High precision: when this pattern fires it is right, so it stays free.
    assert not classify_scope("write me a poem about my dog", model_routing=True).in_scope

    # Low recall in the other direction: these were all refused by the word
    # list and are all pension questions.
    for question in [
        "I'm starting to think seriously about retiring but I don't know where to begin",
        "My wife is worried about what happens to her if something happens to me",
        "Does any of this get taxed, and do I still get health insurance?",
    ]:
        assert classify_scope(question, model_routing=True).in_scope, question


def test_without_model_routing_the_old_behaviour_is_unchanged() -> None:
    from pension_chat.agent.guardrails import classify_scope

    assert classify_scope("how much pension will i get").in_scope
    assert not classify_scope("write me a poem about my dog").in_scope
