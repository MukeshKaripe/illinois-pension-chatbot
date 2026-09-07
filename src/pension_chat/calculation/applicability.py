"""Deterministic evaluation of a recipe's statutory applicability predicates.

A recipe used to declare who it applied to in one sentence of prose
("Applies to this Tier 1 employee withdrawing at age 56 with 21.5 years of
service..."). Prose cannot be checked. It is written once, at derivation time,
against one member's facts — and then the recipe is reused for a different
member whose facts nothing re-tests. A recipe derived for a 56-year-old with
21.5 years of service would have been handed unchanged to a 47-year-old with 9,
who cannot reach the provision at all, and every downstream check would have
passed: the JSON is well formed, the rates are cited, the inputs resolve.

So the derivation also emits the provision's entitlement conditions as
machine-evaluable predicates over the member-fact vocabulary, and this module
decides whether they hold. The rule the architecture turns on applies here as
much as to arithmetic: the model states the condition, code decides whether it
is met.

Evaluation is three-valued. A predicate over a fact nobody supplied is
``unknown``, not false — an anonymous user who never stated their hire date has
not thereby failed an eligibility test. Only a predicate that is *known to be
false* blocks a calculation.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

logger = logging.getLogger("pension_chat.calculation.applicability")

# Comparison operators a predicate may use. Deliberately small: these are
# eligibility conditions ("20 or more years of service", "withdrawal on or after
# January 1, 2002"), not a general expression language.
NUMERIC_OPS = {"eq", "ne", "gt", "ge", "lt", "le"}
SET_OPS = {"in", "not_in"}
RANGE_OPS = {"between"}
BOOL_OPS = {"is_true", "is_false"}
VALID_OPERATORS = NUMERIC_OPS | SET_OPS | RANGE_OPS | BOOL_OPS

# Names a derivation may reasonably pick for a fact that facts.py publishes
# under a different one. Anything still unmatched evaluates to unknown rather
# than being guessed at.
_FACT_ALIASES = {
    "attained_age": "age",
    "age_at_withdrawal": "age",
    "age_at_annuity_start": "age",
    "age_at_commencement": "age",
    "current_age": "age",
    "years_of_service": "service_years",
    "creditable_service": "service_years",
    "creditable_service_years": "service_years",
    "total_service_years": "service_years",
    "service": "service_years",
    "months_before_60": "months_before_age_60",
    "full_months_under_age_60": "months_before_age_60",
    "date_of_withdrawal": "withdrawal_date",
    "final_average_salary": "final_average_salary",
    "fas": "final_average_salary",
    "status": "employment_status",
    "article": "article_number",
}

# Status values, ordered from most to least trustworthy.
VERIFIED = "verified"
PARTIALLY_VERIFIED = "partially_verified"
NOT_SATISFIED = "not_satisfied"
UNVERIFIED = "unverified"


class ApplicabilityReport:
    """The outcome of checking one recipe's predicates against one member."""

    def __init__(
        self,
        status: str,
        satisfied: list[dict],
        failed: list[dict],
        unknown: list[dict],
    ):
        self.status = status
        self.satisfied = satisfied
        self.failed = failed
        self.unknown = unknown

    @property
    def blocks_execution(self) -> bool:
        """True only when a condition is known to be unmet.

        An unverifiable condition does not block. The member is told the check
        could not be completed; they are not refused an estimate because the
        model named a fact the record does not carry.
        """
        return self.status == NOT_SATISFIED

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "satisfied": self.satisfied,
            "failed": self.failed,
            "unknown": self.unknown,
        }

    def summary(self) -> str:
        if self.status == UNVERIFIED:
            return "no machine-checkable applicability conditions were declared"
        if self.status == NOT_SATISFIED:
            return "; ".join(
                f"{c['description']} (member: {c['actual']})" for c in self.failed
            )
        if self.status == PARTIALLY_VERIFIED:
            return (
                f"{len(self.satisfied)} condition(s) confirmed, "
                f"{len(self.unknown)} could not be checked against the record"
            )
        return f"all {len(self.satisfied)} statutory conditions confirmed"


def normalize_fact_name(name: Any) -> str:
    key = str(name or "").strip().lower()
    return _FACT_ALIASES.get(key, key)


def validate_predicate(predicate: Any) -> str | None:
    """Return a reason the predicate is malformed, or ``None`` if it is usable."""
    if not isinstance(predicate, dict):
        return "predicate is not an object"

    fact = predicate.get("fact")
    if not fact or not isinstance(fact, str):
        return "predicate names no fact"

    operator = str(predicate.get("operator") or "").strip().lower()
    if operator not in VALID_OPERATORS:
        return f"operator '{predicate.get('operator')}' is not supported"

    if operator in SET_OPS and not isinstance(predicate.get("value"), list):
        return f"operator '{operator}' requires a list value"
    if operator in RANGE_OPS:
        value = predicate.get("value")
        if not isinstance(value, list) or len(value) != 2:
            return "operator 'between' requires a two-element [low, high] value"
    if operator in NUMERIC_OPS and predicate.get("value") is None:
        return f"operator '{operator}' requires a value to compare against"

    source = predicate.get("source") or {}
    if not isinstance(source, dict) or not source.get("section"):
        return "predicate cites no statute section"

    return None


def evaluate_predicates(
    predicates: list[dict] | None,
    facts: dict[str, Any] | None,
) -> ApplicabilityReport:
    """Check every declared condition against what is known about the member.

    Args:
        predicates: The recipe's ``applicability_predicates``.
        facts: Member facts, plus any inputs the member stated themselves.

    Returns:
        An ``ApplicabilityReport``. ``status`` is ``not_satisfied`` if any
        predicate is known false, ``partially_verified`` if some fact was not
        available, ``verified`` if every predicate held, and ``unverified``
        when the recipe declared none.
    """
    predicates = [p for p in (predicates or []) if isinstance(p, dict)]
    if not predicates:
        return ApplicabilityReport(UNVERIFIED, [], [], [])

    lookup = _normalized_facts(facts or {})

    satisfied: list[dict] = []
    failed: list[dict] = []
    unknown: list[dict] = []

    for predicate in predicates:
        problem = validate_predicate(predicate)
        record = {
            "fact": predicate.get("fact"),
            "operator": predicate.get("operator"),
            "value": predicate.get("value"),
            "description": predicate.get("description")
            or _describe(predicate),
            "source": predicate.get("source") or {},
        }
        if problem:
            # A malformed predicate proves nothing either way. The validator
            # reports it separately; here it simply cannot be checked.
            unknown.append({**record, "reason": problem})
            continue

        name = normalize_fact_name(predicate["fact"])
        if name not in lookup:
            unknown.append(
                {**record, "reason": f"'{predicate['fact']}' is not known for this member"}
            )
            continue

        actual = lookup[name]
        outcome = _compare(actual, str(predicate["operator"]).lower(), predicate.get("value"))
        record["actual"] = _render(actual)
        if outcome is None:
            unknown.append({**record, "reason": "values are not comparable"})
        elif outcome:
            satisfied.append(record)
        else:
            failed.append(record)

    if failed:
        status = NOT_SATISFIED
    elif unknown:
        status = PARTIALLY_VERIFIED
    else:
        status = VERIFIED

    return ApplicabilityReport(status, satisfied, failed, unknown)


def _normalized_facts(facts: dict[str, Any]) -> dict[str, Any]:
    """Index facts under both their own names and their canonical aliases."""
    lookup: dict[str, Any] = {}
    for key, value in facts.items():
        if value is None:
            continue
        plain = str(key).strip().lower()
        lookup.setdefault(plain, value)
        lookup.setdefault(normalize_fact_name(plain), value)
    return lookup


def _compare(actual: Any, operator: str, expected: Any) -> bool | None:
    """Three-valued comparison: True, False, or None when not comparable."""
    if operator == "is_true":
        return bool(actual)
    if operator == "is_false":
        return not bool(actual)

    if operator in SET_OPS:
        options = {_canonical(v) for v in (expected or [])}
        present = _canonical(actual) in options
        return present if operator == "in" else not present

    if operator == "between":
        low, high = _coerce(expected[0]), _coerce(expected[1])
        value = _coerce(actual)
        if value is None or low is None or high is None:
            return None
        # A number bounded by dates (or the reverse) is not a false condition,
        # it is an incoherent one; ordering them would raise rather than answer.
        if not (type(low) is type(high) is type(value)):
            return None
        return low <= value <= high  # type: ignore[operator]

    left, right = _coerce(actual), _coerce(expected)
    if left is None or right is None:
        # Fall back to string equality so a predicate over a status value
        # ("employment_status eq active") still works.
        if operator in {"eq", "ne"}:
            equal = _canonical(actual) == _canonical(expected)
            return equal if operator == "eq" else not equal
        return None
    if type(left) is not type(right):
        return None

    match operator:
        case "eq":
            return left == right
        case "ne":
            return left != right
        case "gt":
            return left > right  # type: ignore[operator]
        case "ge":
            return left >= right  # type: ignore[operator]
        case "lt":
            return left < right  # type: ignore[operator]
        case "le":
            return left <= right  # type: ignore[operator]
    return None


def _coerce(value: Any) -> Decimal | date | None:
    """Coerce to the comparable type the value actually represents."""
    if isinstance(value, bool):
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float, Decimal)):
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None
    if isinstance(value, str):
        text = value.strip()
        try:
            return date.fromisoformat(text)
        except ValueError:
            pass
        try:
            return Decimal(text)
        except InvalidOperation:
            return None
    return None


def _canonical(value: Any) -> str:
    return str(value).strip().lower()


def _render(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _describe(predicate: dict) -> str:
    words = {
        "ge": "at least", "gt": "more than", "le": "at most", "lt": "less than",
        "eq": "equal to", "ne": "not equal to", "in": "one of",
        "not_in": "not one of", "between": "between",
        "is_true": "is true", "is_false": "is false",
    }
    operator = str(predicate.get("operator") or "").lower()
    phrase = words.get(operator, operator)
    if operator in BOOL_OPS:
        return f"{predicate.get('fact')} {phrase}"
    return f"{predicate.get('fact')} {phrase} {predicate.get('value')}"
