"""People in context: labels, parallel loading, per-person pins, and one-person turns unchanged."""
from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal

from pension_chat.agent import nodes
from pension_chat.agent.card import calculation_card
from pension_chat.agent.loop import _context, _record_notes
from pension_chat.agent.people import labels
from pension_chat.agent.state import DURABLE_KEYS, initial_state, turn_input
from pension_chat.agent.worksheet import WorkSheet
from pension_chat.records import EmployeeRecord, RecordUnavailable, SalaryYear, use_provider

A = {"ref": "M-000001", "display_name": "KA_LS MA_LP", "first_name": "KA_LS", "last_name": "MA_LP",
     "department": "Finance", "employer": "City", "birth_year": 1964}
B = {"ref": "M-000002", "display_name": "QW_XZ PL_MR", "first_name": "QW_XZ", "last_name": "PL_MR",
     "department": "Water", "employer": "City", "birth_year": 1970}
A2 = {**A, "ref": "M-000003", "department": "Streets"}


class _Stub:
    def __init__(self, fail=()):
        self.asked, self.fail, self.concurrent, self.peak = [], set(fail), 0, 0

    async def find_employees(self, query, scope=None):
        return []

    async def get_record(self, key):
        self.asked.append(key)
        self.concurrent += 1
        self.peak = max(self.peak, self.concurrent)
        await asyncio.sleep(0.01)
        self.concurrent -= 1
        if key in self.fail:
            raise RecordUnavailable("down")
        return EmployeeRecord(key=key, tier=2, birth_date=date(1964, 9, 30), hire_date=date(2012, 3, 1),
                              display_name="X", department="D", status="active",
                              service_years=Decimal("14"), service_source="counted from payroll",
                              source="PBMS", missing={"tier": "note"} if key == "M-000001" else {})

    async def get_salary_history(self, key):
        return [SalaryYear(2025, Decimal({"M-000001": 90000, "M-000002": 60000}.get(key, 70000)))]


def test_labels_add_the_department_only_when_names_collide() -> None:
    assert labels([A, B]) == {"M-000001": "KA_LS MA_LP", "M-000002": "QW_XZ PL_MR"}
    assert labels([A, A2]) == {"M-000001": "KA_LS MA_LP (Finance)", "M-000003": "KA_LS MA_LP (Streets)"}


def test_the_roster_is_carried_every_turn_and_last_people_is_durable() -> None:
    assert initial_state("q", "s", subjects=[A])["subjects"] == [A]
    assert turn_input("q", "s", subjects=[A])["subjects"] == [A]
    assert {"last_people", "pinned_people"} <= DURABLE_KEYS
    assert "turn_people" not in DURABLE_KEYS and "people" not in DURABLE_KEYS


async def test_several_people_load_in_parallel_and_the_first_fills_the_single_member_keys() -> None:
    stub = _Stub()
    with use_provider(stub):
        out = await nodes.fetch_employee_record({"turn_people": ["M-000001", "M-000002"], "subjects": [A, B]})
    assert stub.peak == 2
    assert set(out["people"]) == {"M-000001", "M-000002"}
    assert out["people"]["M-000002"]["label"] == "QW_XZ PL_MR"
    assert out["employee_record"]["id"] == "M-000001"
    assert out["last_people"] == ["M-000001", "M-000002"]
    assert set(out["pinned_people"]) == {"M-000001", "M-000002"}


async def test_a_pinned_person_is_not_read_again() -> None:
    stub = _Stub()
    pinned = {"M-000001": {"record": {"id": "M-000001", "article_number": 8, "tier": 2}, "salary_history": []}}
    with use_provider(stub):
        await nodes.fetch_employee_record({"turn_people": ["M-000001", "M-000002"], "subjects": [A, B],
                                           "pinned_people": pinned})
    assert stub.asked == ["M-000002"]


async def test_one_unreadable_record_is_reported_and_the_others_load() -> None:
    with use_provider(_Stub(fail={"M-000002"})):
        out = await nodes.fetch_employee_record({"turn_people": ["M-000001", "M-000002"], "subjects": [A, B]})
    assert set(out["people"]) == {"M-000001"} and out["unavailable_people"] == ["M-000002"]


async def test_without_turn_people_the_existing_path_runs(monkeypatch) -> None:
    called = []

    async def fake_load(employee_id):
        called.append(employee_id)
        return None

    monkeypatch.setattr(nodes, "_load_record", fake_load)
    await nodes.fetch_employee_record({"employee_id": "abc"})
    assert called == ["abc"]


def test_the_context_names_whose_record_could_not_be_read() -> None:
    state = {**initial_state("q", "s", subjects=[A, B]), "unavailable_people": ["M-000002"]}
    assert "QW_XZ PL_MR" in _context(state) and "could not be read" in _context(state)


def test_the_record_notes_name_the_employee_not_the_internal_key() -> None:
    notes = _record_notes({"id": "M-000123", "display_name": "QW_XZ PL_MR", "department": "Finance",
                           "record_source": "PBMS", "service_source": "counted from payroll",
                           "missing": {"tier": "PBMS records Tier 2, but ..."}})
    assert "QW_XZ PL_MR" in notes and "Finance" in notes and "tier: PBMS records Tier 2" in notes
    assert "M-000123" not in notes


def test_the_card_names_the_pbms_record_without_its_key() -> None:
    state = {**initial_state("q", "s"),
             "employee_record": {"id": "M-000123", "article_number": 8, "record_source": "PBMS"},
             "calculation_result": {"r": {"base": {"ok": True, "inputs_used": {}, "annual_amount": "1"}}}}
    card = calculation_card(state, WorkSheet.from_state(state))
    assert card is not None and card["record_label"] == "PBMS record"


def test_an_employer_is_never_addressed_as_the_employee() -> None:
    from pension_chat.agent.loop import _attribution

    notes = _record_notes({"id": "M-1", "display_name": "QW_XZ PL_MR", "record_source": "PBMS"})
    assert "employer" in notes and "never as 'you'" in notes
    coverage = {"article": "8", "basis": "record"}
    assert _attribution({"coverage": coverage, "employee_record": {"record_source": "PBMS"}}) == ""
    assert "your record" in _attribution({"coverage": coverage, "employee_record": {}})


def test_compose_is_told_who_the_answer_is_for() -> None:
    from pension_chat.agent.loop import _audience

    one = {"employee_record": {"id": "M-1", "record_source": "PBMS", "display_name": "QW_XZ PL_MR"}}
    text = _audience(one)
    assert "employer" in text and "QW_XZ PL_MR" in text and "stops working" in text and "never" in text
    both = {**one, "people": {"M-1": {"label": "QW_XZ PL_MR"}, "M-2": {"label": "KA_LS MA_LP"}}}
    assert "KA_LS MA_LP" in _audience(both)
    assert _audience({"employee_record": {"id": "x"}}) == ""        # a member's own conversation: unchanged


def test_a_stated_salary_may_stand_in_for_a_fund_records_gap_in_a_one_person_turn() -> None:
    from pension_chat.agent.nodes import _may_take_stated_salary

    fund = {"employee_record": {"id": "M-1", "record_source": "PBMS"}}
    assert _may_take_stated_salary({})                                            # no record: as before
    assert _may_take_stated_salary(fund)
    assert _may_take_stated_salary({**fund, "people": {"M-1": {}}})
    assert not _may_take_stated_salary({**fund, "people": {"M-1": {}, "M-2": {}}})  # whose salary? never guess
    assert not _may_take_stated_salary({"employee_record": {"id": "member-own"}})  # a member's own record


def test_the_context_says_when_names_could_not_be_looked_up() -> None:
    state = {**initial_state("q", "s"), "names_unavailable": True}
    assert "could not be looked up" in _context(state)
    assert "could not be looked up" not in _context(initial_state("q", "s"))


def test_with_names_unavailable_the_answer_speaks_to_the_employer() -> None:
    # Demo check 6: "I can't give your pension amount" to an employer asking
    # about an employee whose record could not be read.
    from pension_chat.agent.loop import _audience

    said = _audience({**initial_state("q", "s"), "names_unavailable": True})
    assert "employer" in said and "never 'you' or 'your'" in said and "could not be read" in said
    assert _audience(initial_state("q", "s")) == ""
