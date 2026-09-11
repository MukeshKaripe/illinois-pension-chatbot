"""Tests for profiles as stored rows: round-tripping, labels, and what may change.

A profile is a preset that was then edited and saved as an `employees` row. To
edit it again, the row has to come back as the same Persona the bounds were
written for -- otherwise `validate` checks one thing and the database holds
another.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from pension_chat import personas


@pytest.mark.parametrize("preset", personas.PERSONAS, ids=lambda p: p.key)
def test_a_stored_preset_comes_back_as_itself(preset: personas.Persona) -> None:
    row = SimpleNamespace(
        **personas.to_employee_kwargs(preset),
        persona_key=preset.key,
        label=preset.label,
        email=preset.email,
    )
    assert personas.persona_from_row(row, preset.salary_history) == preset


def test_an_edited_row_keeps_its_own_values_not_the_presets() -> None:
    preset = personas.ARTICLE_11_STANDARD
    # A Decimal, as `PersonaEdits` delivers it in production.
    edited = personas.apply_edits(preset, {"current_annual_salary": Decimal("90000.00")})
    row = SimpleNamespace(
        **personas.to_employee_kwargs(edited),
        persona_key=preset.key,
        label=preset.label,
        email=None,
    )
    restored = personas.persona_from_row(row, edited.salary_history)
    assert restored.current_annual_salary == edited.current_annual_salary
    assert restored.salary_history == edited.salary_history


def test_the_article_is_not_editable() -> None:
    # Another Article is another profile (spec §6): an edit cannot move one.
    assert "article_number" not in personas.EDITABLE_FIELDS


def test_every_preset_reads_in_a_members_words() -> None:
    engineering_names = {"Standard", "Tier 2", "Near retirement"}
    for preset in personas.PERSONAS:
        assert preset.label not in engineering_names, preset.key


def test_every_offered_article_names_its_fund() -> None:
    assert set(personas.FUND_NAMES) == set(personas.OFFERED_ARTICLES)
