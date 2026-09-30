from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from pension_chat.evals.fixtures import FixtureProvider, load_fixtures

TIER2 = """
key: tier2_standard
display_name: "A. Rivera"
employer: City of Chicago
department: Streets and Sanitation
age: 58
hire_date: 2013-06-01
tier: 2
status: active
service_years: 12.5
service_source: stored
salary: {current: 84000, growth: 0.03, years: 4}
"""

NO_HIRE = """
key: no_hire_date
display_name: "B. Chen"
age: 61
tier: null
status: active
salary: {current: 70000, growth: 0.0, years: 2}
missing: {hire_date: "not recorded in PBMS", service_years: "no stored service"}
"""


def _dir(tmp: Path, **files: str) -> Path:
    for name, text in files.items():
        (tmp / f"{name}.yaml").write_text(text, encoding="utf-8")
    return tmp


def test_relative_dates_are_materialised_against_the_run_date(tmp_path: Path) -> None:
    today = date(2026, 9, 30)
    fx = load_fixtures(_dir(tmp_path, a=TIER2), today)
    record, history = fx["tier2_standard"]
    assert record.birth_date == date(1968, 9, 30)
    assert record.hire_date == date(2013, 6, 1)          # absolute: tier depends on it
    assert [h.year for h in history] == [2023, 2024, 2025, 2026]
    assert history[-1].annual_salary == Decimal("84000")
    assert history[0].annual_salary == (Decimal("84000") / Decimal("1.03") ** 3).quantize(Decimal("0.01"))


def test_the_age_does_not_drift_with_the_calendar(tmp_path: Path) -> None:
    for today in (date(2026, 9, 30), date(2030, 1, 15)):
        record, _ = load_fixtures(_dir(tmp_path, a=TIER2), today)["tier2_standard"]
        born = record.birth_date
        age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
        assert age == 58


def test_missing_fields_stay_missing_with_their_reason(tmp_path: Path) -> None:
    record, _ = load_fixtures(_dir(tmp_path, b=NO_HIRE), date(2026, 9, 30))["no_hire_date"]
    assert record.hire_date is None and record.service_years is None
    assert record.missing["hire_date"] == "not recorded in PBMS"


@pytest.mark.asyncio
async def test_provider_answers_from_fixtures(tmp_path: Path) -> None:
    provider = FixtureProvider(load_fixtures(_dir(tmp_path, a=TIER2, b=NO_HIRE), date(2026, 9, 30)))
    assert (await provider.get_record("tier2_standard")).tier == 2
    assert await provider.get_record("nobody") is None
    assert len(await provider.get_salary_history("tier2_standard")) == 4
    matches = await provider.find_employees("rivera")
    assert [m.key for m in matches] == ["tier2_standard"]


def test_a_tier_that_contradicts_the_hire_date_is_refused(tmp_path: Path) -> None:
    bad = TIER2.replace("hire_date: 2013-06-01", "hire_date: 2018-06-01")      # a Tier 3 date
    with pytest.raises(ValueError, match="tier 2 .* hire date 2018-06-01"):
        load_fixtures(_dir(tmp_path, a=bad), date(2026, 9, 30))


def test_a_stated_basis_allows_a_tier_the_date_alone_would_not_give(tmp_path: Path) -> None:
    text = TIER2.replace("hire_date: 2013-06-01\ntier: 2",
                         "hire_date: 2014-06-01\ntier: 1\ntier_basis: first joined a reciprocal system before 2011")
    record, _ = load_fixtures(_dir(tmp_path, a=text), date(2026, 9, 30))["tier2_standard"]
    assert record.tier == 1


def test_relative_hire_dates_are_refused(tmp_path: Path) -> None:
    text = TIER2.replace("hire_date: 2013-06-01", "hired_years_ago: 13")
    with pytest.raises(ValueError, match="hired_years_ago"):
        load_fixtures(_dir(tmp_path, a=text), date(2026, 9, 30))
