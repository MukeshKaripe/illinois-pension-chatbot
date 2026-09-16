"""The stream stops at the first sentence the evidence does not support.

Without this, an ungrounded answer streams in full and is replaced at `done`.
The member reads it in the meantime. With it, at most one sentence is composed
and none of it is sent.
"""

from __future__ import annotations

from pension_chat.agent.grounding import StreamGate


def _drive(gate: StreamGate, deltas: list[str]) -> tuple[str, str | None]:
    """What a consumer of the gate actually sees across a whole stream."""
    shown = []
    for delta in deltas:
        text, broken = gate.feed(delta)
        if text:
            shown.append(text)
        if broken:
            return "".join(shown), broken
    tail, broken = gate.flush()
    if tail:
        shown.append(tail)
    return "".join(shown), broken


def test_a_clean_answer_streams_whole():
    gate = StreamGate({"11-134"})
    shown, broken = _drive(
        gate,
        [
            "Your annuity ",
            "is fixed at withdrawal ",
            "[40 ILCS 5/11-134]. ",
            "That is the rule.",
        ],
    )
    assert broken is None
    assert shown == (
        "Your annuity is fixed at withdrawal [40 ILCS 5/11-134]. That is the rule."
    )


def test_the_member_never_sees_the_ungrounded_sentence():
    gate = StreamGate({"11-134"})
    shown, broken = _drive(
        gate,
        [
            "Your annuity is fixed at withdrawal [40 ILCS 5/11-134]. ",
            "As a firefighter you would get 50% ",
            "[40 ILCS 5/4-109]. ",
            "So retiring now is better.",
        ],
    )
    assert broken == "4-109"
    assert "4-109" not in shown
    assert "firefighter" not in shown
    assert "retiring now is better" not in shown
    assert "11-134" in shown


def test_what_was_already_sound_is_kept():
    """The answer is cut, not discarded -- the repair path rewrites from here."""
    gate = StreamGate({"11-134"})
    shown, _ = _drive(
        gate, ["First point [40 ILCS 5/11-134]. ", "Second point [40 ILCS 5/9-134]. "]
    )
    assert shown.strip() == "First point [40 ILCS 5/11-134]."


def test_sections_cited_in_earlier_turns_count_as_available():
    """`verify_answer` keeps established sections available because a follow-up
    retrieves on its own words and need not surface the provision again. The
    gate has to agree, or it cuts the stream on a section already verified."""
    gate = StreamGate({"11-134"} | {"11-216"})
    shown, broken = _drive(gate, ["Service is counted under [40 ILCS 5/11-216]. "])
    assert broken is None
    assert "11-216" in shown
