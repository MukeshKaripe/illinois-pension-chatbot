"""What a member can be helped to want, and what the Code cannot tell them.

A recommendation is "selecting or highlighting an option according to an
explicit or inferable objective, supported by corpus facts and deterministic
capability outputs" (spec §11.1). The objective is what makes it checkable: "I
recommend 58 because it gives you the highest pension" is either true of the
computed figures or it is not, and arithmetic can say which.

So the vocabulary is closed. An objective drawn from free text is a sentence
about a preference; one drawn from here is a predicate over candidates that
were already priced by the interpreter. Every entry reads a field a candidate
actually carries -- ``test_objectives.py`` holds that shut, because an objective
needing something the corpus does not contain could only ever be evaluated by
inventing it.

``EXCLUDED`` is the other half of the same honesty. A member deciding when to
retire is weighing their health, their tax position and how long they expect to
live, and none of that is in 40 ILCS 5. The agent says so plainly and stops
there rather than letting a confident answer imply it was considered.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


def _amount(entry: Mapping[str, Any], field: str) -> Decimal | None:
    raw = entry.get(field)
    if raw is None:
        return None
    try:
        return Decimal(str(raw).replace(",", "").replace("$", ""))
    except (InvalidOperation, ValueError):
        return None


def _best(field: str) -> Callable[[str, Mapping[str, Mapping[str, Any]]], bool]:
    """A predicate: ``choice`` carries the largest ``field`` of any candidate.

    A candidate nobody could price refuses the whole comparison rather than
    being dropped from it. Dropping it would make the recommendation the best
    of whatever happened to compute, which is a different claim from the one
    the answer makes.

    A tie is satisfied by either: two options paying the same is not a reason
    to refuse one of them.
    """

    def satisfied(choice: str, candidates: Mapping[str, Mapping[str, Any]]) -> bool:
        if choice not in candidates:
            return False
        amounts = {name: _amount(entry, field) for name, entry in candidates.items()}
        if any(value is None for value in amounts.values()):
            return False
        mine = amounts[choice]
        return all(mine >= other for other in amounts.values() if other is not None)

    return satisfied


def _soonest(choice: str, candidates: Mapping[str, Mapping[str, Any]]) -> bool:
    """``choice`` is the first option offered.

    Order is the order the member built them in, which is the order they were
    computed and filed -- `scenario_set` is a plain dict and Python keeps
    insertion order. Reading a date out of the label would be a regex deciding
    what the member meant, which this codebase has twice recorded as a mistake.
    """
    names = list(candidates)
    return bool(names) and names[0] == choice


def _eligible(choice: str, candidates: Mapping[str, Mapping[str, Any]]) -> bool:
    """``choice`` names a provision. The legal objective, with no figure in it.

    "Which benefit option is available to me?" may be entirely legal.
    Recommendation is a reasoning capability that *may* invoke calculation, not
    calculation with a different label.
    """
    return bool((candidates.get(choice) or {}).get("section"))


@dataclass(frozen=True)
class Objective:
    """One thing a member can be helped to want, and how to check it was served."""

    name: str
    #: The candidate field this reads, so no objective can quietly depend on
    #: something the corpus does not carry. "" for an order-based objective.
    reads: str
    #: How the answer names this back to the member.
    prose: str
    satisfied_by: Callable[[str, Mapping[str, Mapping[str, Any]]], bool]


OBJECTIVES: dict[str, Objective] = {
    "highest_annual": Objective(
        name="highest_annual",
        reads="annual_amount",
        prose="the highest annual pension",
        satisfied_by=_best("annual_amount"),
    ),
    "highest_monthly": Objective(
        name="highest_monthly",
        reads="monthly_amount",
        prose="the highest monthly pension",
        satisfied_by=_best("monthly_amount"),
    ),
    "soonest": Objective(
        name="soonest",
        reads="",
        prose="leaving as soon as possible",
        satisfied_by=_soonest,
    ),
    "eligibility": Objective(
        name="eligibility",
        reads="section",
        prose="what the Code actually makes available",
        satisfied_by=_eligible,
    ),
}


#: What a member weighing this decision also cares about, and why none of it can
#: be weighed here. Named in the answer rather than omitted: a recommendation
#: that quietly leaves these out reads as though they had been considered.
EXCLUDED: dict[str, str] = {
    "life expectancy": "how long a pension is drawn for is not something the Code states",
    "tax": "the Pension Code does not set how a benefit is taxed",
    "investment return": "what savings might earn instead is outside the statute",
    "health": "nothing in your record or the Code speaks to it",
    "risk appetite": "only you can weigh how much certainty is worth to you",
}


def objective_for(name: str) -> Objective | None:
    """The objective ``name`` denotes, or None.

    None is a refusal, not an error: an objective the Code cannot evaluate
    cannot be checked, and an unchecked recommendation is the thing this layer
    exists to prevent.
    """
    return OBJECTIVES.get(name)
