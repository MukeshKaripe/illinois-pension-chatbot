"""A value the member stated survives the turns between saying and needing it.

The fact ledger was built because "a member who gave their service in turn two
was asked for it again in turn four". It half-worked: `fact_ledger` is written
in exactly one place, `resolve_inputs`, which only runs when a calculation runs.
A value stated on a turn that computed nothing was never recorded at all.

Worse, the turn that finally computes may never read the conversation either.
Extraction is gated on `outstanding or question_has_figures` -- inputs the
*record* could not supply, or digits in the current message -- so when the
record covers everything and the member's correction was three turns ago, the
correction is silently ignored and the figure is computed on the file.

Found by `evals/gold_continuity.yaml` C05 on its first live run: the member said
"I actually have 25 years of service, not what your records say", and two turns
later the annuity was computed on the record's 21.5.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage

from pension_chat.agent import nodes

_RECIPE = {
    "retirement_annuity": {
        "spec": {
            "inputs": [
                {"name": "service_years", "type": "number", "required": True},
            ]
        }
    }
}

_RECORD = {"service_years": 21.5, "age": 56}


def _state(question: str, messages: list, ledger: dict | None = None) -> dict:
    return {
        "question": question,
        "messages": messages,
        "requires_calculation": True,
        "recipes": _RECIPE,
        "member_facts": dict(_RECORD),
        "fact_ledger": ledger or {},
        "turn_index": 3,
    }


async def test_a_correction_two_turns_back_is_read_before_computing(monkeypatch):
    """The turn that computes must consult the conversation, not only the file.

    The decision call reads it and passes the correction as `stated`; no
    separate extraction call runs (spec §5.2).
    """
    asked: list = []

    async def fake_complete(**kwargs):
        asked.append(kwargs)
        return "{}"

    monkeypatch.setattr(nodes, "complete", fake_complete)

    out = await nodes.resolve_inputs({
        **_state(
            "So what would my pension be?",
            [
                HumanMessage(content="I actually have 25 years of service, not 21.5"),
                AIMessage(content="Noted."),
                HumanMessage(content="So what would my pension be?"),
            ],
        ),
        "stated_by_model": {"service_years": 25},
    })

    assert not asked, "a separate extraction call ran"
    assert str(out["resolved_inputs"]["service_years"]) == "25"
    assert out["input_sources"]["service_years"] == "stated"


async def test_what_was_read_is_written_down_for_the_next_turn():
    out = await nodes.resolve_inputs({
        **_state(
            "So what would my pension be?",
            [HumanMessage(content="I actually have 25 years of service")],
        ),
        "stated_by_model": {"service_years": 25},
    })
    held = out["fact_ledger"]["me"]
    assert held["service_years"]["value"] == 25
    assert held["service_years"]["source"] == "stated"


async def test_a_conversation_with_no_figures_in_it_is_not_re_read(monkeypatch):
    """The cost has to be bounded. Nothing numeric was ever said, so there is
    nothing for extraction to find and no reason to spend a call looking."""
    asked: list = []

    async def fake_complete(**kwargs):
        asked.append(kwargs)
        return "{}"

    monkeypatch.setattr(nodes, "complete", fake_complete)

    await nodes.resolve_inputs(
        _state(
            "What would my pension be?",
            [HumanMessage(content="Tell me about early retirement please")],
        )
    )
    assert not asked, "spent a model call on a conversation holding no values"


async def test_a_value_already_on_the_ledger_is_not_extracted_again(monkeypatch):
    """Once it is written down it is read from there. Re-extracting every
    calculating turn would put a model call on the critical path for a value
    the conversation already settled."""
    asked: list = []

    async def fake_complete(**kwargs):
        asked.append(kwargs)
        return '{"resolved": {"service_years": 25}}'

    monkeypatch.setattr(nodes, "complete", fake_complete)

    out = await nodes.resolve_inputs(
        _state(
            "And what would waiting get me?",
            [HumanMessage(content="I actually have 25 years of service")],
            ledger={"me": {"service_years": {"value": 25, "source": "stated", "turn": 1}}},
        )
    )
    assert not asked
    assert str(out["resolved_inputs"]["service_years"]) == "25"
