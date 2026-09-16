"""Tests for catching an ungrounded claim while it is still being written.

Verification runs after generation, so an answer citing a section nobody
retrieved streamed in full and was replaced seconds later -- the member read a
sentence about their pension that the Code does not support. A citation is
checkable the instant it appears: set membership, no model call. Holding each
sentence until its citations clear costs a fraction of a second and bounds the
damage to one sentence that never left the server.
"""

from __future__ import annotations

from pension_chat.agent.grounding import StreamGate

AVAILABLE = {"11-134", "11-216"}


def test_text_without_citations_flows_sentence_by_sentence():
    gate = StreamGate(AVAILABLE)
    out, broken = gate.feed("Your pension depends on two things. ")
    assert out == "Your pension depends on two things. "
    assert broken is None


def test_a_partial_sentence_is_held_until_it_completes():
    """Held, not dropped -- and released once the boundary is unambiguous."""
    gate = StreamGate(AVAILABLE)
    assert gate.feed("Your pension is based") == ("", None)
    assert gate.feed(" on your service.") == ("", None)
    assert gate.feed(" Next.") == ("Your pension is based on your service. ", None)


def test_a_full_stop_needs_whitespace_after_it_to_end_a_sentence():
    """A trailing "." is not yet a sentence end, because "2." followed by "40%"
    is a rate arriving in two deltas. Releasing eagerly would cut it in half and
    show the member "2." as a finished thought."""
    gate = StreamGate(AVAILABLE)
    assert gate.feed("The rate is 2.") == ("", None)
    out, broken = gate.feed("40% of salary. ")
    assert out == "The rate is 2.40% of salary. "
    assert broken is None


def test_a_sentence_citing_a_retrieved_section_is_released():
    gate = StreamGate(AVAILABLE)
    out, broken = gate.feed("The annuity is fixed at withdrawal [40 ILCS 5/11-134]. ")
    assert "11-134" in out
    assert broken is None


def test_a_sentence_citing_an_unretrieved_section_is_never_emitted():
    gate = StreamGate(AVAILABLE)
    out, broken = gate.feed("Article 4 gives you 50% of salary [40 ILCS 5/4-109]. ")
    assert out == ""
    assert broken == "4-109"


def test_once_broken_the_gate_stays_shut():
    """The rest of the answer rests on the sentence that failed."""
    gate = StreamGate(AVAILABLE)
    gate.feed("Article 4 gives you 50% [40 ILCS 5/4-109]. ")
    assert gate.feed("And then some more text. ") == ("", "4-109")
    assert gate.flush() == ("", "4-109")
    assert gate.broken == "4-109"


def test_good_sentences_before_a_bad_one_are_kept():
    gate = StreamGate(AVAILABLE)
    out, broken = gate.feed(
        "Your annuity is fixed at withdrawal [40 ILCS 5/11-134]. "
        "Article 4 pays 50% [40 ILCS 5/4-109]. "
    )
    assert "11-134" in out
    assert "4-109" not in out
    assert broken == "4-109"


def test_flush_releases_a_clean_trailing_sentence_without_a_full_stop():
    gate = StreamGate(AVAILABLE)
    gate.feed("Your service counts under Section 11-216")
    assert gate.flush() == ("Your service counts under Section 11-216", None)


def test_flush_withholds_a_trailing_sentence_that_does_not_clear():
    gate = StreamGate(AVAILABLE)
    gate.feed("See [40 ILCS 5/4-109]")
    out, broken = gate.flush()
    assert out == ""
    assert broken == "4-109"


def test_a_citation_split_across_deltas_is_not_judged_early():
    """Tokens arrive mid-citation. Judging '[40 ILCS 5/11-1' would reject a
    section that is about to be fine."""
    gate = StreamGate(AVAILABLE)
    assert gate.feed("The rule is [40 ILCS 5/11-") == ("", None)
    out, broken = gate.feed("134]. ")
    assert "11-134" in out
    assert broken is None


def test_a_bare_mention_is_not_gated():
    """Only bracketed citations are gated here. A bare mention is held to a
    weaker standard by `verify_answer` on purpose -- Article 11 is written in
    cross-references, and naming the provision a retrieved section sends you to
    is reading the page rather than recalling it."""
    gate = StreamGate(AVAILABLE)
    out, broken = gate.feed("Service is governed by Section 4-109. ")
    assert out == "Service is governed by Section 4-109. "
    assert broken is None


def test_newlines_end_a_sentence():
    """Blocks and list items arrive without full stops."""
    gate = StreamGate(AVAILABLE)
    out, broken = gate.feed("- 2.40% for each year [40 ILCS 5/11-134]\n")
    assert "11-134" in out
    assert broken is None


def test_an_empty_feed_is_harmless():
    gate = StreamGate(AVAILABLE)
    assert gate.feed("") == ("", None)
