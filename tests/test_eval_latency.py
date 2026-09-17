"""Latency per class, because one median cannot show two classes diverging.

Spec §3's finding was that explanation and calculation cost the same -- 10.56s
against 10.89s at n=100 -- which is what made the architecture robust to not
knowing the question mix. A single number cannot express that, and cannot show
it ceasing to be true after a change that helps one class and not the other.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from eval_agent import latency_by_class  # noqa: E402


def _case(category, *ttfts):
    return {"category": category, "turns": [{"ttft": t} for t in ttfts]}


def test_each_class_gets_its_own_distribution():
    stats = latency_by_class([_case("figure", 1.0, 2.0, 3.0), _case("legal", 9.0)])
    assert stats["figure"]["p50"] == 2.0
    assert stats["legal"]["p50"] == 9.0


def test_the_ninetieth_percentile_is_reported_not_the_mean():
    """A mean hides the tail, and the tail is what a member waits through."""
    stats = latency_by_class([_case("figure", *([1.0] * 9), 20.0)])
    assert stats["figure"]["p90"] == 20.0
    assert stats["figure"]["max"] == 20.0


def test_a_turn_that_never_streamed_is_not_counted_as_instant():
    """`ttft` is None when the turn errored or was declined without a model
    call. Counting it as zero would make a broken run look fast."""
    stats = latency_by_class([_case("figure", 4.0, None)])
    assert stats["figure"]["n"] == 1
    assert stats["figure"]["p50"] == 4.0


def test_a_class_with_no_timings_is_left_out():
    assert latency_by_class([_case("figure", None)]) == {}


def test_nothing_run_is_no_distribution():
    assert latency_by_class([]) == {}


def test_turns_from_several_cases_in_one_class_pool():
    stats = latency_by_class([_case("figure", 1.0), _case("figure", 3.0)])
    assert stats["figure"]["n"] == 2
    assert stats["figure"]["p50"] == 3.0


def test_a_single_turn_is_its_own_every_percentile():
    stats = latency_by_class([_case("figure", 7.0)])
    assert stats["figure"] == {"p50": 7.0, "p90": 7.0, "max": 7.0, "n": 1}
