"""The calculation chain, offered to the loop as one tool.

Nothing here computes. `compute_benefit` runs `resolve_recipes`,
`resolve_inputs`, `validate_recipes` and `execute_calculations` in that order,
which is a correctness constraint rather than a convenience: resolve_inputs
needs the recipe to know which inputs exist, and execute needs the validator's
verdict. The model chooses when to compute and on what premise; the arithmetic
stays exactly where it was.

Offline: the four nodes are faked, because what is under test is the wiring and
what the model is told -- not the interpreter, which has its own tests.
"""

from __future__ import annotations

from pension_chat.agent import tools


def _chain(monkeypatch, *, recipes=None, inputs=None, missing=None, validation=None, result=None):
    """Fake the four nodes, recording what each one saw."""
    seen: list[tuple[str, dict]] = []

    async def fake_recipes(state):
        seen.append(("resolve_recipes", dict(state)))
        return {"recipes": recipes if recipes is not None else {"retirement_annuity": {"section": "11-134"}}}

    async def fake_inputs(state):
        seen.append(("resolve_inputs", dict(state)))
        return {
            "resolved_inputs": inputs if inputs is not None else {"service_years": "21.5"},
            "missing_inputs": missing or [],
            "input_sources": {"service_years": "record"},
        }

    async def fake_validate(state):
        seen.append(("validate_recipes", dict(state)))
        return validation if validation is not None else {"can_execute": True, "validation_errors": []}

    async def fake_execute(state):
        seen.append(("execute_calculations", dict(state)))
        return {
            "calculation_result": result
            if result is not None
            else {
                "retirement_annuity": {
                    "section": "11-134",
                    "base": {"ok": True, "annual_amount": "32126.78", "monthly_amount": "2677.23"},
                }
            }
        }

    monkeypatch.setattr(tools, "resolve_recipes", fake_recipes)
    monkeypatch.setattr(tools, "resolve_inputs", fake_inputs)
    monkeypatch.setattr(tools, "validate_recipes", fake_validate)
    monkeypatch.setattr(tools, "execute_calculations", fake_execute)
    return seen


async def test_a_figure_comes_back_with_its_section_and_inputs(monkeypatch):
    _chain(monkeypatch)
    text, update = await tools.run_tool(
        "compute_benefit", {"benefit": "retirement_annuity"}, {"question": "q"}
    )
    assert "32,126.78" in text or "32126.78" in text
    assert "11-134" in text
    assert "service_years" in text


async def test_the_chain_runs_in_order_each_seeing_the_last(monkeypatch):
    """Ordering is a correctness constraint, not a style choice.

    resolve_inputs must see the recipes resolve_recipes wrote, or it has no
    input specifications to resolve against and reports everything missing.
    """
    seen = _chain(monkeypatch)
    await tools.run_tool("compute_benefit", {"benefit": "retirement_annuity"}, {"question": "q"})

    assert [name for name, _ in seen] == [
        "resolve_recipes",
        "resolve_inputs",
        "validate_recipes",
        "execute_calculations",
    ]
    _, inputs_state = seen[1]
    assert inputs_state["recipes"], "resolve_inputs saw what resolve_recipes wrote"
    _, execute_state = seen[3]
    assert execute_state["can_execute"] is True, "execute saw the validator's verdict"


async def test_missing_inputs_are_named_not_swallowed(monkeypatch):
    """What lets the model ask for one thing rather than run a form."""
    _chain(
        monkeypatch,
        missing=[{"name": "final_average_salary", "ask_for": "your salary for the last ten years"}],
        result={"retirement_annuity": {"base": {"ok": False}}},
    )
    text, _ = await tools.run_tool(
        "compute_benefit", {"benefit": "retirement_annuity"}, {"question": "q"}
    )
    assert "your salary for the last ten years" in text


async def test_a_blocked_execution_says_why(monkeypatch):
    """"The provision does not reach you" is an answer a member needs."""
    _chain(
        monkeypatch,
        validation={
            "can_execute": False,
            "execution_blocked": "applicability_not_satisfied",
            "validation_errors": [{"detail": "needs 20 years of service"}],
        },
        result={},
    )
    text, _ = await tools.run_tool(
        "compute_benefit", {"benefit": "retirement_annuity"}, {"question": "q"}
    )
    assert "applicability_not_satisfied" in text or "20 years" in text


async def test_a_retirement_age_becomes_a_scenario(monkeypatch):
    """The projection path Phase 2a built, reached from the loop."""
    seen = _chain(monkeypatch)
    await tools.run_tool(
        "compute_benefit",
        {"benefit": "retirement_annuity", "retirement_age": 58},
        {"question": "q"},
    )
    _, execute_state = seen[3]
    assert execute_state["scenarios"] == [{"label": "at age 58", "retirement_age": 58}]


async def test_an_offset_is_carried_unresolved(monkeypatch):
    """`_resolve_retirement_age` turns "two more years" into an age against the
    member's real one, at execution time. This tool must not guess it."""
    seen = _chain(monkeypatch)
    await tools.run_tool(
        "compute_benefit",
        {"benefit": "retirement_annuity", "years_from_now": 2},
        {"question": "q"},
    )
    _, execute_state = seen[3]
    assert execute_state["scenarios"][0]["years_from_now"] == 2
    assert execute_state["scenarios"][0].get("retirement_age") is None


async def test_an_unknown_benefit_is_reported_not_raised():
    text, update = await tools.run_tool("compute_benefit", {"benefit": "lottery_win"}, {})
    assert "lottery_win" in text
    assert "retirement_annuity" in text, "say what it could have asked for"
    assert update == {}


async def test_the_state_update_carries_what_the_guardrail_reads(monkeypatch):
    _chain(monkeypatch)
    _, update = await tools.run_tool(
        "compute_benefit", {"benefit": "retirement_annuity"}, {"question": "q"}
    )
    assert "calculation_result" in update, "the key _collect_allowed_numbers walks"
    assert "input_sources" in update, "the card shows where each input came from"
    assert update["recipes"], "recipe_provenance and the card need it"


async def test_a_stand_in_reaches_the_chain_only_when_the_member_invited_it(monkeypatch):
    """Under a supposition the record is starved and every input comes back
    missing, which is right until the member says "with my salary and service"."""
    seen = _chain(monkeypatch)
    await tools.run_tool(
        "compute_benefit",
        {"benefit": "retirement_annuity", "stand_in_facts": True},
        {"question": "if I were a Chicago police officer with my salary and service?"},
    )
    _, inputs_state = seen[1]
    assert inputs_state["stand_in_facts"] is True

    seen = _chain(monkeypatch)
    await tools.run_tool("compute_benefit", {"benefit": "retirement_annuity"}, {"question": "q"})
    _, inputs_state = seen[1]
    assert inputs_state["stand_in_facts"] is False, "off unless asked for, every call"


async def test_a_borrowed_input_is_named_to_the_model(monkeypatch):
    """The figure is real; whose values it rests on is the part an answer
    quietly drops, and dropping it turns "their numbers under another fund's
    formula" into "their pension"."""

    async def fake_inputs(state):
        return {
            "resolved_inputs": {"service_years": "21.5", "age_at_annuity_start": "56"},
            "missing_inputs": [],
            "input_sources": {"service_years": "stand_in", "age_at_annuity_start": "stated"},
        }

    _chain(monkeypatch)
    monkeypatch.setattr(tools, "resolve_inputs", fake_inputs)
    text, _ = await tools.run_tool(
        "compute_benefit",
        {"benefit": "retirement_annuity", "stand_in_facts": True},
        {"question": "q"},
    )
    assert "service_years" in text
    assert "standing in" in text.lower()
    assert "age_at_annuity_start" not in text.split("standing in")[1]
