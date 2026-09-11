"""The synthetic members someone can sign up as.

Employee facts belong to an HR or pension administration system, and this
prototype is not connected to one. So signup does not ask a member to *author* a
pension record — it asks them to pick one, from a set authored here.

That distinction is what keeps the rest of the system honest. Every other
determination rests on the record being evidence *independent* of what the
member says: `agent/coverage.py` treats the record and the member's word as two
different voices, and `agent/guardrails.py` traces figures to "the member's
record" and "the member's own messages" as separate provenance classes. A member
who typed their own Article number and salary would collapse those into one
voice agreeing with itself, and "your record shows" would become a sentence
about something they entered thirty seconds earlier. Selecting a persona keeps
the two apart: the record was written by someone else, which is exactly the
property that matters. It is fiction, but it is *consistent* fiction, and it is
the same shape the real thing will have.

Values inside a persona are editable within bounds — see `apply_edits`, and note
what it does to the salary history, which is not optional bookkeeping.

**One definition, three consumers.** The signup picker, `seed_synthetic_data.py`
and `eval_agent.py` all read this module. Before it existed, the two seeded
members got their ids from `gen_random_uuid()` while `eval_agent.py` and
`gold_coverage.yaml` each hard-coded one of those ids — so a fresh database
re-seeded fine and silently bound those cases to nothing. The ids below are
fixed, and the two originals keep the values that database already holds.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from pension_chat import funds
from pension_chat.agent.facts import age_on, derive_tier

# Salary grows 3% a year, then the final year is set to the current salary.
#
# The two originally seeded histories are written out in full below rather than
# generated. They are close to this curve but not on it — a cent adrift in two
# years, from rounding that no single rule reproduces, because they were written
# by hand. Their final average salary feeds every calculation case in the
# benchmark, and "close enough" is the wrong standard for a pension figure, so
# they keep their exact values and the generator serves only the new personas.
_ANNUAL_RAISE = Decimal("1.03")
_HISTORY_START_YEAR = 2018
_HISTORY_YEARS = 8

EMPLOYMENT_STATUSES = funds.EMPLOYMENT_STATUSES


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _history(base: Decimal, final: Decimal) -> tuple[tuple[date, Decimal], ...]:
    """Eight years of 3% raises, then a final year at the current salary."""
    rows: list[tuple[date, Decimal]] = []
    salary = base
    for offset in range(_HISTORY_YEARS):
        rows.append((date(_HISTORY_START_YEAR + offset, 1, 1), _money(salary)))
        salary = salary * _ANNUAL_RAISE
    rows.append((date(_HISTORY_START_YEAR + _HISTORY_YEARS, 1, 1), _money(final)))
    return tuple(rows)


def _rows(*amounts: str) -> tuple[tuple[date, Decimal], ...]:
    """One row per year from `_HISTORY_START_YEAR`, written out verbatim."""
    return tuple(
        (date(_HISTORY_START_YEAR + offset, 1, 1), Decimal(amount))
        for offset, amount in enumerate(amounts)
    )


def _career(
    final: str, last_year: int = _HISTORY_START_YEAR + _HISTORY_YEARS
) -> tuple[tuple[date, Decimal], ...]:
    """Nine years of 3% raises ending at `final` on 1 January of `last_year`.

    Counted back from the salary a member sees rather than forward from an
    invented starting figure, so the current salary sits on the curve.
    """
    amount = Decimal(final)
    return tuple(
        (date(last_year - back, 1, 1), _money(amount / _ANNUAL_RAISE**back))
        for back in range(_HISTORY_YEARS, -1, -1)
    )


@dataclass(frozen=True)
class Persona:
    """One selectable synthetic member."""

    id: uuid.UUID
    key: str
    article_number: int
    label: str
    summary: str
    birth_date: date
    hire_date: date
    employment_status: str
    total_service_years: Decimal
    current_annual_salary: Decimal
    salary_history: tuple[tuple[date, Decimal], ...]
    # The two pilot members keep the addresses already in the database, so
    # re-seeding stays idempotent. Everyone else signs up with their own.
    email: str | None = None
    # Facts only some funds have (`funds.Fund.extra_fields`), as sorted
    # (name, value) pairs so a persona stays hashable. Empty for most funds.
    extra_facts: tuple[tuple[str, object], ...] = ()

    @property
    def tier(self) -> int:
        """Never stored, never asked for — see `facts.derive_tier`."""
        return derive_tier(self.hire_date)

    def age_today(self, today: date | None = None) -> int:
        return age_on(self.birth_date, today or date.today())


# ── The presets ───────────────────────────────────────────────────
#
# Chosen so that between them they reach the branches that matter, rather than
# to cover a demographic range. Each one is the shortest path to a behaviour the
# benchmark also tests.

ARTICLE_11_STANDARD = Persona(
    # The id `eval_agent.py` has always used as its default member.
    id=uuid.UUID("de75c498-20cc-446d-8858-ec26303349fa"),
    key="article-11-standard",
    article_number=11,
    label="Mid-career, still working",
    summary="Mid-career, Tier 1, still working. 21.5 years of service at 56.",
    birth_date=date(1970, 5, 15),
    hire_date=date(2005, 3, 1),
    employment_status="active",
    total_service_years=Decimal("21.50"),
    current_annual_salary=Decimal("72000.00"),
    salary_history=_rows(
        "58000.00", "59740.00", "61532.20", "63378.17", "65279.51",
        "67237.89", "69255.03", "71332.68", "72000.00",
    ),
    email="pen_test_1@yopmail.com",
)

ARTICLE_11_NEAR_RETIREMENT = Persona(
    id=uuid.UUID("b1f0a4c2-5d3e-4a71-9c88-0f2e6d7a1b34"),
    key="article-11-near-retirement",
    article_number=11,
    label="Close to retirement",
    summary=(
        "59 with 31 years of service — close enough to age 60 for the "
        "early-retirement reduction to matter, and close enough to the 80% cap "
        "to run into it."
    ),
    birth_date=date(1967, 2, 11),
    hire_date=date(1995, 6, 5),
    employment_status="active",
    total_service_years=Decimal("31.00"),
    current_annual_salary=Decimal("86000.00"),
    salary_history=_history(Decimal("69000.00"), Decimal("86000.00")),
)

ARTICLE_11_TIER_2 = Persona(
    id=uuid.UUID("c4a7e912-88b6-4f05-a3d1-7e5c0b93f2aa"),
    key="article-11-tier-2",
    article_number=11,
    label="Joined after 2011",
    summary="First joined in 2014, so Section 1-160 governs the accrual rather than Article 11 alone.",
    birth_date=date(1988, 10, 23),
    hire_date=date(2014, 4, 14),
    employment_status="active",
    total_service_years=Decimal("12.00"),
    current_annual_salary=Decimal("64000.00"),
    salary_history=_history(Decimal("51000.00"), Decimal("64000.00")),
)

ARTICLE_11_EARLY_CAREER = Persona(
    id=uuid.UUID("d0c93b55-2e14-4a8f-b6d7-9a1f4c8e2705"),
    key="article-11-early-career",
    article_number=11,
    label="Early career",
    # Necessarily Tier 2: to be early in a career and Tier 1 you would have had
    # to join before 2011, which is no longer early. What this one adds over the
    # Tier 2 persona above is too little service to draw an annuity at all.
    summary="34 with 6 years in. Not yet eligible for a retirement annuity, which is its own answer.",
    birth_date=date(1992, 7, 30),
    hire_date=date(2020, 9, 1),
    employment_status="active",
    total_service_years=Decimal("6.00"),
    current_annual_salary=Decimal("54000.00"),
    salary_history=_history(Decimal("43000.00"), Decimal("54000.00")),
)

ARTICLE_11_RETIRED = Persona(
    id=uuid.UUID("e2b6147a-3f9d-4c02-8e5b-1d7a0f36c9b8"),
    key="article-11-retired",
    article_number=11,
    label="Already retired",
    summary=(
        "Withdrew at 62 with 28 years. Questions about an annuity in payment "
        "read differently from questions about one not yet claimed."
    ),
    birth_date=date(1963, 12, 4),
    hire_date=date(1997, 1, 20),
    employment_status="retired",
    total_service_years=Decimal("28.00"),
    current_annual_salary=Decimal("78000.00"),
    salary_history=_history(Decimal("63000.00"), Decimal("78000.00")),
)

ARTICLE_12_STANDARD = Persona(
    # The id `gold_coverage.yaml` names, for the case where a member describes
    # themselves as a park employee and their record agrees.
    id=uuid.UUID("8b33966a-8d4d-47e5-8fbf-fc950239f111"),
    key="article-12-standard",
    article_number=12,
    label="Mid-career, still working",
    summary="Park employee, Tier 1, 25 years of service at 57.",
    birth_date=date(1968, 9, 2),
    hire_date=date(2001, 7, 16),
    employment_status="active",
    total_service_years=Decimal("25.00"),
    current_annual_salary=Decimal("68000.00"),
    salary_history=_rows(
        "55000.00", "56650.00", "58349.50", "60099.99", "61902.98",
        "63760.07", "65672.87", "67643.06", "68000.00",
    ),
    email="pen_test_2@yopmail.com",
)

ARTICLE_12_TIER_2 = Persona(
    id=uuid.UUID("f7d21c48-6a35-4b90-8c1e-2b9f5d03e614"),
    key="article-12-tier-2",
    article_number=12,
    label="Joined after 2011",
    summary=(
        "Park employee who first joined in 2016 — Section 1-160 reaches "
        "Article 12 the same way it reaches Article 11."
    ),
    birth_date=date(1990, 3, 18),
    hire_date=date(2016, 8, 22),
    employment_status="active",
    total_service_years=Decimal("10.00"),
    current_annual_salary=Decimal("59000.00"),
    salary_history=_history(Decimal("47000.00"), Decimal("59000.00")),
)

# ── One standard member for every other fund (Phase 3b) ───────────
#
# So a profile can be filed under the fund someone actually works for. Each is
# Tier 1, active, and already past their own fund's retirement threshold as the
# Article states it (`tests/test_funds.py` holds the thresholds and their
# sections): the smoke question is "what if I retired at the end of this
# year?", and a member who cannot yet retire gets an honest "not yet" that says
# nothing about whether the fund's formula works. What differs between them is
# the fund's law rather than the member's stage -- the stage-specific presets
# above stay with Articles 11 and 12, where the benchmark tests them. Pay and
# hire ages are plausible for each fund's members, not drawn from anyone real.


def _standard(
    persona_id: str,
    article: int,
    summary: str,
    *,
    born: date,
    hired: date,
    service: str,
    salary: str,
    status: str = "active",
    label: str = "Close to retirement",
    last_year: int = _HISTORY_START_YEAR + _HISTORY_YEARS,
    extra: dict[str, object] | None = None,
) -> Persona:
    return Persona(
        id=uuid.UUID(persona_id),
        key=f"article-{article}-standard",
        article_number=article,
        label=label,
        summary=summary,
        birth_date=born,
        hire_date=hired,
        employment_status=status,
        total_service_years=Decimal(service),
        current_annual_salary=Decimal(salary),
        salary_history=_career(salary, last_year),
        extra_facts=tuple(sorted((extra or {}).items())),
    )


FUND_STANDARDS: tuple[Persona, ...] = (
    _standard("62193da4-55ae-4505-9dd0-43339c29915c", 2,
              "First elected in 2003, with 22 years in the General Assembly.",
              born=date(1968, 4, 12), hired=date(2003, 1, 8), service="22.00", salary="89250.00"),
    _standard("5f8ea32a-ffaf-4775-a80b-3ef37d92e581", 3,
              "Officer in a municipal police department outside Chicago, 24 years in. "
              "The pension is half the salary attached to the rank (Section 3-111).",
              born=date(1975, 6, 3), hired=date(2002, 8, 15), service="24.00", salary="98000.00"),
    _standard("662db0e0-54ea-405e-ace5-bdea799e462c", 4,
              "Firefighter in a municipal department outside Chicago, 24 years in. "
              "The pension is half the salary attached to the rank (Section 4-109).",
              born=date(1975, 11, 20), hired=date(2002, 5, 6), service="24.00", salary="96500.00"),
    _standard("176b0472-e700-41ab-8c6f-12de2b651722", 5,
              "Chicago police officer with 24 and a half years on the force.",
              born=date(1976, 2, 9), hired=date(2001, 10, 1), service="24.50", salary="112000.00"),
    _standard("7a29b230-3b3b-431b-bacf-e0926335aebb", 6,
              "Chicago firefighter with 24 years in the department.",
              born=date(1975, 9, 14), hired=date(2002, 3, 18), service="24.00", salary="110500.00"),
    _standard("1ff37522-f0b4-44f6-89c0-5736e0b439c5", 7,
              "Regular employee of a town that participates in IMRF, 20 and a half years in.",
              born=date(1969, 12, 1), hired=date(2006, 2, 6), service="20.50", salary="61000.00"),
    _standard("b884bbe6-1ee6-4438-9f6a-6442e79c3f11", 8,
              "City of Chicago employee with 23 years of service.",
              born=date(1971, 7, 22), hired=date(2003, 5, 12), service="23.00", salary="84000.00"),
    _standard("d1b67d9c-abec-43c2-8f51-4b4ef322df55", 9,
              "Cook County employee with 21 and a half years of service.",
              born=date(1969, 3, 30), hired=date(2004, 9, 7), service="21.50", salary="76000.00"),
    _standard("1b48dc21-8d09-4dc9-a828-ac533b3d00eb", 10,
              "Forest Preserve District employee with 19 years of service.",
              born=date(1968, 8, 17), hired=date(2007, 4, 2), service="19.00", salary="66000.00"),
    _standard("0368d77a-f6c6-4653-911d-2f3bb2aaae4d", 13,
              "Metropolitan Water Reclamation District employee, 26 years in.",
              born=date(1970, 10, 5), hired=date(2000, 6, 19), service="26.00", salary="92000.00"),
    _standard("932245ac-c14c-4925-9fd0-3084b07cfdc4", 14,
              "State of Illinois employee with 21 years of service, covered by Social "
              "Security, which sets the accrual rate (Section 14-108).",
              born=date(1965, 1, 25), hired=date(2005, 7, 1), service="21.00", salary="70000.00",
              extra={"social_security_covered": True}),
    _standard("4eee700a-1037-4644-b1e0-509d635146ac", 15,
              "Staff member at a state university, 22 years in.",
              born=date(1971, 5, 8), hired=date(2004, 8, 16), service="22.00", salary="78000.00"),
    _standard("98dd51b8-c6a6-4a11-9dd2-50c8d1e1806e", 16,
              "Public school teacher outside Chicago with 23 years in the classroom.",
              born=date(1969, 9, 2), hired=date(2003, 8, 18), service="23.00", salary="74000.00"),
    _standard("bb5fb6c5-e700-48aa-81f1-90c16546129d", 17,
              "Chicago Public Schools teacher with 22 years in the classroom.",
              born=date(1970, 4, 27), hired=date(2004, 8, 30), service="22.00", salary="92000.00"),
    _standard("a3afe7a8-2c1c-49ea-a8ec-1d06003d6e16", 18,
              "Circuit judge since 2006, with 19 and a half years on the bench.",
              born=date(1962, 11, 11), hired=date(2006, 12, 4), service="19.50", salary="235000.00"),
    # Authored last, as the spec asks: a closed fund admits no new members, so
    # its member must be one who joined long ago -- here, already retired.
    # Article 19 holds more than one closed fund; this is Division 1 (§19-101).
    _standard("60e22f7a-c853-4c68-8f65-07949bf9e054", 19,
              "Retired in 1998 after 30 years with the Chicago House of Correction, whose "
              "employees' pension fund (Article 19, Division 1) admits no new members.",
              born=date(1945, 3, 14), hired=date(1968, 6, 3), service="30.00", salary="48000.00",
              status="retired", label="Already retired", last_year=1998),
)

PERSONAS: tuple[Persona, ...] = (
    ARTICLE_11_STANDARD,
    ARTICLE_11_NEAR_RETIREMENT,
    ARTICLE_11_TIER_2,
    ARTICLE_11_EARLY_CAREER,
    ARTICLE_11_RETIRED,
    ARTICLE_12_STANDARD,
    ARTICLE_12_TIER_2,
    *FUND_STANDARDS,
)

# The two the pilot database already holds, and the two the benchmark binds to.
SEEDED: tuple[Persona, ...] = (ARTICLE_11_STANDARD, ARTICLE_12_STANDARD)

#: One member per fund in `funds.FUNDS` -- what the smoke evals run as.
STANDARD: tuple[Persona, ...] = (ARTICLE_11_STANDARD, ARTICLE_12_STANDARD, *FUND_STANDARDS)


def by_key(key: str) -> Persona | None:
    return next((p for p in PERSONAS if p.key == key), None)


def by_id(persona_id: uuid.UUID | str) -> Persona | None:
    wanted = str(persona_id)
    return next((p for p in PERSONAS if str(p.id) == wanted), None)


def for_article(article_number: int) -> tuple[Persona, ...]:
    return tuple(p for p in PERSONAS if p.article_number == article_number)


def persona_from_row(row: Any, history: Sequence[tuple[date, Decimal]]) -> Persona:
    """A stored profile as a Persona, so `apply_edits` and `validate` apply to it.

    A profile starts from a preset and is then edited, so every value is the
    row's; the preset lends only what the row does not store (its summary). The
    Article comes from the row and nothing here can change it -- another
    Article is another profile.
    """
    template = by_key(row.persona_key) if row.persona_key else by_id(row.id)
    return Persona(
        id=row.id,
        key=row.persona_key or (template.key if template else "custom"),
        article_number=row.article_number,
        label=row.label or (template.label if template else "Profile"),
        summary=template.summary if template else "",
        birth_date=row.birth_date,
        hire_date=row.hire_date,
        employment_status=row.employment_status,
        total_service_years=Decimal(str(row.total_service_years)),
        current_annual_salary=Decimal(str(row.current_annual_salary)),
        salary_history=tuple((when, Decimal(str(amount))) for when, amount in history),
        email=row.email,
        extra_facts=tuple(sorted((getattr(row, "extra_facts", None) or {}).items())),
    )


# ── Editing a persona ─────────────────────────────────────────────

EDITABLE_FIELDS = funds.COMMON_FIELD_NAMES


def apply_edits(persona: Persona, edits: dict[str, object]) -> Persona:
    """Return the persona with in-bounds edits applied.

    **Editing the salary rescales the history, and that is the whole point.**
    Article 11's annuity is computed from `final_average_salary`, which
    `facts.highest_average_salary` derives from the history rows — not from
    `current_annual_salary`. Leave the two independent and a member who raises
    their salary from $72,000 to $90,000 watches the pension barely move, and
    concludes the thing is broken. They would be right: the number they changed
    would not be an input to the formula. Scaling by the same ratio keeps the
    shape of the raises and moves the average with the figure they edited.

    Unknown keys are ignored rather than raising: the caller validates the
    payload, and this stays a pure transformation.
    """
    changes = {k: v for k, v in edits.items() if k in EDITABLE_FIELDS and v is not None}
    extra = edits.get("extra_facts") or {}
    if not changes and not extra:
        return persona

    updated = replace(persona, **changes)  # type: ignore[arg-type]
    if extra:
        # Kept even when the fund declares no such field, so `validate` can
        # refuse it: this JSON arrives from an open endpoint.
        merged = dict(persona.extra_facts)
        merged.update(
            {name: _coerce(persona.article_number, name, value) for name, value in extra.items()}
        )
        updated = replace(updated, extra_facts=tuple(sorted(merged.items())))

    new_salary = changes.get("current_annual_salary")
    if new_salary is not None and persona.current_annual_salary > 0:
        ratio = Decimal(str(new_salary)) / persona.current_annual_salary
        updated = replace(
            updated,
            salary_history=tuple(
                (when, _money(amount * ratio)) for when, amount in persona.salary_history
            ),
        )
    return updated


def _coerce(article: int, name: str, value: object) -> object:
    """A form sends text; the fund's field type decides what is stored."""
    fund = funds.for_article(article)
    spec = next((s for s in fund.extra_fields if s.name == name), None) if fund else None
    if spec is not None and spec.type == "yesno" and isinstance(value, str):
        return {"true": True, "yes": True, "false": False, "no": False}.get(
            value.strip().lower(), value
        )
    return value


def validate(persona: Persona, today: date | None = None) -> list[str]:
    """Every way a persona can be internally incoherent.

    Bounds are **relational**. Checking each field against its own range
    independently still admits someone hired before they were born, or with more
    service than they have had a job — and the agent will answer confidently
    about either, because nothing downstream re-checks the record.

    What this deliberately does *not* enforce is eligibility. A persona too young
    or too short-served to draw an annuity is a legitimate thing to be, and
    saying so is one of the answers this system is meant to give well.
    """
    today = today or date.today()
    problems: list[str] = []

    fund = funds.for_article(persona.article_number)
    if fund is None:
        problems.append(f"Article {persona.article_number} has no profiles.")
        # Everything else still applies, against the defaults every fund starts from.
        fund = funds.Fund(persona.article_number, "", "")

    if persona.employment_status not in EMPLOYMENT_STATUSES:
        problems.append(f"Unknown employment status {persona.employment_status!r}.")

    if persona.birth_date >= today:
        problems.append("Date of birth must be in the past.")
    age = age_on(persona.birth_date, today)
    if not 18 <= age <= 100:
        problems.append(f"Date of birth gives an age of {age}; expected 18 to 100.")

    if persona.hire_date > today:
        problems.append("Hire date cannot be in the future.")
    hire_age = age_on(persona.birth_date, persona.hire_date)
    if hire_age < fund.min_hire_age:
        problems.append(
            f"Hire date puts them in the job at {hire_age}; "
            f"the minimum here is {fund.min_hire_age}."
        )

    tenure = Decimal(str((today - persona.hire_date).days)) / Decimal("365.25")
    if persona.total_service_years < 0:
        problems.append("Service years cannot be negative.")
    elif persona.total_service_years > tenure.quantize(Decimal("0.01")):
        problems.append(
            f"Service of {persona.total_service_years} years exceeds the "
            f"{tenure.quantize(Decimal('0.01'))} years since the hire date."
        )

    if persona.current_annual_salary <= 0:
        # A zero salary is not merely odd: the column is NOT NULL with no
        # default, so it produces a confident $0.00 annuity that the numeric
        # guardrail passes without complaint, because it traces to the record.
        problems.append("Salary must be greater than zero.")
    elif persona.current_annual_salary > fund.salary_max:
        problems.append(
            f"Salary above ${fund.salary_max:,.0f} is outside the range this fund's profiles cover."
        )

    if not persona.salary_history:
        # Without history there is no final average salary and therefore no
        # figure at all — the member would be asked for ten years of pay on
        # every calculation question.
        problems.append("A persona must carry salary history.")

    declared = {spec.name: spec for spec in fund.extra_fields}
    for name, value in persona.extra_facts:
        spec = declared.get(name)
        if spec is None:
            problems.append(f"{name!r} is not a field for this fund.")
        elif spec.type == "yesno" and not isinstance(value, bool):
            problems.append(f"{spec.label} must be yes or no.")

    return problems


def to_employee_kwargs(
    persona: Persona, employee_id: uuid.UUID | None = None
) -> dict[str, object]:
    """The column values for an `employees` row, tier derived rather than given.

    `employee_id` overrides the persona's own id, and signup always passes one:
    a persona is a template, and two people picking the same one are two members
    with two records, not one row with two passwords. The persona's id is used
    only for the seeded pilot members, where it is what the benchmark binds to.
    """
    return {
        "id": employee_id or persona.id,
        "article_number": persona.article_number,
        "tier": persona.tier,
        "birth_date": persona.birth_date,
        "hire_date": persona.hire_date,
        "employment_status": persona.employment_status,
        "total_service_years": persona.total_service_years,
        "current_annual_salary": persona.current_annual_salary,
        "extra_facts": dict(persona.extra_facts),
    }
