"""The scenario chip shows what-ifs the member built, and nothing else (spec §7).

Every calculation files its unscenarioed figure in `scenario_set` under the
"retire now" label, and a corrected scenario keeps its old figure under a
"(before the correction ...)" label. Both belong in the checkpoint; neither is a
what-if the member set up, and showing them would put a chip on every figure.
"""

from __future__ import annotations

from pension_chat.agent.graph import NOW_LABEL, active_scenarios


def test_the_chip_shows_only_the_members_own_scenarios() -> None:
    scenario_set = {
        NOW_LABEL: {"annual_amount": "32126.78", "monthly_amount": "2677.23"},
        "at age 60": {"annual_amount": "36097.50", "monthly_amount": "3008.13"},
        "at age 60 (before the correction on turn 2)": {"annual_amount": "35000.00"},
    }
    assert active_scenarios(scenario_set) == [
        {"label": "at age 60", "annual_amount": "36097.50", "monthly_amount": "3008.13"}
    ]


def test_no_scenarios_no_chip() -> None:
    assert active_scenarios({}) == []
    assert active_scenarios({NOW_LABEL: {"annual_amount": "1.00"}}) == []
