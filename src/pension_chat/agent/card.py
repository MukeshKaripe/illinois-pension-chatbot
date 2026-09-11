"""The calculation card: a figure and what it rests on, as data.

`blocks.py` renders a result as markdown inside the answer; this is its
structured twin, which the client draws as the card under an answer and as the
calculation drawer (spec §5). The drawer's worked steps, comparison and
assumptions are the very blocks `prepare_answer` rendered -- carried, not
re-derived -- so the answer and the drawer cannot disagree.

Pure: a function of the turn's state and sheet. Whether a card is shown at all
is the caller's decision; `graph.py` withholds it when verification replaced the
answer, because the figures beside an unverified answer are unverified too.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pension_chat.agent import blocks
from pension_chat.agent.worksheet import WorkSheet

#: The computed blocks the calculation drawer shows, in order.
DRAWER_BLOCKS = ("WORKED_STEPS", "COMPARISON", "ASSUMPTIONS")

#: The source of an input the sheet holds no fact for: the engine computed it
#: from others -- final average salary from the salary history, most often.
SOURCE_DERIVED = "derived"


def calculation_card(state: Mapping[str, Any], sheet: WorkSheet) -> dict[str, Any] | None:
    """The first successful result as a card, or None when nothing computed."""
    for entry in (state.get("calculation_result") or {}).values():
        base = (entry or {}).get("base") or {}
        if not base.get("ok"):
            continue

        inputs = []
        for name, value in (base.get("inputs_used") or {}).items():
            fact = sheet.fact(name)
            inputs.append(
                {
                    "name": name,
                    "label": blocks._label(name),
                    "value": value,
                    "source": fact.source if fact else SOURCE_DERIVED,
                }
            )

        computed = state.get("answer_blocks") or {}
        return {
            "type": "card",
            "formula_name": entry.get("formula_name"),
            "section": entry.get("section"),
            "subsection": entry.get("subsection"),
            # Only an approved recipe may present a figure as anything but an
            # estimate (architecture doc §7.2).
            "estimate": entry.get("execution_policy") != "authoritative",
            "annual_amount": base.get("annual_amount"),
            "monthly_amount": base.get("monthly_amount"),
            "inputs": inputs,
            "scenarios": [
                {
                    "label": scenario.get("label"),
                    "annual_amount": scenario.get("annual_amount"),
                    "monthly_amount": scenario.get("monthly_amount"),
                }
                for scenario in entry.get("scenarios") or []
                if scenario.get("annual_amount")
            ],
            "blocks": {slot: computed[slot] for slot in DRAWER_BLOCKS if computed.get(slot)},
        }
    return None
