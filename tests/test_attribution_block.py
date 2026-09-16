"""Whether to say "I'm going by your record" is a fact about state.

The prompt asked the model to work out both the condition and the wording:
"Where an answer rests on what their file says rather than on anything they
told you, say so in one line and invite them to correct it." A condition in
prose is a request, and CLAUDE.md records what those are worth -- "a paragraph
in a tool schema is a warning, not a mechanism", twice over.

`coverage.basis` already holds the answer. This is the same sentence rendered
by code that reads it, like every other block `compose` hands over.
"""

from __future__ import annotations

from pension_chat.agent.loop import _attribution


def test_an_answer_resting_on_the_file_says_so():
    text = _attribution(
        {"coverage": {"basis": "record", "article": "11", "record_article": "11"}}
    )
    assert "your record" in text.lower()
    assert "Article 11" in text


def test_a_member_who_confirmed_is_not_told_what_their_file_says():
    """They said it. Attributing it to the file reads as not having listened."""
    assert _attribution({"coverage": {"basis": "member_confirmed", "article": "11"}}) == ""


def test_a_statutory_match_is_not_the_file_speaking():
    """The Code's own coverage test decided this, which is stronger than the
    record and is not something the member is being asked to correct."""
    assert _attribution({"coverage": {"basis": "statutory_match", "article": "12"}}) == ""


def test_a_supposition_is_not_attributed_to_the_record():
    assert _attribution({"coverage": {"basis": "hypothetical", "article": "5"}}) == ""


def test_no_coverage_is_nothing_to_attribute():
    assert _attribution({}) == ""
    assert _attribution({"coverage": None}) == ""


def test_an_unresolved_basis_attributes_nothing():
    """There is no file to be going by."""
    assert _attribution({"coverage": {"basis": "unresolved", "article": None}}) == ""


def test_the_invitation_to_correct_is_part_of_it():
    """Without it the line states a fact at the member rather than opening the
    question. The whole reason this sentence exists is that a member who has
    just described their job is entitled to say which of the two was used."""
    text = _attribution({"coverage": {"basis": "record", "article": "11"}})
    assert "tell me" in text.lower()


def test_the_prompt_no_longer_carries_the_condition():
    """A bug class: leaving the prose in place beside the block would have the
    model write the line twice, once on its own judgement."""
    from pension_chat.agent.nodes import ANSWER_SYSTEM

    assert "I'm going by your record" not in ANSWER_SYSTEM
    assert "One exception, and it is not chattiness" not in ANSWER_SYSTEM


async def test_compose_hands_the_line_over_rather_than_asking_for_a_judgement():
    from pension_chat.agent import loop as loop_mod

    out = await loop_mod.compose(
        {
            "question": "which retirement provision applies to me?",
            "messages": [],
            "loop_messages": [],
            "coverage": {"basis": "record", "article": "11", "record_article": "11"},
            "retrieved_sections": [],
        }
    )
    system = [
        str(m.get("content") or "")
        for m in out["answer_messages"]
        if m.get("role") == "system"
    ]
    assert any("I'm going by your record" in text for text in system)


async def test_a_confirmed_member_gets_no_such_line():
    from pension_chat.agent import loop as loop_mod

    out = await loop_mod.compose(
        {
            "question": "which retirement provision applies to me?",
            "messages": [],
            "loop_messages": [],
            "coverage": {"basis": "member_confirmed", "article": "11"},
            "retrieved_sections": [],
        }
    )
    assert not any(
        "going by your record" in str(m.get("content") or "")
        for m in out["answer_messages"]
    )
