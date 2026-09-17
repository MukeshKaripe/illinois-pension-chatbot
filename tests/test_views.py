"""Every table this turn can show, rendered before a word is written.

They were being computed and handed to the model as prose to paraphrase, and
nothing kept a copy -- so `card["blocks"]` has been empty since the cutover and
the calculation drawer has had three empty panels in it. `render_worked_steps`
has not run at all on the live path: `loop.py` imports the assumptions, branch
and threshold renderers and never that one.
"""

from __future__ import annotations

from pension_chat.agent.loop import _views

_RESULT = {
    "retirement_annuity": {
        "section": "11-134",
        "base": {
            "ok": True,
            "annual_amount": "38000.00",
            "monthly_amount": "3166.67",
            "inputs_used": {"final_average_salary": "70000.00"},
        },
    }
}


def test_a_turn_that_computed_a_figure_has_worked_steps():
    views = _views({"calculation_result": _RESULT})
    assert "WORKED_STEPS" in views
    assert "70,000" in views["WORKED_STEPS"]
    assert "38,000" in views["WORKED_STEPS"]


def test_a_turn_with_no_figure_has_no_views():
    assert _views({}) == {}
    assert _views({"calculation_result": {}}) == {}


def test_a_failed_calculation_renders_nothing():
    assert _views({"calculation_result": {"x": {"base": {"ok": False}}}}) == {}


def test_assumptions_are_a_view_even_though_the_model_writes_them_too():
    """The drawer needs the copy; the prose is a separate obligation that
    `guardrails._check_assumptions_disclosed` enforces on the answer itself."""
    views = _views(
        {
            "calculation_result": _RESULT,
            "assumptions": {
                "withdrawal_date": {
                    "value": "2026-09-16",
                    "source": "assumed",
                    "basis": "taken as today",
                }
            },
        }
    )
    assert "taken as today" in views["ASSUMPTIONS"]


def test_a_boundary_ahead_is_a_view():
    views = _views(
        {
            "calculation_result": {
                "retirement_annuity": {
                    **_RESULT["retirement_annuity"],
                    "thresholds": [
                        {
                            "boundary": 60,
                            "distance_months": 44,
                            "difference_from_now_annual": "4000.00",
                            "section": "11-134",
                        }
                    ],
                }
            }
        }
    )
    assert "THRESHOLDS" in views
    assert "44 months" in views["THRESHOLDS"]


def test_an_empty_render_is_not_recorded_as_a_view():
    """A slot present with empty content is a blank panel in the drawer, which
    reads to a member as something having gone wrong."""
    views = _views({"calculation_result": _RESULT})
    assert all(text.strip() for text in views.values())
    assert "COMPARISON" not in views, "one figure is not a comparison"


async def test_compose_publishes_the_views_it_rendered():
    """End to end: the drawer's content exists again."""
    from pension_chat.agent import loop as loop_mod

    out = await loop_mod.compose(
        {
            "question": "what would my annuity be?",
            "messages": [],
            "loop_messages": [],
            "calculation_result": _RESULT,
            "retrieved_sections": [],
        }
    )
    assert "70,000" in out["views"]["WORKED_STEPS"]


async def test_a_turn_with_nothing_to_show_publishes_nothing():
    from pension_chat.agent import loop as loop_mod

    out = await loop_mod.compose(
        {
            "question": "what is Article 11?",
            "messages": [],
            "loop_messages": [],
            "retrieved_sections": [],
        }
    )
    assert out["views"] == {}


# ── The model names the view; code fills it ───────────────────────
#
# A block handed over as text to paraphrase is a block whose numbers can be
# transposed -- and two figures swapped between two branches both trace to
# engine output, so no guardrail catches it. A marker has no numbers in it.

_BRANCHED = {
    "retirement_annuity@tier1": {
        "branch": {"fact": "tier", "value": 1},
        "section": "11-134",
        "base": {"ok": True, "annual_amount": "38000.00", "monthly_amount": "3166.67"},
    },
    "retirement_annuity@tier2": {
        "branch": {"fact": "tier", "value": 2},
        "section": "1-160",
        "base": {"ok": True, "annual_amount": "31000.00", "monthly_amount": "2583.33"},
    },
}


async def _composed(state):
    from pension_chat.agent import loop as loop_mod

    return await loop_mod.compose(
        {"question": "q", "messages": [], "loop_messages": [], **state}
    )


def _system(out):
    return [
        str(m.get("content") or "")
        for m in out["answer_messages"]
        if m.get("role") == "system"
    ]


async def test_a_table_is_offered_as_a_marker():
    out = await _composed({"calculation_result": _BRANCHED})
    assert out["answer_blocks"].get("COMPARISON")
    assert any("<<<COMPARISON>>>" in text for text in _system(out))


async def test_the_model_is_told_not_to_write_the_figures():
    out = await _composed({"calculation_result": _BRANCHED})
    assert "do not write the figures" in " ".join(_system(out)).lower()


async def test_the_branch_instruction_survives():
    """The marker replaces the transcription, not the reasoning: the model
    still must not pick one branch or refuse for want of a tier."""
    system = " ".join(_system(await _composed({"calculation_result": _BRANCHED}))).lower()
    assert "do not pick one" in system


async def test_assumptions_stay_prose():
    """`guardrails._check_assumptions_disclosed` requires the *answer* to
    disclose an assumption in its own words, and a block substituted into the
    stream is not the answer saying anything."""
    out = await _composed(
        {
            "calculation_result": _RESULT,
            "assumptions": {
                "withdrawal_date": {
                    "value": "2026-09-16",
                    "source": "assumed",
                    "basis": "taken as today",
                }
            },
        }
    )
    assert "ASSUMPTIONS" not in out["answer_blocks"]
    assert out["views"].get("ASSUMPTIONS"), "the drawer still needs its copy"


async def test_a_turn_with_no_table_registers_no_blocks():
    out = await _composed({})
    assert out["answer_blocks"] == {}


async def test_every_registered_block_is_one_the_model_was_asked_for():
    """A block nobody asked for is appended at the end by `unused()`, which is
    a backstop rather than a layout."""
    out = await _composed({"calculation_result": _BRANCHED})
    system = " ".join(_system(out))
    for slot in out["answer_blocks"]:
        assert f"<<<{slot}>>>" in system


# ── The options a member built, side by side ──────────────────────
#
# `render_comparison` is the third renderer found dead on the live path, after
# `render_worked_steps` and everything `prepare_answer` used to fill. A member
# who built three scenarios across three turns has been told about them one at
# a time and never shown them together.

_SCENARIOS = {
    "if you retire now": {"annual_amount": "31000.00", "monthly_amount": "2583.33"},
    "at age 60": {"annual_amount": "38000.00", "monthly_amount": "3166.67"},
}


def test_the_options_a_member_built_are_shown_together():
    views = _views({"calculation_result": _RESULT, "scenario_set": _SCENARIOS})
    assert "31,000" in views["COMPARISON"]
    assert "38,000" in views["COMPARISON"]
    assert "at age 60" in views["COMPARISON"]


def test_one_option_is_not_a_comparison():
    views = _views(
        {
            "calculation_result": _RESULT,
            "scenario_set": {"at age 60": {"annual_amount": "38000.00"}},
        }
    )
    assert "COMPARISON" not in views


def test_a_superseded_option_is_not_offered():
    """A figure the member corrected is history, not something in play -- the
    same rule `active_scenarios` applies to the composer chip."""
    views = _views(
        {
            "calculation_result": _RESULT,
            "scenario_set": {
                **_SCENARIOS,
                "at age 60 (before the correction on turn 2)": {
                    "annual_amount": "99999.00"
                },
            },
        }
    )
    assert "99,999" not in views["COMPARISON"]


def test_a_tier_branch_still_wins_the_slot():
    """Both are comparisons and there is one slot. Which provision applies
    outranks which date to pick: a member who does not know their tier cannot
    act on a date."""
    views = _views({"calculation_result": _BRANCHED, "scenario_set": _SCENARIOS})
    assert "Tier 1" in views["COMPARISON"]
    # By label, not by figure: the branch and the scenario fixtures share an
    # amount, so an assertion on the number would pass for the wrong reason.
    assert "if you retire now" not in views["COMPARISON"]
    assert "If you are" in views["COMPARISON"], "the branch table's own header"
