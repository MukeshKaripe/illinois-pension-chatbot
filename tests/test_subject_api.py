"""The conversation's people: choose, remove, recency, and the audit -- at the helpers the routes wrap."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi import HTTPException

from pension_chat.api.sessions import _subject_trail
from pension_chat.api.subject import add_to_roster, by_recency, remove_from_roster, resolve_ref, roster_entry
from pension_chat.records import MEAB_ID_PATTERN, EmployeeMatch, RecordUnavailable
from pension_chat.sse import sse_people


def _e(ref):
    return {"ref": ref, "display_name": ref, "first_name": "F", "last_name": ref, "department": None,
            "employer": None, "birth_year": None}


class _Stub:
    def __init__(self, matches=(), fail=False):
        self.matches, self.fail, self.asked = list(matches), fail, []

    async def find_employees(self, query, scope=None):
        self.asked.append(query)
        if self.fail:
            raise RecordUnavailable("down")
        return self.matches


A = EmployeeMatch("M-000123", "KA_LS MA_LP", "City of Chicago", "active", "Finance", 1964, "KA_LS", "MA_LP")


async def test_a_ref_is_re_resolved_before_it_is_stored() -> None:
    stub = _Stub([A])
    match = await resolve_ref(stub, " m-000123 ")
    assert stub.asked == ["M-000123"] and roster_entry(match) == {
        "ref": "M-000123", "display_name": "KA_LS MA_LP", "first_name": "KA_LS", "last_name": "MA_LP",
        "department": "Finance", "employer": "City of Chicago", "birth_year": 1964}


@pytest.mark.parametrize("ref, matches, status", [("KA_LS MA_LP", [A], 400), ("M-12", [A], 400), ("M-000999", [], 404)])
async def test_bad_or_unknown_refs(ref, matches, status) -> None:
    with pytest.raises(HTTPException) as caught:
        await resolve_ref(_Stub(matches), ref)
    assert caught.value.status_code == status


async def test_unavailable_is_503_and_no_provider_is_404() -> None:
    with pytest.raises(HTTPException) as down:
        await resolve_ref(_Stub(fail=True), "M-000123")
    with pytest.raises(HTTPException) as off:
        await resolve_ref(None, "M-000123")
    assert (down.value.status_code, off.value.status_code) == (503, 404)


def test_choosing_puts_the_person_first_and_a_fifth_pushes_out_the_least_recent() -> None:
    roster = [_e("M-4"), _e("M-3"), _e("M-2"), _e("M-1")]
    assert [e["ref"] for e in add_to_roster(roster, _e("M-5"))] == ["M-5", "M-4", "M-3", "M-2"]
    assert [e["ref"] for e in add_to_roster(roster, _e("M-2"))] == ["M-2", "M-4", "M-3", "M-1"]


def test_removing_and_recency() -> None:
    roster = [_e("M-1"), _e("M-2"), _e("M-3")]
    assert [e["ref"] for e in remove_from_roster(roster, "M-2")] == ["M-1", "M-3"]
    assert [e["ref"] for e in by_recency(roster, ["M-3", "M-2"])] == ["M-3", "M-2", "M-1"]
    assert by_recency(roster, []) == roster


def test_the_audit_trail_names_the_people_and_any_choice() -> None:
    suggestion = {"kind": "choose", "question": "q", "choices": []}
    state = {"turn_people": ["M-1", "M-2"], "subject_suggestion": suggestion}
    assert _subject_trail(state) == [{"type": "subjects", "refs": ["M-1", "M-2"]},
                                     {"type": "subject_suggestion", **suggestion}]
    assert _subject_trail({}) == []


def test_the_people_event() -> None:
    assert sse_people(["M-1"]) == 'event: people\ndata: {"active": ["M-1"]}\n\n'


def test_the_migration() -> None:
    text = Path("alembic/versions/009_session_subjects.py").read_text(encoding="utf-8")
    assert "subjects" in text and 'down_revision = "008_answer_evidence"' in text
    assert "jsonb_array_length(subjects) <= 4" in text
    assert re.fullmatch(MEAB_ID_PATTERN, "M-000123") and not re.fullmatch(MEAB_ID_PATTERN, "m-000123")
