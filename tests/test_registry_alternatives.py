"""Why this provision and not the one next to it, kept past the first member.

`derive_recipe` weighs the neighbouring provisions and records which it set
aside -- 11-129, 11-130, 11-131 and 11-134 all reach a member withdrawing after
55, so "why 11-134" is the question a reader of Article 11 has to ask.

This is a guard, not a fix: the reasoning is already persisted and already read
back. It is guarded because *where* it lives is easy to get wrong. It is stored
in the `applicability` column, not in `rule_specification` -- where
`rule_specification.alternatives_considered` reads `null`, which looks exactly
like the reasoning having been dropped.
"""

from __future__ import annotations

from pension_chat.agent.registry import RegistryRecipe


class _Row:
    """The columns `RegistryRecipe` reads. A stand-in, so this stays offline."""

    def __init__(self, applicability: dict) -> None:
        self.formula_id = "11_retirement_annuity_11-134_abcd1234"
        self.formula_type = "retirement_annuity"
        self.formula_name = "Minimum annuity"
        self.article_number = 11
        self.tier = 1
        self.section = "11-134"
        self.description = "d"
        self.rule_specification = {}
        self.source_sections = ["11-134"]
        self.dependencies = []
        self.required_inputs = []
        self.optional_inputs = []
        self.applicability = applicability
        self.knowledge_status = "agent_derived"
        self.execution_policy = "estimate"
        self.risk_tier = "medium"
        self.version = 1
        self.evidence_fingerprint = {}


def test_a_stored_recipe_carries_the_provisions_it_set_aside():
    alternatives = [
        {"section": "11-130", "subsection": "", "status": "not_applicable",
         "reason": "no conversion factors"},
        {"section": "11-131", "subsection": "", "status": "not_applicable",
         "reason": "withdrawal before 55"},
    ]
    recipe = RegistryRecipe(_Row({"alternatives_considered": alternatives})).to_dict()

    assert [a["section"] for a in recipe["alternatives_considered"]] == ["11-130", "11-131"]


def test_a_recipe_stored_without_them_reads_as_empty_not_missing():
    """Rows written before this existed must not KeyError on reuse."""
    recipe = RegistryRecipe(_Row({})).to_dict()
    assert recipe["alternatives_considered"] == []
