"""Which calculation inputs may be filled in, and on what basis.

An input is either known or it is not, and until now "not" was terminal: a
member who could not supply one value got no figure at all. This table is the
narrow exception. It may fill in things about *timing and framing* -- when a
member means by "retire now", where in the year an unstated birthday falls --
and it may never fill in things about who the member is or what they earned.
Assuming a salary or a service figure does not estimate a member's pension; it
invents a member.

Every assumable rule also carries the words that count as disclosing it, so the
guardrail can check that the answer said so rather than trusting that it did.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

#: Inputs that must be asked for, never filled in. Listed explicitly so the
#: absence of a rule is not the only thing standing between a member and a
#: fabricated salary.
NEVER_ASSUMABLE = frozenset(
    {
        "article_number",
        "employment_status",
        "tier",
        "final_average_salary",
        "highest_average_annual_salary",
        "highest_average_annual_salary_4yr",
        "highest_average_annual_salary_5yr",
        "highest_average_annual_salary_8yr",
        "fas",
        "service_years",
        "years_of_service",
        "creditable_service_years",
        "total_service_years",
        "current_annual_salary",
        "salary_history",
    }
)


@dataclass(frozen=True)
class AssumptionRule:
    """What may be assumed about one input, and how to say so."""

    name: str
    assumable: bool
    basis: str
    disclosure_cues: tuple[str, ...] = ()
    default: Callable[[Mapping[str, Any]], Any] | None = None
    plausible: Callable[[Mapping[str, Any]], tuple[Any, Any] | None] | None = None


def _today(known: Mapping[str, Any]) -> Any:
    return known.get("as_of_date")


def _months_before_sixty(known: Mapping[str, Any]) -> Any:
    """Whole months from a mid-year birthday to age 60.

    Knowing someone is 56 does not say where in the year their birthday falls,
    so the honest default sits in the middle of the range the statement allows
    and the spread is reported alongside it. Picking a point silently is what
    turns "age 56" into a confident 44 that nobody can check.
    """
    age = known.get("age")
    if age is None:
        return None
    months = (60 - int(age)) * 12
    if months <= 0:
        return 0
    return months - 6


def _months_before_sixty_range(known: Mapping[str, Any]) -> tuple[Any, Any] | None:
    age = known.get("age")
    if age is None:
        return None
    months = (60 - int(age)) * 12
    if months <= 0:
        return None
    # Birthday on the last day of the coming year: one month short of the full
    # count. Birthday today: the full count.
    return (months - 11, months)


POLICY: dict[str, AssumptionRule] = {
    "withdrawal_date": AssumptionRule(
        name="withdrawal_date",
        assumable=True,
        basis="the date you stop working, taken as today",
        disclosure_cues=("retire now", "as of today", "stop working now", "retiring today", "stopping work today"),
        default=_today,
        plausible=lambda known: (_today(known), _today(known)) if _today(known) else None,
    ),
    "annuity_start_date": AssumptionRule(
        name="annuity_start_date",
        assumable=True,
        basis="the date your annuity starts, taken as the date you stop working",
        disclosure_cues=("starts when you stop", "begins now", "starts today", "begins today"),
        default=_today,
        plausible=lambda known: (_today(known), _today(known)) if _today(known) else None,
    ),
    "months_before_age_60": AssumptionRule(
        name="months_before_age_60",
        assumable=True,
        basis="your birthday falling mid-year, because your date of birth is not on file",
        disclosure_cues=("date of birth", "your birthday", "were born"),
        default=_months_before_sixty,
        plausible=_months_before_sixty_range,
    ),
}

for _name in NEVER_ASSUMABLE:
    POLICY.setdefault(
        _name,
        AssumptionRule(
            name=_name,
            assumable=False,
            basis="",
        ),
    )
del _name


def rule_for(name: str) -> AssumptionRule | None:
    return POLICY.get(name)


def is_assumable(name: str) -> bool:
    rule = POLICY.get(name)
    return bool(rule and rule.assumable)


def default_for(name: str, known: Mapping[str, Any]) -> Any:
    """The value to fill in, or ``None`` when this input must be asked for."""
    rule = POLICY.get(name)
    if rule is None or not rule.assumable or rule.default is None:
        return None
    return rule.default(known)


def plausible_range(name: str, known: Mapping[str, Any]) -> tuple[Any, Any] | None:
    rule = POLICY.get(name)
    if rule is None or not rule.assumable or rule.plausible is None:
        return None
    return rule.plausible(known)
