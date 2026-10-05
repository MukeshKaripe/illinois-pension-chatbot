"""The only SQL the chatbot sends PBMS: read-only, allow-listed, and indexed."""
from __future__ import annotations

import re

import pytest

from pension_chat.pbms.queries import ALLOWED_COLUMNS, REGISTRY, check_statement


def test_the_registry_holds_exactly_the_five_statements() -> None:
    assert set(REGISTRY) == {"name_directory", "resolve_name", "resolve_meab_id", "record", "salary_years"}


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_every_statement_passes_the_guard(name: str) -> None:
    check_statement(REGISTRY[name].sql)


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_placeholders_match_declared_parameters(name: str) -> None:
    assert REGISTRY[name].sql.count("?") == len(REGISTRY[name].params)


@pytest.mark.parametrize("name", ["name_directory", "resolve_name", "resolve_meab_id"])
def test_lookups_cover_active_members_only(name: str) -> None:
    sql = REGISTRY[name].sql
    assert "e.Employee_Status_Cd = 'A'" in sql and "e.Status = 'A'" in sql and "LIKE 'M-%'" in sql


def test_the_directory_load_may_take_longer() -> None:
    assert REGISTRY["name_directory"].timeout_s == 30


@pytest.mark.parametrize("bad", [
    "UPDATE dbo.T_Employees SET Status = 'I'",
    "SELECT 1; DROP TABLE x",
    "SELECT p.Ssn FROM dbo.T_Person_Info p",
    "SELECT e.SSN FROM dbo.T_Employees e",
    "SELECT p.CCLast FROM dbo.T_Person_Info p",
    "SELECT p.PbsEmail FROM dbo.T_Person_Info p",
    "SELECT p.Middle_Name FROM dbo.T_Person_Info p",
    "SELECT l.MEAB_Id INTO #t FROM dbo.T_Pbs_Linker l",
    "EXEC sp_who",
])
def test_the_guard_refuses(bad: str) -> None:
    with pytest.raises(ValueError):
        check_statement(bad)


def test_salary_is_read_through_the_master_table() -> None:
    sql = REGISTRY["salary_years"].sql
    assert "m.Person_Info_Seq_No" in sql
    assert not re.search(r"\bd\.Person_Info_Seq_No\b", sql)
    assert "d.Pen_Ded_Master_Seq_No = m.Pen_Ded_Master_Seq_No" in sql


def test_no_personal_identifier_is_on_the_allow_list() -> None:
    for col in ALLOWED_COLUMNS:
        assert not re.fullmatch(
            r"(?i)e?ssn|cc\w*|pbsaddress\w*|pbsemail|pbs\w*_phone|maiden_name|middle_name|memberid", col)


def test_a_years_salary_prefers_pbms_capped_pensionable_salary() -> None:
    # Active members' recent Gross is empty or fragmentary; Capped_Salary is
    # PBMS's own pensionable salary (12 x its monthly BestSal; deductions are
    # exactly 8.5% of it). Gross stands in only where no capped figure exists,
    # and only when the deductions are plausible for it (spec §6.2).
    sql = REGISTRY["salary_years"].sql
    assert sql.index("SUM(d.Capped_Salary) > 0") < sql.index("SUM(d.Gross)")
    assert "SUM(d.Ded) <= 0.2 * SUM(d.Gross)" in sql
