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
