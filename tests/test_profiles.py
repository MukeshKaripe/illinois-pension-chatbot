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
from pension_chat.api.auth import list_personas


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


async def test_every_preset_is_served_with_its_funds_facts() -> None:
    # The picker groups by employer and the form renders from `fields`, so
    # both travel with each preset rather than living in the client.
    options = await list_personas()
    assert len(options) == len(personas.PERSONAS)
    judge = next(option for option in options if option.article_number == 18)
    assert (judge.member_label, judge.employer_group, judge.support_level) == (
        "Judge",
        "State of Illinois",
        "experimental",
    )
    assert [spec.name for spec in judge.fields] == list(personas.EDITABLE_FIELDS)
    laborer = next(option for option in options if option.key == "article-11-standard")
    assert laborer.support_level == "checked"
    # A fund's own field travels flagged, so the form sends it under extra_facts.
    sers = next(option for option in options if option.article_number == 14)
    covered = next(spec for spec in sers.fields if spec.name == "social_security_covered")
    assert (covered.type, covered.extra) == ("yesno", True)
    assert sers.extra_facts == {"social_security_covered": True}
    assert not any(spec.extra for spec in judge.fields)
