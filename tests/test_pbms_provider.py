"""PBMS rows become the EmployeeRecord the agent reads; gaps stay gaps. Synthetic rows only."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from pension_chat.pbms.provider import (
    PbmsProvider,
    match_from_row,
    reconcile_tier,
    record_from_rows,
    salary_from_rows,
)
from pension_chat.records import (
    RecordUnavailable,
    current_provider,
    normalise_meab_id,
    set_default_provider,
    use_provider,
)


@pytest.fixture(autouse=True)
def _no_pbms_environment(monkeypatch):
    # Some tests import scripts that call load_dotenv(), which copies .env into
    # os.environ; these settings must come only from what each test passes.
    for name in ("RECORD_SOURCE", "PBMS_SQL_SERVER", "PBMS_SQL_DATABASE", "PBMS_SQL_USER", "PBMS_SQL_PASSWORD"):
        monkeypatch.delenv(name, raising=False)

ROW = {
    "MEAB_Id": "M-000123", "First_Name": "QW_XZ", "Last_Name": "PL_MR", "Birth_Year": 1964,
    "Birth_Date": datetime(1964, 9, 30), "Hire_Date": datetime(2012, 3, 1), "First_Payroll_Date": None,
    "Seperation_Date": None, "Employee_Status_Cd": "A", "Tier_Type_Cd": "2", "Reciprocal_Flg": "N",
    "ER_Type_Cd": "CITY", "Er_Type_Short_Desc": "City of Chicago", "Department_Short_Desc": "Finance",
}
PAY = [
    {"Year": 2024, "Salary": Decimal("88000.00"), "Contribution_Months": 12},
    {"Year": 2025, "Salary": Decimal("90000.00"), "Contribution_Months": 12},
    {"Year": 2026, "Salary": Decimal("67500.00"), "Contribution_Months": 9},
]


@pytest.mark.parametrize("raw, want", [
    ("M-012345", "M-012345"), (" m-012345 ", "M-012345"), ("M-12345", None), ("E-012345", None), (None, None),
])
def test_meab_ids_are_normalised(raw, want) -> None:
    assert normalise_meab_id(raw) == want


def test_a_full_row_maps_to_the_record() -> None:
    rec = record_from_rows(ROW, PAY)
    assert (rec.key, rec.display_name, rec.employer, rec.department) == (
        "M-000123", "QW_XZ PL_MR", "City of Chicago", "Finance")
    assert rec.birth_date == date(1964, 9, 30) and rec.hire_date == date(2012, 3, 1)
    assert (rec.tier, rec.status, rec.source) == (2, "active", "PBMS")
    assert rec.service_years == Decimal("2.75")                  # 33 months
    assert rec.service_source == "counted from payroll (months with a pension deduction)"
    assert rec.missing == {}


def test_no_hire_date_uses_the_first_payroll_date_and_says_so() -> None:
    rec = record_from_rows({**ROW, "Hire_Date": None, "First_Payroll_Date": datetime(2012, 4, 15)}, PAY)
    assert rec.hire_date == date(2012, 4, 15)
    assert "first payroll date (2012-04-15)" in rec.missing["hire_date"]


def test_no_dates_at_all_leaves_them_absent() -> None:
    rec = record_from_rows({**ROW, "Hire_Date": None, "First_Payroll_Date": None, "Birth_Date": None}, [])
    assert rec.hire_date is None and rec.birth_date is None
    assert set(rec.missing) >= {"hire_date", "birth_date", "service_years"}


@pytest.mark.parametrize("code, start, want_tier, note_has", [
    ("2", date(2012, 3, 1), 2, None),
    ("2", date(2019, 1, 1), 2, "indicates Tier 3"),
    ("0", date(2005, 1, 1), 1, "tier code '0'"),
    ("", date(2017, 8, 1), None, "does not settle it"),
    ("3", date(2017, 8, 1), 3, None),
    ("T", None, None, "not on file"),
])
def test_tier_is_reconciled_with_the_dates(code, start, want_tier, note_has) -> None:
    tier, note = reconcile_tier(code, start)
    assert tier == want_tier
    assert (note is None) if note_has is None else (note_has in note)


def test_an_unmapped_status_is_reported_not_guessed() -> None:
    rec = record_from_rows({**ROW, "Employee_Status_Cd": "DB4"}, PAY)
    assert rec.status is None and "DB4" in rec.missing["employment_status"]


def test_reciprocal_flag_is_reported_as_a_gap() -> None:
    assert "reciprocal" in record_from_rows({**ROW, "Reciprocal_Flg": "Y"}, PAY).missing["reciprocal_service_years"]


def test_the_current_year_is_left_out_of_salary_history() -> None:
    assert [h.year for h in salary_from_rows(PAY, as_of=date(2026, 9, 30))] == [2024, 2025]


def test_a_match_carries_what_the_choice_card_shows() -> None:
    m = match_from_row(ROW)
    assert (m.key, m.display_name, m.employer, m.department, m.status, m.birth_year) == (
        "M-000123", "QW_XZ PL_MR", "City of Chicago", "Finance", "active", 1964)


class FakeClient:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    async def fetch(self, name, **params):
        self.calls.append((name, params))
        if self.fail:
            raise RecordUnavailable("down")
        if name == "salary_years":
            return PAY
        return [ROW] if params.get("meab_id") == "M-000123" else []

    async def warm(self, connections=4):
        pass

    async def close(self):
        pass


async def test_the_provider_reads_record_and_salary_and_caches_salary() -> None:
    client = FakeClient()
    provider = PbmsProvider(client, today=lambda: date(2026, 9, 30))
    record = await provider.get_record(" m-000123 ")
    history = await provider.get_salary_history("M-000123")
    assert record is not None and record.key == "M-000123"
    assert [h.year for h in history] == [2024, 2025]
    assert [c[0] for c in client.calls].count("salary_years") == 1


async def test_a_key_that_is_not_a_meab_id_is_no_record_and_no_query() -> None:
    client = FakeClient()
    assert await PbmsProvider(client).get_record("3f1c9e2a-a-profile-uuid") is None
    assert await PbmsProvider(client).find_employees("QW_XZ PL_MR") == []
    assert client.calls == []


async def test_unavailable_propagates() -> None:
    with pytest.raises(RecordUnavailable):
        await PbmsProvider(FakeClient(fail=True)).get_record("M-000123")


def test_a_context_provider_outranks_the_default() -> None:
    default, scoped = object(), object()
    set_default_provider(default)  # type: ignore[arg-type]
    try:
        assert current_provider() is default
        with use_provider(scoped):  # type: ignore[arg-type]
            assert current_provider() is scoped
    finally:
        set_default_provider(None)
    assert current_provider() is None


def _settings(**overrides):
    from pension_chat.config import Settings

    base = dict(azure_openai_api_key="k", azure_openai_endpoint="https://x", azure_search_api_key="k",
                azure_search_endpoint="https://x", pghost="h", pguser="u", pgpassword="p", _env_file=None)
    return Settings(**{**base, **overrides})


def test_no_provider_unless_pbms_and_never_half_configured() -> None:
    from pension_chat.pbms import build_record_provider

    assert build_record_provider(_settings()) is None
    with pytest.raises(RuntimeError, match="PBMS_SQL_SERVER"):
        build_record_provider(_settings(record_source="pbms"))
    assert build_record_provider(_settings(
        record_source="pbms", pbms_sql_server="s", pbms_sql_database="d",
        pbms_sql_user="u", pbms_sql_password="p")) is not None
