"""Boundaries the member is approaching, read out of the recipe that names them.

The Code is a field of cliffs. Section 11-134 reduces an annuity by 0.25% for
each month it begins before age 60, so a member of 56 is carrying an 11%
reduction that vanishes on a date nobody told them about. The recipe has always
known: its ``parameters`` name ``age_60`` and ``minimum_service_years`` with the
subsection each came from, and its applicability predicates name the rest.

Nothing here computes a benefit. It finds the boundary; ``execute_calculations``
prices it through the same interpreter as every other figure, which is what
makes a cliff figure exactly as trustworthy as the one beside it.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

#: Which member fact each boundary is about. A parameter named for a fact is a
#: boundary on that fact; anything else -- a rate, a cap, a percentage -- is an
#: input to the arithmetic rather than a date the member is walking towards.
#: Listed rather than pattern-matched, because "maximum_salary_percentage" looks
#: exactly like a boundary to any rule general enough to catch the real ones.
_BOUNDARY_FACTS = {
    "age_55": "age",
    "age_60": "age",
    "age_62": "age",
    "age_65": "age",
    "age_67": "age",
    "minimum_age": "age",
    "minimum_service_years": "service_years",
    "minimum_service": "service_years",
    "maximum_service_years": "service_years",
}

#: How many months one unit of each fact is worth. Both of these advance with
#: the calendar for a member who stays in service, which is what makes a
#: distance in months meaningful for either.
_MONTHS_PER_UNIT = {"age": 12, "service_years": 12}

#: Past this, a boundary is not news a member can act on.
_DEFAULT_HORIZON_MONTHS = 72


@dataclass(frozen=True)
class Threshold:
    """One boundary, and how far the member is from it."""

    fact: str
    boundary: Any
    current: Any
    distance_months: int
    section: str | None
    subsection: str | None
    description: str

    def to_dict(self) -> dict:
        return {
            "fact": self.fact,
            "boundary": self.boundary,
            "current": self.current,
            "distance_months": self.distance_months,
            "section": self.section,
            "subsection": self.subsection,
            "description": self.description,
        }


def _number(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _plain(value: Decimal) -> Any:
    return int(value) if value == value.to_integral_value() else float(value)


def find_thresholds(
    spec: dict,
    facts: dict,
    *,
    horizon_months: int = _DEFAULT_HORIZON_MONTHS,
) -> list[Threshold]:
    """Boundaries this member is approaching, nearest first.

    A boundary already passed is not a cliff, and one beyond the horizon is not
    news they can act on -- both are dropped rather than reported quietly. The
    same boundary named by both a parameter and a predicate is reported once.
    """
    found: dict[tuple[str, str], Threshold] = {}

    def consider(fact: str, boundary: Any, source: Any, description: str) -> None:
        current = _number(facts.get(fact))
        target = _number(boundary)
        if current is None or target is None or target <= current:
            return
        per_unit = _MONTHS_PER_UNIT.get(fact)
        if per_unit is None:
            return
        # Prefer the exact count the facts layer already computed. A member of
        # 56 is 56 and some months, so (60 - 56) * 12 says 48 where their real
        # distance is 44 -- and 44 is the number the reduction is built on
        # (44 x 0.25% = the 11% they are carrying). Rounding the member's age
        # down and then multiplying quietly overstates every age cliff.
        exact = _number(facts.get(f"months_before_age_{_plain(target)}")) if fact == "age" else None
        if exact is not None and exact > 0:
            distance = int(exact)
        else:
            distance = int((target - current) * per_unit)
        if distance <= 0 or distance > horizon_months:
            return
        key = (fact, str(target))
        if key in found:
            return
        source = source if isinstance(source, dict) else {}
        found[key] = Threshold(
            fact=fact,
            boundary=_plain(target),
            current=_plain(current),
            distance_months=distance,
            section=source.get("section"),
            subsection=source.get("subsection"),
            description=description,
        )

    for name, parameter in (spec.get("parameters") or {}).items():
        fact = _BOUNDARY_FACTS.get(name)
        if fact is None or not isinstance(parameter, dict):
            continue
        consider(
            fact,
            parameter.get("value"),
            parameter.get("source"),
            f"{str(name).replace('_', ' ')} under the controlling provision",
        )

    for predicate in spec.get("applicability_predicates") or []:
        if not isinstance(predicate, dict):
            continue
        # Only a lower bound is a cliff to walk towards. An upper bound is one
        # the member is walking *away* from, and an equality is not a distance.
        if predicate.get("operator") not in {"ge", "gt", ">=", ">"}:
            continue
        fact = str(predicate.get("fact") or "")
        if fact not in _MONTHS_PER_UNIT:
            continue
        consider(
            fact,
            predicate.get("value"),
            predicate.get("source"),
            str(predicate.get("description") or fact),
        )

    return sorted(found.values(), key=lambda t: t.distance_months)
