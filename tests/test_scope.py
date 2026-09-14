"""Tests for what the deterministic gate will and will not decide on its own.

This file has had its premise inverted twice, and both directions were bugs.

**Default in-scope** was the original, and it answered "i want an ice cream"
with "Go for it — you've earned one" and "can you help me sleep" with a page of
sleep-hygiene advice.

**Default deny** replaced it: a message matching no word in a pension vocabulary
was refused. That fixed the ice cream and broke the thing this assistant is for.
Three refusals inside one fourteen-turn case, and on the routing suite it turned
away "I'm starting to think seriously about retiring" (because "retiring" does
not contain "retire"), "my wife is worried about what happens to her" (the list
held "spouse" and "widow", not "wife") and "does any of this get taxed" — each in
1.75s at zero cost, which is the cheapest possible way to fail somebody.

**The default is now in-scope again, but the mechanism is different**: deciding
what a member meant moved to `understand_turn`, and what stays here is only what
a regular expression can be trusted with — a deny list that is right whenever it
fires, and a greeting. So the tests below come in three groups: what is still
refused for free, what is deliberately *not* decided here any more, and the
greeting exit.

The measured price of the change is one routing call (~570 tokens) on an
off-topic aside the deny list does not name. See eval S5, which had its `no_llm`
expectation dropped for exactly this reason.
"""

from __future__ import annotations

import pytest

from pension_chat.agent.guardrails import classify_scope

# ── Still refused for free ────────────────────────────────────────
#
# Every one of these trips `_OUT_OF_SCOPE`, which is high precision by
# construction: when it fires it is right, so it costs nothing.


@pytest.mark.parametrize(
    "question",
    [
        "i want to order some food can you order it for me?",
        "can you help me sleep",
        "help me write a cover letter",
        "can you book me a flight to Denver next week",
        "give me a recipe for banana bread",
        "what will the weather be tomorrow",
        "write me a poem about my dog",
        "who won the game last night",
    ],
)
def test_the_blatant_cases_are_refused_with_no_model_call(question):
    assert classify_scope(question).in_scope is False


def test_a_blatant_case_is_refused_mid_conversation_too():
    """Having talked about pensions does not open the door to everything else."""
    assert classify_scope("write me a poem about it", has_history=True).in_scope is False


# ── Deliberately not decided here any more ────────────────────────


@pytest.mark.parametrize(
    "question",
    [
        "i want an ice cream",
        "what's a good movie tonight",
        "what is the capital of France",
        "write a python function to sort a list",
    ],
)
def test_an_aside_the_deny_list_does_not_name_reaches_the_model(question):
    """These are refused, but by `understand_turn`, not by a word list.

    Catching them here would mean enumerating everything a member might type,
    and the bill for that list was paid by real pension questions. The model
    refuses them correctly for about 570 tokens; eval S5 is the live proof.
    """
    assert classify_scope(question).in_scope is True


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
        # The three the vocabulary list refused outright, at zero cost.
        "I'm starting to think seriously about retiring but I don't know where to begin",
        "My wife is worried about what happens to her if something happens to me",
        "Does any of this get taxed, and do I still get health insurance?",
        # The comparison question in a member's own words.
        "is it worth it to keep working",
        "should i retire",
    ],
)
def test_pension_questions_are_answered(question):
    assert classify_scope(question).in_scope is True


@pytest.mark.parametrize(
    "question",
    [
        "Which provisions apply to me?",
        "Which provisions could you calculate, and which actually govern my case?",
        "Now show me the same at ages 57, 59 and 60, keeping everything unchanged.",
        "I think the pathway you picked was wrong. Re-run the applicability analysis.",
        "What is your determination, and what assumptions did it rest on?",
        "why not?",
        "what about that?",
        "and then?",
        "ok explain that",
    ],
)
def test_a_member_continuing_the_conversation_is_not_turned_away(question):
    """Three separate refusals inside one fourteen-turn case came from here.

    Each was a question this assistant exists to answer, and each was phrased
    without a single word from the benefit vocabulary — including the member
    replying in the words the assistant had just used at them.
    """
    assert classify_scope(question, has_history=True).in_scope


# ── The greeting exit ─────────────────────────────────────────────


@pytest.mark.parametrize("question", ["hi", "Hello!", "hey there", "good morning", "bye"])
def test_a_pleasantry_costs_nothing_and_is_answered_as_one(question):
    """"hi" needs no model call to classify, and must not get one.

    Routing it onward loses two things: the round trip, and the reason code.
    `decline` picks GREETING_MESSAGE off `out_of_scope_reason == "greeting"`, so
    a model-written clause in its place answers "hi" with "that is outside the
    Pension Code".
    """
    verdict = classify_scope(question, has_history=True)

    assert verdict.in_scope is False
    assert verdict.reason == "greeting"


@pytest.mark.parametrize("question", ["yes", "ok", "no", "thanks", "sure"])
def test_a_bare_acknowledgement_is_a_greeting_only_with_nothing_before_it(question):
    """Mid-conversation it is how a member answers what was just asked.

    `_CONTINUATION` matched "ok", "yes", "no" and "thanks" for exactly this
    reason, and a whole-message greeting gate that ignored `has_history` would
    throw the member's answer away and reply with a salutation.
    """
    assert classify_scope(question, has_history=True).in_scope is True
    assert classify_scope(question, has_history=False).in_scope is False


def test_a_greeting_with_a_question_attached_is_a_question():
    assert classify_scope("hi, how much will i get?").in_scope is True


def test_an_empty_message_is_not_treated_as_a_greeting():
    verdict = classify_scope("   ")

    assert verdict.in_scope is False
    assert verdict.reason != "greeting"
# ── The model's own scope verdict ──────────────────────────────────

# `_still_in_scope` went with the pipeline at cutover. It re-read the model's
# own scope verdict on an edge, because `understand_turn` ran *after* `triage`
# had already decided and its `in_scope: false` declined nothing.
#
# There is no second verdict to re-read now: `triage` is still the
# deterministic gate in front of the loop, and a turn it passes reaches a model
# that can decline in its own words. Worth naming the risk this test was pinned
# against — an eval asserting a state key passed while the behaviour was wrong —
# so the replacement is behavioural: S1, S2, S5, S6, S7, S8 and S9 all pass on
# the loop and they check the answer, not the key.

