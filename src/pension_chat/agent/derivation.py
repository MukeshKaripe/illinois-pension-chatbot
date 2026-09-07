"""LLM-driven derivation of calculation recipes from retrieved statute text.

This is the "reusable calculation knowledge" layer of the architecture (§7.2):
the model reads statute text and emits a *declarative rule specification*, which
the deterministic interpreter then executes. The model never computes a benefit
figure — it only describes how one is computed, in a fixed operation vocabulary.

The first implementation stubbed this out with a hardcoded ``salary x 0.5``
tree, which meant every calculation question returned 50% of salary presented
as a deterministic result. That is precisely the unsupported formula the
adversarial test case asks the agent to reject, so the stub turned the safety
property inside out.

Two rules make the derivation trustworthy:

1. **Every numeric constant carries a citation.** A parameter with no
   ``source.section`` fails validation, so the model cannot smuggle in a rate it
   did not read.
2. **Every cited section must be one that was actually retrieved.** The model
   cannot cite from memory; it can only cite text it was shown.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from pension_chat.agent.llm import TokenUsage, complete
from pension_chat.agent.retrieval import RetrievedSection
from pension_chat.calculation.dsl_types import OperationType, RuleSpecification
from pension_chat.config import get_settings

logger = logging.getLogger("pension_chat.agent.derivation")


# The operation vocabulary, written out for the model. Kept in sync with
# OperationType by the assertion at the bottom of this module.
DSL_GRAMMAR = """
A calculation is a JSON tree. Every node is one of:

  {"value": <number>}                      a literal constant
  {"variable": "<input_or_parameter_name>"} a named input or parameter
  {"operation": "<op>", "operands": [<node>, ...], ...}

Operations and their shapes:
  add / multiply            operands: 2+ nodes
  subtract / divide         operands: exactly 2 nodes  (a - b, a / b)
  min / max                 operands: 2+ nodes
  percentage                operands: [value, rate]    -> value * rate
                            (rate is a fraction: 2.4% is 0.024)
  round                     operands: [value], "precision": <int>
  cap / ceiling             operands: [value], "limit": <node or number>  -> min(value, limit)
  floor                     operands: [value], "limit": <node or number>  -> max(value, limit)
  in_range                  operands: [value], "low": <n>, "high": <n>    -> boolean
  compare                   "left": <node>, "operator": "eq|ne|gt|ge|lt|le", "right": <node>
  if                        "condition": <compare-node>, "then_branch": <node>, "else_branch": <node>
  lookup                    "key": "<variable name>", "default": <number>,
                            "table": [{"min": n, "max": n, "value": v} | {"match": "x", "value": v}]
  date_diff_years           "date_a": <node>, "date_b": <node>   -> years from a to b
  date_diff_months          "date_a": <node>, "date_b": <node>
  date_compare              "date_a": <node>, "date_b": <node>, "operator": "ge"

Any node may carry "result_name": "<name>" to expose that value as a named
intermediate; later nodes can then reference it with {"variable": "<name>"}.
Name every step a member would need to follow the arithmetic — the unreduced
amount, any reduction factor, any statutory maximum, and the final figure. These
names are what the answer shows as the path from salary to benefit, so a tree
with none of them yields a number the member cannot check.

Hard constraints:
- Operands must be node objects ({"value":...}, {"variable":...}, or a nested
  operation). Never a bare string or bare number.
- There is no exponentiation, no loops, no function calls, no free-text
  expressions. If a rule cannot be expressed in this vocabulary, say so via
  "cannot_derive" rather than approximating it.
"""

_SYSTEM_PROMPT = """You derive deterministic calculation recipes from the Illinois Pension Code.

You are given the verbatim text of statute sections and a set of member facts.
Produce a declarative rule specification that a deterministic interpreter will
execute. You never compute the benefit yourself and you never state a result.

""" + DSL_GRAMMAR + """
Rules you must follow:

1. Use ONLY rates, percentages, caps, thresholds and dollar amounts that appear
   verbatim in the supplied statute text. Every entry in "parameters" must cite
   the section (and subsection where the text has one) it was read from.

2. Parameters vs inputs — this distinction matters:
   - "parameters" are values FIXED BY STATUTE: accrual rates, the 0.25% monthly
     reduction, service thresholds, age thresholds, percentage caps, effective
     dates such as January 1 2002. Each carries a citation.
   - "inputs" are facts ABOUT THIS MEMBER that only they or their record can
     supply: age, service years, salary history, contribution balances,
     withdrawal date.
   Never declare a statutory constant as an input. Doing so makes the system
   ask the member for a number the statute already fixes.

3. Derive ONLY the provision that applies to the supplied member facts. Do not
   encode alternative provisions as extra branches in the tree — name them in
   "alternatives_considered" instead. A tree that branches over provisions the
   member cannot reach is harder to verify and no more correct.
   Do keep branches that the applicable provision itself requires (for example
   an exception that waives a reduction at a stated service threshold).

3a. Where the statute gives the member an ELECTION between formulas ("may elect
   to receive, in lieu of any other annuity provided in this Section..."), the
   member has not told you which they will elect. Derive the one that yields the
   larger annuity on their facts — that is what electing means in practice —
   and record the others in "alternatives_considered". Do not refuse to derive
   because the election is unstated.

3b. Where the provision the member's facts point to first does NOT contain an
   executable formula in the supplied text — typically because it converts
   accumulated contributions using an actuarial factor the statute does not
   state — check whether another supplied provision does produce a computable
   annuity for the same member (a minimum-annuity provision, for example, often
   applies "in lieu of all annuities otherwise provided"). If one does, derive
   that, and use "alternatives_considered" to record the provision you could not
   execute and why. Refuse only when NO supplied provision yields a computable
   result.

4. Encode the eligibility conditions, early-retirement reductions, and maximum
   percentage caps that the controlling provision imposes. Omitting a reduction
   or a cap is a correctness failure.

4a. Also state the provision's entitlement conditions separately, as
   "applicability_predicates" — machine-checkable tests that decide whether this
   provision reaches a member at all. Each is:

     {"fact": "<member fact>", "operator": "ge|gt|le|lt|eq|ne|in|not_in|between|
                                             is_true|is_false",
      "value": <number|string|date|list>,
      "description": "<the condition in the statute's own words>",
      "source": {"section": "11-134", "subsection": "(a)"}}

   Facts you may test: age, service_years, employment_status, article_number,
   tier, withdrawal_date, birth_date, current_annual_salary,
   final_average_salary, months_before_age_60. Dates are ISO strings.

   These are the conditions the STATUTE imposes, not a description of the member
   in front of you. "20 or more years of service" is a condition; "has 21.5
   years of service" is not. Only state a threshold the provision actually
   sets — an unnecessary bound will wrongly exclude members the provision covers,
   and this recipe is stored and reused for other members and for the same
   member at other retirement ages.

   A provision that applies "in lieu of any other annuity provided in this
   Section" imposes no upper age bound just because the member is under 60
   today; the under-60 reduction is arithmetic in the tree, not an eligibility
   condition. Commencement rules ("to begin not earlier than age 55") ARE
   conditions.

5. If the supplied text does not contain a complete, executable rule — the
   conversion factor is actuarial, the governing subsection was not retrieved,
   the member's facts fall outside every provision shown — set "cannot_derive"
   to a plain-language explanation and leave "rule_specification" null.

Keep the calculation tree as small as the statute allows.

Respond with a single JSON object:

{
  "cannot_derive": null | "<why not>",
  "formula_name": "<short human name>",
  "formula_type": "<the requested formula type>",
  "controlling_section": "<e.g. 11-134>",
  "controlling_subsection": "<e.g. (a)>" | null,
  "applicability": "<one sentence: who this recipe applies to>",
  "alternatives_considered": ["<section/subsection and why it was not used>"],
  "source_sections": ["<every section id whose text this recipe relies on>"],
  "rule_specification": {
    "formula_id": "<article>_<formula_type>_<section>",
    "execution_type": "declarative",
    "inputs": [
      {"name": "...", "type": "money|decimal|integer|date|boolean|string|years|percentage",
       "description": "...", "source_section": "...", "required": true}
    ],
    "parameters": {
      "<name>": {"value": <number>, "source": {"section": "11-134", "subsection": "(a)"}}
    },
    "calculation": <node>,
    "applicability_predicates": [
      {"fact": "...", "operator": "...", "value": ...,
       "description": "...", "source": {"section": "...", "subsection": "..."}}
    ],
    "output_name": "annual_annuity",
    "output_type": "money"
  },
  "derivation_notes": "<how you read the statute; 2-4 sentences>"
}
"""


@dataclass
class DerivedRecipe:
    """A recipe the agent derived from statute text this turn."""

    formula_type: str
    rule_specification: dict | None = None
    formula_name: str = ""
    controlling_section: str | None = None
    controlling_subsection: str | None = None
    applicability: str = ""
    source_sections: list[str] = field(default_factory=list)
    alternatives_considered: list[str] = field(default_factory=list)
    derivation_notes: str = ""
    cannot_derive: str | None = None

    @property
    def ok(self) -> bool:
        return self.cannot_derive is None and bool(self.rule_specification)

    def to_dict(self) -> dict:
        return {
            "formula_type": self.formula_type,
            "formula_name": self.formula_name,
            "spec": self.rule_specification,
            "controlling_section": self.controlling_section,
            "controlling_subsection": self.controlling_subsection,
            "applicability": self.applicability,
            "source_sections": self.source_sections,
            "alternatives_considered": self.alternatives_considered,
            "derivation_notes": self.derivation_notes,
            "cannot_derive": self.cannot_derive,
        }


def _statute_context(sections: list[RetrievedSection]) -> str:
    blocks = []
    for section in sections:
        header = f"### 40 ILCS 5/{section.section} — {section.title}"
        if section.truncated:
            header += "  [TEXT TRUNCATED — do not rely on omitted subsections]"
        blocks.append(f"{header}\n{section.text}")
    return "\n\n".join(blocks)


def _facts_block(facts: dict[str, Any]) -> str:
    if not facts:
        return "(no member facts supplied — derive the general rule and declare every input)"
    lines = [f"- {k}: {v}" for k, v in facts.items() if v is not None]
    return "\n".join(lines)


async def derive_recipe(
    formula_type: str,
    sections: list[RetrievedSection],
    facts: dict[str, Any],
    *,
    usage: TokenUsage | None = None,
) -> DerivedRecipe:
    """Ask the model to express one formula type as a DSL rule specification.

    Args:
        formula_type: What is being derived, e.g. ``"retirement_annuity"``.
        sections: Retrieved statute text the recipe may rely on.
        facts: Known member facts, used to pick the applicable provision.

    Returns:
        A ``DerivedRecipe``. Check ``.ok`` — a recipe the model declined to
        derive is a normal outcome, not an error.
    """
    settings = get_settings()

    if not sections:
        return DerivedRecipe(
            formula_type=formula_type,
            cannot_derive=(
                "No statute text was retrieved for this question, so there is "
                "nothing to derive a calculation from."
            ),
        )

    user_prompt = (
        f"Formula type requested: {formula_type}\n\n"
        f"Member facts:\n{_facts_block(facts)}\n\n"
        f"Statute text:\n{_statute_context(sections)}"
    )

    try:
        raw = await complete(
            node="derive_recipe",
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            usage=usage,
            reasoning_effort=settings.reasoning_effort_derive,
            json_mode=True,
            timeout=settings.derivation_timeout_seconds,
            attempts=1,
        )
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        logger.error("Recipe derivation returned non-JSON: %s", exc)
        return DerivedRecipe(
            formula_type=formula_type,
            cannot_derive="The derivation step returned a malformed response.",
        )
    except Exception as exc:
        logger.exception("Recipe derivation failed")
        return DerivedRecipe(
            formula_type=formula_type,
            cannot_derive=f"The derivation step failed: {exc}",
        )

    if payload.get("cannot_derive"):
        return DerivedRecipe(
            formula_type=formula_type,
            cannot_derive=str(payload["cannot_derive"]),
            derivation_notes=payload.get("derivation_notes", ""),
            source_sections=list(payload.get("source_sections") or []),
        )

    spec = payload.get("rule_specification")
    if not isinstance(spec, dict) or not spec.get("calculation"):
        return DerivedRecipe(
            formula_type=formula_type,
            cannot_derive="The derivation step produced no calculation tree.",
            derivation_notes=payload.get("derivation_notes", ""),
        )

    # Reject anything that does not round-trip through the DSL schema before it
    # reaches the validator, so a malformed tree surfaces here with a clear cause.
    try:
        RuleSpecification(**spec)
    except Exception as exc:
        logger.warning("Derived spec failed schema validation: %s", exc)
        return DerivedRecipe(
            formula_type=formula_type,
            cannot_derive=f"The derived recipe did not match the rule schema: {exc}",
            derivation_notes=payload.get("derivation_notes", ""),
        )

    return DerivedRecipe(
        formula_type=formula_type,
        rule_specification=spec,
        formula_name=payload.get("formula_name") or formula_type.replace("_", " ").title(),
        controlling_section=payload.get("controlling_section"),
        controlling_subsection=payload.get("controlling_subsection"),
        applicability=payload.get("applicability", ""),
        source_sections=list(payload.get("source_sections") or []),
        alternatives_considered=list(payload.get("alternatives_considered") or []),
        derivation_notes=payload.get("derivation_notes", ""),
    )


# Keep the prompt's operation list honest: if an operation is added to the DSL
# and not documented above, fail at import rather than silently letting the
# model reach for a vocabulary the prompt never described.
#
# CAPABILITY_REF is excluded deliberately — the interpreter refuses it (it needs
# async resolution through the graph), so the model must never emit one.
_documented_ops = set(re.findall(r"\b[a-z_]+\b", DSL_GRAMMAR))
_undocumented = {
    op.value for op in OperationType if op is not OperationType.CAPABILITY_REF
} - _documented_ops
assert not _undocumented, f"DSL operations undocumented in DSL_GRAMMAR: {sorted(_undocumented)}"
