"""Two agents behind one flag, and the seam that keeps them apart.

The pipeline is the default and must stay exactly as it is: every existing eval
and test measures it, and the loop is only worth having if the comparison is
honest.
"""

from __future__ import annotations

from pension_chat.agent import loop as loop_mod
from pension_chat.agent.nodes import ANSWER_SYSTEM
from pension_chat.config import Settings


def test_pipeline_is_the_default():
    assert Settings().agent_mode == "pipeline"


async def test_compose_continues_the_loops_history_rather_than_restarting():
    """The whole diagnosis in one assertion.

    A fresh call would hand a model that never saw the statute a pile of text
    and ask it to narrate -- which is the two-minds split this phase exists to
    remove. The model that read the section is the model that writes about it.
    """
    loop_messages = [
        {"role": "system", "content": "investigate"},
        {"role": "user", "content": "what is Article 11?"},
        {"role": "assistant", "tool_calls": [{"id": "c1", "type": "function",
                                              "function": {"name": "outline", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "### 40 ILCS 5/11-101 — Creation of fund"},
    ]

    out = await loop_mod.compose({"question": "what is Article 11?", "loop_messages": loop_messages})
    messages = out["answer_messages"]

    assert messages[: len(loop_messages)] == loop_messages, "the investigation is carried, not replaced"
    assert any("11-101" in str(m.get("content", "")) for m in messages), "the evidence it read is still there"


async def test_compose_keeps_the_answer_style_rules():
    out = await loop_mod.compose({"question": "q", "loop_messages": []})
    tail = "\n".join(str(m.get("content", "")) for m in out["answer_messages"])

    assert "40 ILCS 5/11-134" in tail, "the citation rule survives"
    assert "binding determination" in tail, "so does the closing line"
    assert ANSWER_SYSTEM in tail


async def test_compose_reports_what_was_never_read():
    """Exhaustion is not a licence to answer: prose can still conclude on law
    that was never opened, even when a figure cannot be fabricated."""
    out = await loop_mod.compose(
        {"question": "q", "loop_messages": [], "loop_exhausted": True,
         "unread_references": ["11-216"]}
    )
    tail = "\n".join(str(m.get("content", "")) for m in out["answer_messages"])

    assert "11-216" in tail
    assert "could not" in tail.lower() or "not read" in tail.lower()


async def test_compose_says_nothing_about_gaps_when_there_are_none():
    out = await loop_mod.compose({"question": "q", "loop_messages": [], "unread_references": []})
    tail = "\n".join(str(m.get("content", "")) for m in out["answer_messages"])
    assert "never read" not in tail.lower()


async def test_compose_offers_the_citations_the_verifier_and_the_ui_read():
    """The loop's answer cited two sections and `citations` came back empty.

    `prepare_answer` builds that list from the retrieved sections; `compose` has
    to as well, or the evidence drawer has nothing to open and the `done` event
    carries no verified citations at all. Found by streaming a real turn, not by
    reading the code.
    """
    out = await loop_mod.compose(
        {
            "question": "q",
            "loop_messages": [],
            "retrieved_sections": [
                {"section": "11-101", "title": "Creation of fund", "citation": "40 ILCS 5/11-101"},
                {"section": "11-102", "title": "Terms defined"},
            ],
        }
    )

    assert [c["section"] for c in out["citations"]] == ["11-101", "11-102"]
    assert out["citations"][1]["citation"] == "40 ILCS 5/11-102", "built when absent"
    assert out["citations"][0]["id"] == "11-101", "the drawer keys on id"


async def test_compose_refuses_to_let_an_unread_answer_speak_for_the_code():
    """If the loop insisted on answering having read nothing, the answer must
    not claim what the Code says. This is the deterministic half of the guard;
    the loop's push back is the persuasive half."""
    out = await loop_mod.compose(
        {"question": "q", "loop_messages": [], "consulted_nothing": True, "retrieved_sections": []}
    )
    tail = "\n".join(str(m.get("content", "")) for m in out["answer_messages"])
    assert "did not read" in tail.lower() or "have not read" in tail.lower()
    assert "memory" in tail.lower()


async def test_compose_names_exactly_what_may_be_cited():
    """The loop read an outline, saw section 11-124 listed, and cited it without
    ever reading it -- UNVERIFIED_CITATION, answer replaced.

    ANSWER_SYSTEM already says to cite only what is in front of you, and the
    guardrail already catches the breach. Naming the readable set outright is
    the cheap deterministic half: a list is harder to misread than a rule.
    """
    out = await loop_mod.compose(
        {
            "question": "q",
            "loop_messages": [],
            "retrieved_sections": [{"section": "11-101", "title": "Creation of fund"}],
        }
    )
    tail = "\n".join(str(m.get("content", "")) for m in out["answer_messages"])
    assert "11-101" in tail
    assert "only" in tail.lower()


async def test_compose_says_what_the_figure_rested_on_that_nobody_supplied():
    """R03, G19, L05 and G15 all had their answers thrown away for
    ASSUMPTION_NOT_DISCLOSED, and the member saw the fallback.

    `prepare_answer` renders an ASSUMPTIONS block into the answer. `compose`
    rendered nothing, so the model could not disclose an assumption it had no
    way of knowing had been made -- `apply_assumptions` fills the value deep
    inside `resolve_inputs`, several tool calls below the conversation.
    """
    out = await loop_mod.compose(
        {
            "question": "what would my pension be?",
            "loop_messages": [],
            "calculation_result": {"retirement_annuity": {"base": {"ok": True}}},
            "assumptions": {
                "withdrawal_date": {
                    "value": "2026-09-13",
                    "source": "assumed",
                    "basis": "the date you stop working, taken as today",
                }
            },
        }
    )
    tail = "\n".join(str(m.get("content", "")) for m in out["answer_messages"])
    assert "the date you stop working, taken as today" in tail
    assert "assum" in tail.lower()


async def test_compose_discloses_nothing_when_nothing_was_assumed():
    out = await loop_mod.compose(
        {
            "question": "q",
            "loop_messages": [],
            "calculation_result": {"retirement_annuity": {"base": {"ok": True}}},
            "assumptions": {
                "service_years": {"value": "21.5", "source": "stated", "basis": ""}
            },
        }
    )
    tail = "\n".join(str(m.get("content", "")) for m in out["answer_messages"])
    assert "What I assumed" not in tail


async def test_compose_does_not_disclose_assumptions_beside_no_figure():
    """The rule is "a figure resting on a value nobody supplied must say so".
    With no figure there is nothing resting on anything, and a disclosure
    reads as a hedge about an answer that never needed one."""
    out = await loop_mod.compose(
        {
            "question": "what is a refund?",
            "loop_messages": [],
            "assumptions": {
                "withdrawal_date": {
                    "value": "2026-09-13",
                    "source": "assumed",
                    "basis": "the date you stop working, taken as today",
                }
            },
        }
    )
    tail = "\n".join(str(m.get("content", "")) for m in out["answer_messages"])
    assert "taken as today" not in tail
