"""Which funds a member can have a profile under, and what each one allows.

Spec §6, Phase 3b. One entry per member fund of the Pension Code: what the
picker calls it (members know their employer, not their Article -- the whole
reason `agent/coverage.py` exists), how far each field of a profile may be
edited, and how much the assistant claims to know about the fund.

**Support level is a claim made to members, so it is set here and never
inferred** -- not from ingestion, not from a recipe happening to exist. `checked`
means the fund's smoke eval passes beside the existing benchmark; `experimental`
means statute answers are sound but a figure may be missing or less tested, and
the UI says so on the preset, the profile chip and the calculation card. That
label is what makes offering these funds honest: before it, a member filed under
a fund with no derived recipe would get "I cannot find a governing provision" to
every calculation, with nothing on screen saying why.

Deliberately imports nothing from `personas.py`: `personas.validate` reads the
bounds here, so the dependency runs one way.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

SupportLevel = Literal["checked", "experimental"]
FieldType = Literal["date", "years", "money", "status"]

EMPLOYMENT_STATUSES = ("active", "separated", "retired")

#: The fields every profile shows, in the order the form shows them. Salary
#: history is not among them: it is derived, and rescaled with the salary
#: (`personas.apply_edits`).
COMMON_FIELD_NAMES = (
    "birth_date",
    "hire_date",
    "employment_status",
    "total_service_years",
    "current_annual_salary",
)


@dataclass(frozen=True)
class FieldSpec:
    """One field of the profile form, over a small fixed set of types."""

    name: str
    label: str
    type: FieldType
    min: Decimal | None = None
    max: Decimal | None = None
    options: tuple[str, ...] = ()


@dataclass(frozen=True)
class Fund:
    article: int
    employer_group: str
    member_label: str
    support_level: SupportLevel = "experimental"
    # Bounds that differ by Article. The rest of `personas.validate` is
    # relational and holds for every fund alike.
    min_hire_age: int = 16
    salary_max: Decimal = Decimal("1000000")
    # A field is added here only when a recipe for this Article reads it --
    # typically found as a recurring `missing_inputs` entry -- and `facts.py`
    # gains the alias that maps it into the engine. Storage for such a field is
    # built with the first one; none exists yet.
    extra_fields: tuple[FieldSpec, ...] = ()

    @property
    def fields(self) -> tuple[FieldSpec, ...]:
        return (
            FieldSpec("birth_date", "Date of birth", "date"),
            FieldSpec("hire_date", "Start date", "date"),
            FieldSpec("employment_status", "Employment status", "status",
                      options=EMPLOYMENT_STATUSES),
            FieldSpec("total_service_years", "Years of service", "years", min=Decimal("0")),
            FieldSpec("current_annual_salary", "Annual salary", "money",
                      min=Decimal("0.01"), max=self.salary_max),
            *self.extra_fields,
        )


CHICAGO = "City of Chicago"
COOK = "Cook County"
STATE = "State of Illinois"
LOCAL = "Local and regional"

GROUP_ORDER = (CHICAGO, COOK, STATE, LOCAL)

# Nobody joins a police or fire service, or the General Assembly, before 21;
# a judge is a licensed attorney first.
_UNIFORMED_OR_ELECTED = 21
_JUDGE = 25

FUNDS: dict[int, Fund] = {
    fund.article: fund
    for fund in (
        Fund(5, CHICAGO, "Chicago police officer", min_hire_age=_UNIFORMED_OR_ELECTED),
        Fund(6, CHICAGO, "Chicago firefighter", min_hire_age=_UNIFORMED_OR_ELECTED),
        Fund(8, CHICAGO, "Chicago municipal employee"),
        Fund(11, CHICAGO, "Chicago laborer", support_level="checked"),
        Fund(12, CHICAGO, "Chicago Park District employee", support_level="checked"),
        Fund(17, CHICAGO, "Chicago Public Schools teacher"),
        Fund(9, COOK, "Cook County employee"),
        Fund(10, COOK, "Forest Preserve District employee"),
        Fund(2, STATE, "General Assembly member", min_hire_age=_UNIFORMED_OR_ELECTED),
        Fund(14, STATE, "State employee"),
        Fund(15, STATE, "State university employee"),
        Fund(16, STATE, "Teacher outside Chicago"),
        Fund(18, STATE, "Judge", min_hire_age=_JUDGE),
        Fund(3, LOCAL, "Police officer outside Chicago", min_hire_age=_UNIFORMED_OR_ELECTED),
        Fund(4, LOCAL, "Firefighter outside Chicago", min_hire_age=_UNIFORMED_OR_ELECTED),
        Fund(7, LOCAL, "Municipal employee (IMRF)"),
        Fund(13, LOCAL, "Water Reclamation District employee"),
        Fund(19, LOCAL, "Closed fund member"),
    )
}


def for_article(article: int) -> Fund | None:
    return FUNDS.get(article)


def support_level(article: int | str | None) -> SupportLevel | None:
    """The fund's level, whether the Article arrives as coverage's string or the record's int."""
    if article is None:
        return None
    try:
        fund = FUNDS.get(int(article))
    except (TypeError, ValueError):
        return None
    return fund.support_level if fund else None
