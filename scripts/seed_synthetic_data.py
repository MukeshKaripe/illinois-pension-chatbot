"""Synthetic employee data seeder per §7.4.

Generates realistic synthetic employees for pilot testing.
Run: uv run python scripts/seed_synthetic_data.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import date
from decimal import Decimal

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import select

from pension_chat.db.models import Employee, EmployeeSalaryHistory, PensionArticle
from pension_chat.db.session import get_admin_session

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


# Single test employee as requested
EMPLOYEES = [
    {
        "email": "pen_test_1@yopmail.com",
        "article_number": 11,
        "tier": 1,
        "birth_date": date(1970, 5, 15),
        "hire_date": date(2005, 3, 1),
        "employment_status": "active",
        "total_service_years": Decimal("21.50"),
        "current_annual_salary": Decimal("72000.00"),
        "salary_history": [
            # 8 years of salary history for FAS calculation
            (date(2018, 1, 1), Decimal("58000.00")),
            (date(2019, 1, 1), Decimal("59740.00")),
            (date(2020, 1, 1), Decimal("61532.20")),
            (date(2021, 1, 1), Decimal("63378.17")),
            (date(2022, 1, 1), Decimal("65279.51")),
            (date(2023, 1, 1), Decimal("67237.89")),
            (date(2024, 1, 1), Decimal("69255.03")),
            (date(2025, 1, 1), Decimal("71332.68")),
            (date(2026, 1, 1), Decimal("72000.00")),
        ],
    },
    # An Article 12 member, so that the *agreeing* case can be tested at all.
    # With only the Article 11 employee above, every question from someone who
    # describes themselves as a park employee is a coverage conflict, and a
    # check that can only ever fire one way proves nothing. This member says
    # "I am a park employee" and their record agrees — the assistant must
    # recognise that and answer, not ask.
    {
        "email": "pen_test_2@yopmail.com",
        "article_number": 12,
        "tier": 1,
        "birth_date": date(1968, 9, 2),
        "hire_date": date(2001, 7, 16),
        "employment_status": "active",
        "total_service_years": Decimal("25.00"),
        "current_annual_salary": Decimal("68000.00"),
        "salary_history": [
            (date(2018, 1, 1), Decimal("55000.00")),
            (date(2019, 1, 1), Decimal("56650.00")),
            (date(2020, 1, 1), Decimal("58349.50")),
            (date(2021, 1, 1), Decimal("60099.99")),
            (date(2022, 1, 1), Decimal("61902.98")),
            (date(2023, 1, 1), Decimal("63760.07")),
            (date(2024, 1, 1), Decimal("65672.87")),
            (date(2025, 1, 1), Decimal("67643.06")),
            (date(2026, 1, 1), Decimal("68000.00")),
        ],
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
        for emp_data in EMPLOYEES:
            existing = await db.execute(
                select(Employee).where(Employee.email == emp_data["email"])
            )
            if existing.scalar_one_or_none() is not None:
                print(f"  · Employee {emp_data['email']} already exists, skipping")
                continue

            salary_history = emp_data.pop("salary_history", [])
            employee = Employee(**emp_data)
            db.add(employee)
            await db.flush()
            await db.refresh(employee)

            # Add salary history
            for eff_date, salary in salary_history:
                db.add(EmployeeSalaryHistory(
                    employee_id=employee.id,
                    effective_date=eff_date,
                    annual_salary=salary,
                ))

            print(f"  ✓ Created employee: {emp_data['email']} "
                  f"(Article {emp_data['article_number']}, Tier {emp_data['tier']}, "
                  f"hired {emp_data['hire_date']}, "
                  f"salary ${emp_data['current_annual_salary']:,.2f})")
            # Restore for potential re-run
            emp_data["salary_history"] = salary_history

        await db.commit()
        print("\n✅ Seed complete!")


if __name__ == "__main__":
    print("🌱 Seeding synthetic data...\n")
    asyncio.run(seed())
