"""Which calculation inputs may be filled in, and on what basis.

An input is either known or it is not, and until now "not" was terminal: a
member who could not supply one value got no figure at all. This table is the
narrow exception. It may fill in things about *timing and framing* -- when a
member means by "retire now", where in the year an unstated birthday falls --
and it may never fill in things about who the member is or what they earned.
Assuming a salary or a service figure does not estimate a member's pension; it
invents a member.

## Disclosure contract for Task 6's guardrail

An assumption is disclosed when the answer contains BOTH:
1. A topic cue from the rule's ``disclosure_cues`` (e.g., "today", "retire", "birthday")
2. An assumption signal from ``ASSUMPTION_SIGNALS`` (e.g., "assuming", "taken as")

So "as of today your record shows 21.5 years" has a topic cue but no signal and fails
the check, while "I'm assuming you retire today" has both and passes. This two-part model
keeps topic cues broad (matching natural prose) while requiring an explicit intent marker.
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
        "service_years",
        "years_of_service",
        "creditable_service_years",
        "total_service_years",
        "current_annual_salary",
        "salary_history",
    }
)

#: Words that mark a sentence as disclosing an assumption rather than merely
#: mentioning the topic. Used with topic cues to verify that an assumption was
#: actually stated, not just that the topic came up.
ASSUMPTION_SIGNALS = frozenset(
    {
        "assum",      # assume, assumed, assuming, assumption, assumes
        "taken as",
        "treating",
        "unless you",
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
    #: True when ``default`` *computes* the value from facts already held
    #: rather than choosing one. A derivation invents nothing -- every value it
    #: rests on came from the member or their record -- so it does not spend
    #: the ceiling and it is not disclosed as an assumption. Disclosing it
    #: tells a member we guessed at something they told us themselves, and
    #: spending the ceiling on it can refuse them a figure for want of a slot
    #: they themselves paid for.
    derives: bool = False


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


def _stated_salary(known: Mapping[str, Any]) -> Any:
    """The annual salary the user stated, taken as their average -- or nothing.

    Only a salary the user typed (`stated_annual_salary`); never the record's
    current pay, and never a figure from nowhere.
    """
    return known.get("stated_annual_salary")


def _birth_date_from_age(known: Mapping[str, Any]) -> Any:
    """A date of birth placed mid-year from a stated age, as `_months_before_sixty` is."""
    from datetime import date, timedelta

    age, today = known.get("age"), known.get("as_of_date")
    if age is None or not today:
        return None
    try:
        on = date.fromisoformat(str(today))
        birthday = on.replace(year=on.year - int(age))
    except ValueError:
        return None
    return (birthday - timedelta(days=182)).isoformat()


def _birth_date_range(known: Mapping[str, Any]) -> tuple[Any, Any] | None:
    """Anyone aged N today was born between N+1 years ago (plus a day) and N years ago."""
    from datetime import date, timedelta

    age, today = known.get("age"), known.get("as_of_date")
    if age is None or not today:
        return None
    try:
        on = date.fromisoformat(str(today))
        latest = on.replace(year=on.year - int(age))
        earliest = on.replace(year=on.year - int(age) - 1) + timedelta(days=1)
    except ValueError:
        return None
    return earliest.isoformat(), latest.isoformat()


def _annuity_start(known: Mapping[str, Any]) -> Any:
    return known.get("withdrawal_date") or known.get("as_of_date")


#: The names a salary basis goes by. Missing-input policy (product owner,
#: 2026-09-30): salary growth is a secondary input, so a salary the user stated
#: may be taken as flat across the averaging period -- one assumption, disclosed.
SALARY_BASES = (
    "final_average_salary", "highest_average_annual_salary", "highest_average_annual_salary_4yr",
    "highest_average_annual_salary_5yr", "highest_average_annual_salary_8yr", "fas",
    "final_average_salary_8_year", "final_average_salary_96_months", "tier_2_final_average_salary",
)

POLICY: dict[str, AssumptionRule] = {
    "withdrawal_date": AssumptionRule(
        name="withdrawal_date",
        assumable=True,
        basis="the date you stop working, taken as today",
        disclosure_cues=("today", "retire", "stop working", "withdraw"),
        default=_today,
        plausible=lambda known: (_today(known), _today(known)) if _today(known) else None,
    ),
    "annuity_start_date": AssumptionRule(
        name="annuity_start_date",
        assumable=True,
        basis="the date your annuity starts, taken as the date you stop working",
        disclosure_cues=("annuity", "start", "begin", "today"),
        default=_annuity_start,
        plausible=lambda known: (_annuity_start(known), _annuity_start(known)) if _annuity_start(known) else None,
        # Follows the withdrawal date rather than making a second claim about when.
        derives=True,
    ),
    "birth_date": AssumptionRule(
        name="birth_date",
        assumable=True,
        basis="your date of birth placed mid-year from the age you gave",
        disclosure_cues=("birthday", "date of birth", "born", "age"),
        default=_birth_date_from_age,
        plausible=_birth_date_range,
        derives=True,
    ),
    "months_before_age_60": AssumptionRule(
        name="months_before_age_60",
        assumable=True,
        basis="your birthday falling mid-year, because your date of birth is not on file",
        disclosure_cues=("birthday", "date of birth", "born"),
        default=_months_before_sixty,
        plausible=_months_before_sixty_range,
        # Computed from an age the member gave. The mid-year placement is the
        # honest middle of the range their own statement allows, reported with
        # its spread -- not a value chosen in place of one they withheld.
        derives=True,
    ),
}

for _name in SALARY_BASES:
    POLICY[_name] = AssumptionRule(
        name=_name,
        assumable=True,
        basis="the salary you gave, taken as flat across the averaging period -- pay rises would raise it",
        disclosure_cues=("salary", "pay", "earn", "flat", "average"),
        default=_stated_salary,
        plausible=lambda known: (_stated_salary(known), _stated_salary(known)) if _stated_salary(known) else None,
    )

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
