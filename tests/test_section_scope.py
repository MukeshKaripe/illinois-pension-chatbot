"""Sections that apply only to some funds, or only on a condition, say so.

A Tier 2 MEABF derivation had §8-138 (2.4% per year) in front of it and still
took 1.25% two times in three, from §1-161(g) and §1-162(g), whose text reads
like the Tier 2 rule: "the amount of the retirement annuity ... shall be
computed by multiplying 1.25% for each year of service credit". Neither applies
to an MEABF member by default -- §1-161 is for Articles 14, 15 and 16 alone, and
§1-162 only for a fund its local government designated "affected", and then
only for members who joined after that or elected it. Retrieved together, the
model cannot tell. So derivation is not shown a section scoped to other funds,
is told the condition on a conditional one, and a recipe resting on a
conditional section is not saved for the whole fund.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from pension_chat.agent import nodes
from pension_chat.agent.retrieval import RetrievedSection
from pension_chat.agent.section_scope import SECTION_SCOPE, scoped_evidence


def _s(section: str, text: str = "text") -> RetrievedSection:
    return RetrievedSection(section=section, article=section.split("-")[0], title="t", text=text)


def test_the_table_carries_the_two_sections_and_their_statute_basis() -> None:
    assert SECTION_SCOPE["1-161"].articles == frozenset({14, 15, 16})
    assert SECTION_SCOPE["1-162"].condition and "affected pension fund" in SECTION_SCOPE["1-162"].condition


def test_a_section_scoped_to_other_funds_is_withheld_from_an_article_8_derivation() -> None:
    kept = scoped_evidence([_s("8-138"), _s("1-160"), _s("1-161"), _s("1-162")], article=8)
    assert [s.section for s in kept] == ["8-138", "1-160", "1-162"]


def test_the_same_section_is_kept_for_a_fund_it_governs() -> None:
    kept = scoped_evidence([_s("1-161")], article=14)
    assert [s.section for s in kept] == ["1-161"]


def test_a_conditional_section_is_shown_with_its_condition_first() -> None:
    [kept] = scoped_evidence([_s("1-162", "(g) The amount ... 1.25% ...")], article=8)
    assert kept.text.startswith("[Applies only")
    assert "(g) The amount ... 1.25% ..." in kept.text


def test_an_unknown_article_changes_nothing() -> None:
    sections = [_s("1-161"), _s("1-162")]
    assert scoped_evidence(sections, article=None) == sections


@pytest.mark.asyncio
async def test_derivation_evidence_is_scoped(monkeypatch) -> None:
    async def retrieve(queries, **kwargs):
        return [_s("8-138", "(g-1) 2.4%")]

    monkeypatch.setattr(nodes, "retrieve", retrieve)
    got = await nodes._derivation_evidence("retirement_annuity", "8", [_s("1-160"), _s("1-161"), _s("1-162")])
    assert [s.section for s in got] == ["8-138", "1-160", "1-162"]
    assert got[-1].text.startswith("[Applies only")


@pytest.mark.asyncio
async def test_a_recipe_resting_on_a_conditional_section_is_not_saved(monkeypatch) -> None:
    saved: list = []

    async def persist(derived, **kwargs):
        saved.append(derived)

    async def texts(ids):
        return {}

    monkeypatch.setattr(nodes, "persist_recipe", persist)
    monkeypatch.setattr(nodes, "canonical_section_texts", texts)
    monkeypatch.setattr(nodes, "get_settings", lambda: SimpleNamespace(persist_derived_recipes=True))

    def state(sources: list[str]) -> dict:
        derived = SimpleNamespace(source_sections=sources)
        return {
            "recipes": {"retirement_annuity": {"source": "derived", "_derived": derived}},
            "retrieved_sections": [], "validation_errors": [], "employee_record": None,
            "coverage": {"article": "8", "basis": "record"},
        }

    ok = {"retirement_annuity": {"base": {"ok": True}}}
    await nodes._persist_new_recipes(state(["1-162"]), ok)
    assert saved == []
    await nodes._persist_new_recipes(state(["8-138", "1-160"]), ok)
    assert len(saved) == 1
