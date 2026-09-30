"""Who a turn is about -- spec §4.2, one test per rule -- and the choice replies."""
from __future__ import annotations

from pension_chat.agent import nodes
from pension_chat.agent.graph import decline, subject_suggestion_message
from pension_chat.agent.people import decide
from pension_chat.records import EmployeeMatch, RecordUnavailable, use_provider


def _p(ref, first, last, dept="Finance"):
    return {"ref": ref, "display_name": f"{first} {last}", "first_name": first, "last_name": last,
            "department": dept, "employer": "City", "birth_year": 1964}


A, B, C = _p("M-1", "KA_LS", "MA_LP"), _p("M-2", "QW_XZ", "PL_MR", "Water"), _p("M-3", "RO_BK", "TA_NE")


def test_familiar_names_need_no_query() -> None:
    d = decide("compare qw_xz pl_mr and KA_LS MA_LP", [A, B], [], [])
    assert (d.people, d.unfamiliar, d.which_of) == (["M-2", "M-1"], [], [])


def test_an_unfamiliar_name_makes_a_choice_turn_and_familiar_ones_ride_along() -> None:
    d = decide("compare KA_LS MA_LP and LI_MO GA_RD", [A], [], [("KA_LS", "MA_LP"), ("LI_MO", "GA_RD")])
    assert d.unfamiliar == [("LI_MO", "GA_RD")] and d.people == ["M-1"]


def test_no_name_continues_with_the_previous_turns_people() -> None:
    assert decide("and at 65?", [A, B], ["M-1", "M-2"], []).people == ["M-1", "M-2"]


def test_a_singular_pronoun_after_a_comparison_asks_which() -> None:
    d = decide("what would she get at 65?", [A, B], ["M-1", "M-2"], [])
    assert d.people == [] and d.which_of == ["M-1", "M-2"]
    assert decide("what would she get at 65?", [A, B], ["M-1"], []).people == ["M-1"]


def test_nothing_before_means_the_most_recent_person_or_nobody() -> None:
    assert decide("what is their tier?", [B, A], [], []).people == ["M-2"]
    assert decide("what is Tier 2?", [], [], []).people == []


def test_a_removed_person_is_not_continued_with() -> None:
    assert decide("and at 65?", [A], ["M-1", "M-2"], []).people == ["M-1"]


def test_two_people_in_context_with_the_same_name_ask_which() -> None:
    a2 = _p("M-9", "KA_LS", "MA_LP", "Streets")
    assert decide("KA_LS MA_LP at 62?", [A, a2], [], []).which_of == ["M-1", "M-9"]


def test_at_most_four_people_in_a_turn() -> None:
    many = [_p(f"M-{i}", f"A{i}_X", f"B{i}_Y") for i in range(5)]
    text = " and ".join(p["display_name"] for p in many)
    assert len(decide(text, many, [], []).people) == 4


class _Stub:
    def __init__(self, count=1, fail=False):
        self.count, self.fail, self.searched = count, fail, []

    def names_in(self, text):
        return [("LI_MO", "GA_RD")] if "LI_MO GA_RD" in text.upper() else []

    async def find_by_name(self, first, last):
        self.searched.append((first, last))
        if self.fail:
            raise RecordUnavailable("down")
        return [EmployeeMatch(f"M-{i:06d}", f"{first} {last}", "City", "active", f"Dept {i % 3}", 1960 + i,
                              first, last) for i in range(self.count)]

    async def find_employees(self, query, scope=None):
        return []

    async def get_record(self, key):
        raise AssertionError("a choice turn must not load a record")

    async def get_salary_history(self, key):
        raise AssertionError("a choice turn must not load a record")


def _state(q, subjects=(), last=()):
    return {"question": q, "messages": [q], "subjects": list(subjects), "last_people": list(last)}


async def test_triage_offers_one_card_per_unfamiliar_name() -> None:
    with use_provider(_Stub(count=3)):
        out = await nodes.triage(_state("compare KA_LS MA_LP and LI_MO GA_RD at 62", [A]))
    s = out["subject_suggestion"]
    assert out["out_of_scope_reason"] == "subject_suggestion" and out["turn_people"] == ["M-1"]
    assert s["kind"] == "choose" and s["question"].startswith("compare")
    assert [(c["first_name"], len(c["candidates"])) for c in s["choices"]] == [("LI_MO", 3)]


async def test_triage_decides_familiar_people_without_a_query() -> None:
    stub = _Stub()
    with use_provider(stub):
        out = await nodes.triage(_state("and KA_LS MA_LP at 65?", [A, B]))
    assert out["turn_people"] == ["M-1"] and out.get("subject_suggestion") is None and stub.searched == []


async def test_triage_which_one_uses_the_roster_and_no_query() -> None:
    stub = _Stub()
    with use_provider(stub):
        out = await nodes.triage(_state("what would he get?", [A, B], ["M-1", "M-2"]))
    s = out["subject_suggestion"]
    assert s["kind"] == "which_one" and [c["ref"] for c in s["choices"][0]["candidates"]] == ["M-1", "M-2"]
    assert stub.searched == []


async def test_without_roster_or_name_search_triage_is_unchanged() -> None:
    out = await nodes.triage(_state("What is Tier 2?"))
    assert "turn_people" not in out and out.get("subject_suggestion") is None


def _choice(count, unavailable=False, first="LI_MO", last="GA_RD"):
    return {"first_name": first, "last_name": last, "unavailable": unavailable,
            "candidates": [{"ref": f"M-{i}", "display_name": f"{first} {last}", "department": "Finance",
                            "employer": "City", "birth_year": 1964} for i in range(count)]}


def test_the_messages() -> None:
    one = subject_suggestion_message({"kind": "choose", "question": "q", "choices": [_choice(1)]})
    assert one.startswith("Did you mean LI_MO GA_RD (Finance, born 1964)?") and "M-0" not in one
    assert "There are 9 active employees named LI_MO GA_RD" in subject_suggestion_message(
        {"kind": "choose", "question": "q", "choices": [_choice(9)]})
    assert "which department" in subject_suggestion_message(
        {"kind": "choose", "question": "q", "choices": [_choice(40)]})
    assert "couldn't find an active employee" in subject_suggestion_message(
        {"kind": "choose", "question": "q", "choices": [_choice(0)]})
    assert "not answering" in subject_suggestion_message(
        {"kind": "choose", "question": "q", "choices": [_choice(0, True)]})
    two = subject_suggestion_message(
        {"kind": "choose", "question": "q", "choices": [_choice(1), _choice(3, first="RO_BK")]})
    assert "LI_MO GA_RD" in two and "RO_BK GA_RD" in two and "Once you've chosen" in two
    which = subject_suggestion_message({"kind": "which_one", "question": "q",
                                        "choices": [{"candidates": [A, B], "unavailable": False}]})
    assert which == "Which of them do you mean: KA_LS MA_LP or QW_XZ PL_MR? Choose below."


async def test_decline_answers_the_suggestion_without_a_model() -> None:
    out = await decline({"out_of_scope_reason": "subject_suggestion",
                         "subject_suggestion": {"kind": "choose", "question": "q", "choices": [_choice(9)]}})
    assert "9 active employees" in out["final_answer"]
