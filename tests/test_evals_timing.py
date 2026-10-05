from __future__ import annotations

import pytest

from pension_chat.evals.timing import estimate_in_region, stage_breakdown
from pension_chat.evals.trace import TurnTrace

PROBE = {"openai": {"cold": 0.9, "warm": 0.25}, "search": {"cold": 0.8, "warm": 0.25},
         "postgres": {"cold": 1.5, "warm": 0.25}}


def _t(calls, ttft=10.0, wait=0.0) -> TurnTrace:
    return TurnTrace(case_id="c", turn_index=0, question="q", employee=None,
                     ttft=ttft, ttft_rate_limit_wait=wait,
                     calls=[{"kind": k, "label": "", "start": s, "end": e} for k, s, e in calls])


def test_sequential_calls_each_lose_their_network_overhead() -> None:
    t = _t([("search", 0.0, 1.0), ("model", 1.0, 3.0)])
    assert estimate_in_region(t, PROBE) == pytest.approx(10.0 - 2 * 0.248)


def test_parallel_calls_count_once_at_the_largest_overhead() -> None:
    t = _t([("search", 0.0, 1.0), ("embedding", 0.1, 0.6)])
    assert estimate_in_region(t, PROBE) == pytest.approx(10.0 - 0.248)


def test_a_new_connection_loses_its_setup() -> None:
    t = _t([("postgres_connect", 0.0, 1.5)])
    assert estimate_in_region(t, PROBE) == pytest.approx(10.0 - (1.5 - 0.006))


def test_calls_after_the_first_token_do_not_count() -> None:
    t = _t([("model", 11.0, 12.0)])
    assert estimate_in_region(t, PROBE) == pytest.approx(10.0)


def test_rate_limit_waits_come_off_too() -> None:
    assert estimate_in_region(_t([], wait=1.5), PROBE) == pytest.approx(8.5)


def test_no_first_token_means_no_estimate() -> None:
    assert estimate_in_region(_t([], ttft=None), PROBE) is None


def test_stage_breakdown_counts_overlap_once_and_names_the_gap() -> None:
    t = _t([("search", 0.0, 1.0), ("embedding", 0.5, 1.5), ("model", 2.0, 5.0)], ttft=6.0)
    stages = stage_breakdown(t)
    assert stages["model"] == pytest.approx(3.0)
    assert stages["search"] == pytest.approx(1.0)
    assert stages["unattributed"] == pytest.approx(6.0 - 4.5)   # union of [0,1.5] and [2,5] is 4.5 s


def test_pbms_calls_count_as_pbms_round_trips():
    from pension_chat.evals.timing import KIND_ENDPOINT
    assert KIND_ENDPOINT["pbms"] == "pbms"
