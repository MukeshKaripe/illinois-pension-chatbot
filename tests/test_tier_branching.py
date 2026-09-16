"""Two provisions, both priced, rather than a sentence about both.

Tier is derived from a hire date and the agent usually has it. When it does not
-- an anonymous session, a member exploring a hypothetical -- the transcripts
show what happened: the agent correctly identified the branch ("If you are Tier
1... If you are Tier 2...") and then refused to compute either. An unknown tier
is not a missing number, it is two different provisions, and both can be priced.

Derivation is not branched. It reads the statute, not the member, so running it
twice would cost two model calls to produce the same recipe. Only the registry
lookup is tier-keyed, so only the registry lookup branches.
"""

from __future__ import annotations

from pension_chat.agent import nodes


class _Row:
    """The shape `lookup_recipe` returns, as much of it as resolution reads."""

    def __init__(self, tier: int) -> None:
        self.formula_id = f"art11-annuity-tier{tier}"
        self.source_sections = []
        self.tier = tier


def _registry(monkeypatch, rows: dict[int | None, object]):
    """A registry holding `rows`, keyed by the tier asked for."""
    looked_up: list[int | None] = []

    async def fake_lookup(formula_type, article, tier):
        looked_up.append(tier)
        return rows.get(tier)

    async def fake_texts(sections):
        return {}

    def fake_to_dict(self):
        return {
            "formula_id": self.row.formula_id,
            "formula_type": "retirement_annuity",
            "section": "11-134",
            "status": "approved",
            "spec": {"inputs": [], "parameters": {}, "applicability_predicates": []},
            "source": "registry",
        }

    monkeypatch.setattr(nodes, "lookup_recipe", fake_lookup)
    monkeypatch.setattr(nodes, "canonical_section_texts", fake_texts)
    monkeypatch.setattr(nodes, "note_reuse_later", lambda row: None)
    monkeypatch.setattr(nodes.RegistryRecipe, "__init__", lambda self, row: setattr(self, "row", row))
    monkeypatch.setattr(nodes.RegistryRecipe, "to_dict", fake_to_dict)
    return looked_up


def _state(tier: int | None) -> dict:
    return {
        "question": "what would my annuity be?",
        "requires_calculation": True,
        "formula_types_needed": ["retirement_annuity"],
        "employee_record": {"tier": tier, "article_number": 11} if tier else None,
        "retrieved_sections": [],
    }


async def test_an_unknown_tier_asks_the_registry_for_both(monkeypatch):
    looked_up = _registry(monkeypatch, {1: _Row(1), 2: _Row(2)})
    await nodes.resolve_recipes(_state(None))
    assert sorted(t for t in looked_up if t is not None) == [1, 2]


async def test_a_known_tier_asks_for_one(monkeypatch):
    looked_up = _registry(monkeypatch, {2: _Row(2)})
    await nodes.resolve_recipes(_state(2))
    assert looked_up == [2]


async def test_two_distinct_provisions_are_both_kept(monkeypatch):
    _registry(monkeypatch, {1: _Row(1), 2: _Row(2)})
    out = await nodes.resolve_recipes(_state(None))
    assert set(out["recipes"]) == {
        "retirement_annuity@tier1",
        "retirement_annuity@tier2",
    }
    branches = {key: r["branch"] for key, r in out["recipes"].items()}
    assert branches["retirement_annuity@tier1"] == {"fact": "tier", "value": 1}
    assert branches["retirement_annuity@tier2"] == {"fact": "tier", "value": 2}


async def test_one_provision_covering_both_tiers_is_not_a_branch(monkeypatch):
    """A single row answering for either tier is one provision, not two. The
    member should read one figure, not the same figure printed twice."""
    shared = _Row(1)
    _registry(monkeypatch, {1: shared, 2: shared})
    out = await nodes.resolve_recipes(_state(None))
    assert set(out["recipes"]) == {"retirement_annuity"}
    assert "branch" not in out["recipes"]["retirement_annuity"]


async def test_only_one_tier_on_file_is_not_a_branch_either(monkeypatch):
    """Naming a branch we could not price is the behaviour being removed."""
    _registry(monkeypatch, {2: _Row(2)})
    out = await nodes.resolve_recipes(_state(None))
    assert set(out["recipes"]) == {"retirement_annuity"}


# ── What the member reads ─────────────────────────────────────────

from pension_chat.agent.blocks import render_branches  # noqa: E402
from pension_chat.agent.loop import _branched  # noqa: E402

_PRICED = {
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


def test_both_figures_are_rendered_with_the_condition_on_each():
    table = render_branches(_PRICED)
    assert "Tier 1" in table and "Tier 2" in table
    assert "38,000" in table and "31,000" in table
    assert "11-134" in table and "1-160" in table


def test_one_branch_is_not_a_comparison():
    """A single figure is an answer, not a fork. Printing a one-row comparison
    tells the member their tier matters when nothing turned on it."""
    assert render_branches({"a": _PRICED["retirement_annuity@tier1"]}) == ""


def test_a_branch_that_did_not_compute_is_not_offered():
    """Naming a branch we could not price is exactly what this replaces."""
    broken = {
        **_PRICED,
        "retirement_annuity@tier2": {
            "branch": {"fact": "tier", "value": 2},
            "base": {"ok": False, "error": "missing input"},
        },
    }
    assert render_branches(broken) == ""


def test_an_ordinary_turn_renders_no_branch_block():
    assert _branched({"calculation_result": {"retirement_annuity": {"base": {"ok": True}}}}) == ""
    assert _branched({}) == ""


async def test_compose_hands_the_model_both_figures_and_forbids_picking():
    from pension_chat.agent import loop as loop_mod

    out = await loop_mod.compose(
        {
            "question": "what would my annuity be?",
            "messages": [],
            "loop_messages": [],
            "calculation_result": _PRICED,
            "retrieved_sections": [],
        }
    )
    system = [
        str(m.get("content") or "")
        for m in out["answer_messages"]
        if m.get("role") == "system"
    ]
    assert any("do not pick one" in text.lower() for text in system)
    assert any("38,000" in text for text in system)
