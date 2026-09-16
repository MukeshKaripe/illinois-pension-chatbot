"""The guard against answering from memory, and the seam that keeps it.

The loop pushes back once when the model declares itself done having read
nothing. That guard exists because the loop once answered from memory that
Article 11 is the Municipal Employees' fund -- it is the Laborers' fund -- and
verification passed it, because an answer with no citations has nothing to
verify.

A later plan hands the loop statute text before its first model call. That would
make `sections` non-empty on every turn and silently retire the guard. The
invariant has to be "read nothing it was not handed", not "read nothing".
"""

from __future__ import annotations

from pension_chat.agent.loop import _read_beyond_prefetch


def test_nothing_read_at_all_is_a_pushback():
    assert _read_beyond_prefetch(sections=[], prefetched=set()) == []


def test_reading_something_unprompted_is_not():
    sections = [{"section": "11-134"}]
    assert _read_beyond_prefetch(sections=sections, prefetched=set()) == ["11-134"]


def test_being_handed_a_section_is_not_the_same_as_reading_one():
    """The case the seam exists for: prefetch fills `sections`, the model reads
    nothing more, and the guard must still fire."""
    sections = [{"section": "11-134"}]
    assert _read_beyond_prefetch(sections=sections, prefetched={"11-134"}) == []


def test_reading_past_what_was_handed_over_counts():
    sections = [{"section": "11-134"}, {"section": "11-216"}]
    assert _read_beyond_prefetch(sections=sections, prefetched={"11-134"}) == ["11-216"]


def test_a_section_with_no_id_is_ignored_rather_than_counted():
    assert _read_beyond_prefetch(sections=[{"text": "..."}], prefetched=set()) == []
