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

from pension_chat import funds
from pension_chat.agent import blocks
from pension_chat.agent.facts import aliases_of
from pension_chat.agent.worksheet import Fact, WorkSheet

#: The computed blocks the calculation drawer shows, in order.
DRAWER_BLOCKS = ("WORKED_STEPS", "COMPARISON", "ASSUMPTIONS")


def _fact_for(sheet: WorkSheet, name: str) -> Fact | None:
    """The sheet's fact for an input, under the input's own name or any alias.

    Recipes name inputs in the statute's words (`highest_average_annual_salary_4yr`)
    while a member's statement lands under another name for the same quantity
    (`final_average_salary`) -- `facts.ALIAS_GROUPS` says they are one. Looking
    up the recipe's name alone labelled a salary the member stated "derived".
    """
    fact = sheet.fact(name)
    if fact is not None:
        return fact
    for alias in sorted(aliases_of(name) - {name}):
        if (fact := sheet.fact(alias)) is not None:
            return fact
    return None


def calculation_card(state: Mapping[str, Any], sheet: WorkSheet) -> dict[str, Any] | None:
    """The first successful result as a card, or None when nothing computed."""
    for entry in (state.get("calculation_result") or {}).values():
        base = (entry or {}).get("base") or {}
        if not base.get("ok"):
            continue

        origins = state.get("input_sources") or {}
        inputs = []
        for name, value in (base.get("inputs_used") or {}).items():
            fact = _fact_for(sheet, name)
            inputs.append(
                {
                    "name": name,
                    "label": blocks._label(name),
                    "value": value,
                    # The turn's own record first -- resolve_inputs knows where
                    # each value came from -- then the sheet; when neither
                    # knows, no label rather than a guess shown to the member.
                    "source": origins.get(name) or (fact.source if fact else None),
                }
            )

        # `views`, not `answer_blocks`: the latter holds only the blocks the
        # model emits by marker, and the drawer wants every table the turn
        # rendered -- including the assumptions, which the answer discloses in
        # prose and which have no marker.
        computed = state.get("views") or {}
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
            # How well the assistant knows the fund the figure was computed
            # under (`funds.py`): coverage's verdict, else the record's Article.
            "support_level": funds.support_level(
                (state.get("coverage") or {}).get("article")
                or (state.get("employee_record") or {}).get("article_number")
            ),
            # Whose record the "Record" inputs came from, when it was the fund's.
            "record_label": _record_label(state),
        }
    return None


def _record_label(state: Mapping[str, Any]) -> str | None:
    """Whose record the "Record" inputs came from -- the fund's, named without its internal key."""
    record = state.get("employee_record") or {}
    return f"{record['record_source']} record" if record.get("record_source") else None


def calculation_cards(state: Mapping[str, Any], sheet: WorkSheet) -> list[dict[str, Any]]:
    """One card per person with a figure; a one-person turn's card exactly as before."""
    people = state.get("people") or {}
    if len(people) < 2:
        card = calculation_card(state, sheet)
        return [card] if card else []
    cards: list[dict[str, Any]] = []
    done: set[str] = set()
    for entry in (state.get("calculation_result") or {}).values():
        ref = (entry or {}).get("person_ref")
        if not ref or ref in done or ref not in people or not (entry.get("base") or {}).get("ok"):
            continue
        done.add(ref)
        card = calculation_card(
            {**state, "calculation_result": {"figure": entry}, "input_sources": entry.get("input_sources") or {},
             "employee_record": people[ref]["record"]},
            sheet,
        )
        if card:
            cards.append({**card, "person": entry.get("person")})
    order = {r: i for i, r in enumerate(state.get("turn_people") or [])}
    people_order = {people[r]["label"]: order.get(r, 99) for r in people}
    return sorted(cards, key=lambda c: people_order.get(c["person"], 99))
