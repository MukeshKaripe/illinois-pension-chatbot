"""Deterministic member facts derived from the employee record.

Everything here is arithmetic on database values — no model involvement. The
first implementation hardcoded ``age = 55`` with a ``# Mock age calc`` comment,
which silently produced wrong eligibility for every member.

A derived recipe declares the inputs it needs by name, and different derivations
of the same rule pick different names for the same quantity (``months_before_60``
vs ``months_before_age_60`` vs ``age_at_annuity_start``). Rather than force one
vocabulary on the model, this module publishes the canonical facts under a
generous set of aliases; anything still unresolved falls through to the
extraction step, and anything unresolved after that gates execution.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import Any

logger = logging.getLogger("pension_chat.agent.facts")


def age_on(birth_date: date, on: date) -> int:
    """Whole years completed on ``on``."""
    years = on.year - birth_date.year
    if (on.month, on.day) < (birth_date.month, birth_date.day):
        years -= 1
    return years


def months_between(start: date, end: date) -> int:
    """Whole months from ``start`` to ``end``; negative if ``end`` precedes it."""
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
    return months


def nth_birthday(birth_date: date, age: int) -> date:
    """The date the member turns ``age``."""
    try:
        return birth_date.replace(year=birth_date.year + age)
    except ValueError:  # 29 February
        return birth_date.replace(year=birth_date.year + age, day=28)


# Tier 2 is Section 1-160's "new hires" rule, and it names Articles 8 through 12
# outright. The date is the whole test.
TIER_2_FROM = date(2011, 1, 1)


def derive_tier(first_participation: date) -> int:
    """Which tier a member falls in, from the date they first participated.

    Tier is never asked for. It follows from one date, the `CHECK (tier IN
    (1,2))` on the column means a wrong answer is silently *accepted*, and tier
    selects the accrual rule via Section 1-160 as well as keying the recipe
    registry lookup — so a wrong tier is a wrong pension with nothing on screen
    to say so.

    The approximation worth being honest about: the statute turns on first
    participation in *any* covered system, not on the hire date into this one. A
    member with pre-2011 reciprocal service who joined this fund in 2015 is Tier
    1, and a hire date alone gets them wrong.
    """
    return 2 if first_participation >= TIER_2_FROM else 1


def highest_average_salary(
    salary_history: list[tuple[date, Decimal]],
    consecutive_years: int,
    window_years: int,
    as_of: date,
) -> Decimal | None:
    """Highest average annual salary over N consecutive years in the last M.

    Article 11's minimum-annuity formulas are written on "the highest average
    annual salary for any 4 consecutive years within the last 10 years of
    service immediately preceding the date of withdrawal", so this computes a
    rolling mean over the eligible window rather than simply averaging the most
    recent years.

    Returns ``None`` when the history does not cover enough years to compute the
    average the statute asks for — a missing input, not a zero.
    """
    if not salary_history or consecutive_years <= 0:
        return None

    cutoff = date(as_of.year - window_years, as_of.month, as_of.day)
    eligible = sorted(
        ((d, Decimal(str(s))) for d, s in salary_history if d >= cutoff),
        key=lambda row: row[0],
    )
    if len(eligible) < consecutive_years:
        return None

    best: Decimal | None = None
    for start in range(len(eligible) - consecutive_years + 1):
        window = eligible[start : start + consecutive_years]
        average = sum(s for _, s in window) / Decimal(consecutive_years)
        if best is None or average > best:
            best = average
    return best


def _rate_label(rate: Decimal | float | str) -> str:
    """A growth rate as a member would say it: 0.03 -> "3%"."""
    percent = (Decimal(str(rate)) * 100).normalize()
    return f"{percent:f}%"


def _status_at(on_record: str | None) -> str | None:
    """The member's employment status at the annuity start date.

    A retirement annuity calculation posits withdrawal -- that is what
    `withdrawal_date` means, and it is published on every turn. So a member the
    record calls active is, at the date the figure is for, separated. Anything
    else the record says is already a statement about a member who has stopped,
    and is left alone.
    """
    return "separated" if on_record == "active" else on_record


def build_member_facts(
    employee: dict[str, Any] | None,
    salary_history: list[tuple[date, Decimal]] | None = None,
    *,
    today: date | None = None,
    assumed_retirement_age: int | None = None,
    salary_growth: Decimal | None = None,
) -> dict[str, Any]:
    """Compute every member fact a recipe might reference.

    Args:
        employee: The employee record dict, or ``None`` for an anonymous user.
        salary_history: ``(effective_date, annual_salary)`` rows.
        today: Overridable for deterministic tests.
        assumed_retirement_age: When set, facts are computed as of the date the
            member reaches that age instead of today — this is what powers
            "what changes when I reach 60" scenarios.
        salary_growth: Annual rate to carry salary forward by when projecting,
            as a decimal (0.03 for 3%). ``None`` holds salary flat, which is
            the honest default: a rate the member has not stated is invented,
            not projected. Only salary moves; service and age are arithmetic on
            dates and owe nothing to pay.

    Returns:
        A flat dict of facts. Keys are aliased liberally so that a derived
        recipe's chosen input names resolve without a model call.
    """
    today = today or date.today()
    if not employee:
        return {"as_of_date": today.isoformat()}

    birth_date = _as_date(employee.get("birth_date"))
    hire_date = _as_date(employee.get("hire_date"))
    if birth_date is None:
        return {"as_of_date": today.isoformat()}

    service_years = Decimal(str(employee.get("total_service_years") or 0))

    if assumed_retirement_age is not None:
        # Clamped to today: a member who is already 56 asking about "retiring
        # now at 56" means now, not the day they turned 56. Without the clamp
        # the same question produced two different figures in one answer — the
        # base calculation as of today ($32,126.78) and a "retire now" scenario
        # as of the past birthday ($31,765.80), which differ because the months
        # remaining before age 60 differ.
        as_of = max(nth_birthday(birth_date, assumed_retirement_age), today)
        # Service keeps accruing while the member stays in service.
        if employee.get("employment_status") == "active" and as_of > today:
            extra_years = Decimal(str((as_of - today).days)) / Decimal("365.25")
            service_years = service_years + extra_years
    else:
        as_of = today

    current_age = age_on(birth_date, as_of)
    age_60_date = nth_birthday(birth_date, 60)
    age_55_date = nth_birthday(birth_date, 55)
    months_before_60 = max(0, months_between(as_of, age_60_date))

    facts: dict[str, Any] = {
        "as_of_date": as_of.isoformat(),
        "birth_date": birth_date.isoformat(),
        "hire_date": hire_date.isoformat() if hire_date else None,
        "article_number": employee.get("article_number"),
        "tier": employee.get("tier"),
        # Every other fact here describes the member at `as_of` -- service
        # accrues to it, age is taken at it, `withdrawal_date` and
        # `annuity_start_date` *are* it. This one passed the record's value
        # straight through, so a derivation reading §3-111(a) ("no longer in
        # service") against an active record concluded, correctly, that the
        # provision does not reach them -- on a question that begins "if I
        # retired". Article 11's recipe never tests status, which is why it
        # stayed invisible; Articles 3, 4 and 14 all derive predicates that do,
        # and CLAUDE.md already records the same defect for Article 19's "attains
        # an age of 65".
        #
        # It rests on the same framing default `withdrawal_date` does, and is
        # disclosed the same way: the member is taken to stop working at
        # `as_of`. The record's own value keeps a name, because a provision
        # about being *in* service (§11-128, "employees who attain age 65 in
        # service") has to be able to test it.
        "employment_status": _status_at(employee.get("employment_status")),
        "employment_status_on_record": employee.get("employment_status"),
        "current_annual_salary": _num(employee.get("current_annual_salary")),
        # Age, under the names a derivation is likely to pick
        "age": current_age,
        "current_age": current_age,
        "age_at_annuity_start": current_age,
        "age_at_withdrawal": current_age,
        "attained_age": current_age,
        # Service
        "service_years": _num(service_years),
        "years_of_service": _num(service_years),
        "total_service_years": _num(service_years),
        "creditable_service_years": _num(service_years),
        # Dates a date_diff operation may reference
        "withdrawal_date": as_of.isoformat(),
        "annuity_start_date": as_of.isoformat(),
        "age_55_date": age_55_date.isoformat(),
        "age_60_date": age_60_date.isoformat(),
        # The early-retirement reduction in Article 11 is stated per month
        # before age 60, so publish that count directly.
        "months_before_age_60": months_before_60,
        "months_before_60": months_before_60,
        "full_months_under_age_60": months_before_60,
    }
    for alias in aliases_of("current_annual_salary"):
        facts[alias] = facts["current_annual_salary"]
    # A record that holds no service figure (PBMS stores one for few MEABF
    # members) leaves service unknown -- a missing input to ask for -- rather
    # than publishing 0 years under the record's name.
    if employee.get("total_service_years") is None:
        for alias in aliases_of("total_service_years"):
            facts.pop(alias, None)
    # Facts only some funds have (`funds.Fund.extra_fields`), under every name a
    # derivation might give them, as the ones above are.
    for name, value in (employee.get("extra_facts") or {}).items():
        for alias in aliases_of(name):
            facts[alias] = value

    # Salary carried forward to the projection date, when the member supplied a
    # rule for it. Every row is compounded by the years between its own date
    # and `as_of`, so the 4-consecutive-year window is taken over projected pay
    # rather than over today's. The disclosure is set beside the number, not by
    # whoever renders it, so the two cannot drift apart.
    projected_history = salary_history
    if salary_history and salary_growth and as_of > today:
        rate = Decimal("1") + Decimal(str(salary_growth))
        projected_history = [
            (
                row_date,
                Decimal(str(amount))
                * (rate ** (Decimal(str((as_of - row_date).days)) / Decimal("365.25"))),
            )
            for row_date, amount in salary_history
        ]
        facts["salary_basis"] = (
            f"carried forward at {_rate_label(salary_growth)} a year, as you said"
        )
    elif assumed_retirement_age is not None:
        facts["salary_basis"] = (
            "carried forward at today's level; future pay rises are not projected"
        )

    if projected_history:
        for consecutive in (4, 5, 8):
            average = highest_average_salary(
                projected_history, consecutive, window_years=10, as_of=as_of
            )
            if average is None:
                continue
            value = _num(average)
            facts[f"highest_average_annual_salary_{consecutive}yr"] = value
            if consecutive == 4:
                # 4 consecutive years is the Article 11 default for withdrawals
                # under the post-1988 formulas, so it also answers the generic
                # "final average salary" input name.
                facts["final_average_salary"] = value
                facts["highest_average_annual_salary"] = value
                facts["fas"] = value

    return {k: v for k, v in facts.items() if v is not None}


#: Names that mean the same quantity. ``build_member_facts`` publishes each
#: canonical value under several aliases, because different derivations of the
#: same rule pick different names for it -- so a member correcting
#: ``service_years`` has to invalidate a figure that was built from
#: ``creditable_service_years``. Grouped here, beside the facts themselves, so
#: the groups cannot drift from the names actually published.
ALIAS_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({
        "age", "current_age", "age_at_annuity_start", "age_at_withdrawal",
        "attained_age",
    }),
    frozenset({
        "service_years", "years_of_service", "total_service_years",
        "creditable_service_years",
    }),
    frozenset({
        "months_before_age_60", "months_before_60", "full_months_under_age_60",
    }),
    frozenset({
        "final_average_salary", "highest_average_annual_salary",
        "highest_average_annual_salary_4yr", "fas",
    }),
    frozenset({"withdrawal_date", "annuity_start_date", "as_of_date"}),
    # §3-111 and §4-109 pay on "the salary attached to the rank", which on a
    # record is the current salary rather than an average.
    frozenset({"current_annual_salary", "salary_attached_to_rank"}),
    # §14-108's accrual rate turns on whether a state employee is covered by
    # Social Security (`funds.SOCIAL_SECURITY`).
    frozenset({
        "social_security_covered", "covered_by_social_security",
        "is_covered_employee", "covered_employee",
    }),
)


#: Keys published above that are *framing choices*, not facts about the member.
#: No file records when somebody will stop working; "today" is the reading this
#: system takes of a bare "what would I get", and a figure resting on it has to
#: say so -- a member whose pension grows with every further year of service
#: needs to know which year was used. They live here, beside the names actually
#: emitted, for the same reason `ALIAS_GROUPS` does: a list kept anywhere else
#: drifts from the dictionary it describes.
#:
#: `as_of_date` is deliberately absent. Today's date is not an assumption.
#: Values the facts layer supplies so the arithmetic has something to run on,
#: rather than values anybody stated. Each is disclosed as an assumption.
#:
#: `employment_status` is deliberately **not** among them, though it is derived
#: from the same supposition. It is not a second guess: "you have stopped
#: working" is what "the date you stop working, taken as today" already says,
#: and `render_assumptions` exists to keep one choice from being disclosed as
#: two separate ones. Adding it produced exactly that -- an answer that
#: disclosed the withdrawal date, said nothing about the status derived from
#: it, and was thrown away for ASSUMPTION_NOT_DISCLOSED.
FRAMING_DEFAULTS: frozenset[str] = frozenset({"withdrawal_date", "annuity_start_date"})


def aliases_of(name: str) -> frozenset[str]:
    """Every name that means the same quantity as ``name``, including itself."""
    for group in ALIAS_GROUPS:
        if name in group:
            return group
    return frozenset({name})


def _as_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
