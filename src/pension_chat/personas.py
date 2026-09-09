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
from dataclasses import dataclass, replace
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

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

EMPLOYMENT_STATUSES = ("active", "separated", "retired")

# The minimum working age the bounds enforce, so a persona cannot have been
# hired as a small child.
_MIN_WORKING_AGE = 16


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
    label="Standard",
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
    label="Near retirement",
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
    label="Tier 2",
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
    label="Standard",
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
    label="Tier 2",
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

PERSONAS: tuple[Persona, ...] = (
    ARTICLE_11_STANDARD,
    ARTICLE_11_NEAR_RETIREMENT,
    ARTICLE_11_TIER_2,
    ARTICLE_11_EARLY_CAREER,
    ARTICLE_11_RETIRED,
    ARTICLE_12_STANDARD,
    ARTICLE_12_TIER_2,
)

# The two the pilot database already holds, and the two the benchmark binds to.
SEEDED: tuple[Persona, ...] = (ARTICLE_11_STANDARD, ARTICLE_12_STANDARD)

# Articles a persona may be offered for. Deliberately narrower than what is
# ingested: Articles 8 and 9 are in the corpus and the foreign key would accept
# them, but neither has a derived recipe, so a member filed under one would get
# good statute answers and "I cannot find a governing provision" to every
# calculation, permanently, with nothing on screen explaining why. They can be
# asked about — that path does not need a persona.
OFFERED_ARTICLES = (11, 12)


def by_key(key: str) -> Persona | None:
    return next((p for p in PERSONAS if p.key == key), None)


def by_id(persona_id: uuid.UUID | str) -> Persona | None:
    wanted = str(persona_id)
    return next((p for p in PERSONAS if str(p.id) == wanted), None)


def for_article(article_number: int) -> tuple[Persona, ...]:
    return tuple(p for p in PERSONAS if p.article_number == article_number)


# ── Editing a persona ─────────────────────────────────────────────

EDITABLE_FIELDS = (
    "birth_date",
    "hire_date",
    "employment_status",
    "total_service_years",
    "current_annual_salary",
)


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
    if not changes:
        return persona

    updated = replace(persona, **changes)  # type: ignore[arg-type]

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

    if persona.article_number not in OFFERED_ARTICLES:
        problems.append(f"Article {persona.article_number} has no personas.")

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
    if hire_age < _MIN_WORKING_AGE:
        problems.append(
            f"Hire date puts them in the job at {hire_age}; "
            f"the minimum here is {_MIN_WORKING_AGE}."
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
    elif persona.current_annual_salary > Decimal("1000000"):
        problems.append("Salary above $1,000,000 is outside the range these presets cover.")

    if not persona.salary_history:
        # Without history there is no final average salary and therefore no
        # figure at all — the member would be asked for ten years of pay on
        # every calculation question.
        problems.append("A persona must carry salary history.")

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
    }
