"""Synthetic employee data seeder per §7.4.

Generates realistic synthetic employees for pilot testing.
Run: uv run python scripts/seed_synthetic_data.py
"""

from __future__ import annotations

import asyncio
import os
import sys

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dotenv import load_dotenv

load_dotenv()

# Imported after load_dotenv() on purpose: pension_chat.config reads the
# environment, so the .env file has to be in place before these bind.
from sqlalchemy import select  # noqa: E402

from pension_chat import personas  # noqa: E402
from pension_chat.db.models import (  # noqa: E402
    Employee,
    EmployeeSalaryHistory,
    PensionArticle,
)
from pension_chat.db.session import get_admin_session  # noqa: E402

# ── Seed data ─────────────────────────────────────────────────────

ARTICLES = [
    {
        "article_number": 11,
        "system_name": "Laborers' & Retirement Board Employees' Annuity and Benefit Fund",
    },
    {
        "article_number": 12,
        "system_name": "Park Employees' & Retirement Board Employees' Annuity and Benefit Fund",
    },
]


async def seed():
    """Seed the database with articles and synthetic employees."""
    # Row-level security is enforced on `employees` (migration 003), and
    # seeding is by definition an operation with no member to be scoped to.
    # The raw session factory sets no identity, so every insert here failed
    # the policy -- and so did the SELECT that checks whether a row already
    # exists, which is why a re-run tried to insert a duplicate rather than
    # skipping.
    async with get_admin_session() as db:
        # ── Seed pension articles ─────────────────────────────────
        for article_data in ARTICLES:
            existing = await db.execute(
                select(PensionArticle).where(
                    PensionArticle.article_number == article_data["article_number"]
                )
            )
            if existing.scalar_one_or_none() is None:
                db.add(PensionArticle(**article_data))
                print(f"  ✓ Created Article {article_data['article_number']}: {article_data['system_name']}")
            else:
                print(f"  · Article {article_data['article_number']} already exists, skipping")

        await db.flush()

        # ── Seed employees ────────────────────────────────────────
        #
        # The two pilot members come from `personas.py`, which is also what the
        # signup picker and the eval harness read. They carry explicit ids on
        # purpose: `eval_agent.py` and `gold_coverage.yaml` name those ids
        # outright, and while the column defaulted to `gen_random_uuid()` a
        # freshly seeded database bound both cases to nothing at all -- without
        # failing, because a case with no such employee simply runs anonymous.
        for persona in personas.SEEDED:
            existing = await db.execute(
                select(Employee).where(Employee.id == persona.id)
            )
            if existing.scalar_one_or_none() is not None:
                print(f"  · Employee {persona.email} already exists, skipping")
                continue

            db.add(Employee(email=persona.email, **personas.to_employee_kwargs(persona)))
            await db.flush()

            for eff_date, salary in persona.salary_history:
                db.add(EmployeeSalaryHistory(
                    employee_id=persona.id,
                    effective_date=eff_date,
                    annual_salary=salary,
                ))

            print(f"  ✓ Created employee: {persona.email} "
                  f"(Article {persona.article_number}, Tier {persona.tier}, "
                  f"hired {persona.hire_date}, "
                  f"salary ${persona.current_annual_salary:,.2f})")

        await db.commit()
        print("\n✅ Seed complete!")


if __name__ == "__main__":
    print("🌱 Seeding synthetic data...\n")
    asyncio.run(seed())
