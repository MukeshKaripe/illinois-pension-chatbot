"""Tests for the response-template registry.

A template is a slot order plus the prose guidance for the slots the model
writes. The registry is data, so these tests walk it: every shape has one,
every computed slot has a renderer, and no template asks the model to write a
block that Python owns.
"""

from __future__ import annotations

from pension_chat.agent import blocks
from pension_chat.agent.presentation import (
    COMPUTED_SLOTS,
    TEMPLATES,
    Template,
    shape_for,
    template_for,
)
from pension_chat.agent.worksheet import WorkSheet

SHAPES = ("figure", "comparison", "legal", "partial", "clarify")


def test_every_shape_has_a_template() -> None:
    for shape in SHAPES:
        assert isinstance(template_for(shape), Template)


def test_every_computed_slot_names_a_real_marker() -> None:
    for template in TEMPLATES.values():
        for slot in template.slots:
            if slot in COMPUTED_SLOTS:
                assert slot in blocks.MARKERS, f"{template.shape}: {slot} has no marker"


def test_every_template_opens_with_the_lede_so_the_answer_comes_first() -> None:
    for template in TEMPLATES.values():
        assert template.slots[0] in {"STATUS", "lede"}, template.shape
        assert "lede" in template.slots, template.shape


def test_every_template_that_shows_a_figure_shows_how_it_was_reached() -> None:
    for shape in ("figure", "comparison"):
        slots = template_for(shape).slots
        assert "WORKED_STEPS" in slots or "COMPARISON" in slots


def test_guidance_never_asks_the_model_to_write_a_computed_block() -> None:
    forbidden = ("| Step | Amount |", "|---|---:|", "How that is worked out")
    for template in TEMPLATES.values():
        for phrase in forbidden:
            assert phrase not in template.guidance, f"{template.shape} still describes a table"


def test_an_unknown_shape_falls_back_to_the_legal_template() -> None:
    assert template_for("nonsense").shape == "legal"


def test_shape_and_template_agree_on_a_comparison_turn() -> None:
    sheet = WorkSheet.from_state({"turn_index": 1, "member_facts": {}, "assumptions": {}})
    shape = shape_for(sheet, {"scenario_set": {"at 60": {}}, "calculation_result": {}})
    assert "COMPARISON" in template_for(shape).slots
