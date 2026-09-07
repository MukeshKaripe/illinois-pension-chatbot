"""Deterministic risk tier assessor (§7.2 step 5).

Assigns risk_tier based on observable signals, NOT by the agent's
self-assessment. This is pure code, no LLM call.

Signals used:
- Source-section count
- Conditional-branch count
- Historical/effective-date branching
- Cross-Article or reciprocal-service dependencies
- Custom actuarial handlers
- High-risk topic lists
- Worked-example evidence availability
- Input ambiguity
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("pension_chat.calculation.risk_assessor")


# Topics that automatically qualify as high-risk
HIGH_RISK_TOPICS = {
    "disability", "survivor", "death_benefit", "actuarial",
    "reciprocal", "cross_system", "military_service",
}

# Formula types that are inherently complex
COMPLEX_FORMULA_TYPES = {
    "disability_annuity", "survivor_annuity", "actuarial_calculation",
    "reciprocal_service", "death_benefit",
}


class RiskAssessment:
    """Result of risk assessment."""

    def __init__(
        self,
        risk_tier: str,
        signals: dict[str, Any],
        reasons: list[str],
    ):
        self.risk_tier = risk_tier  # low, medium, high
        self.signals = signals
        self.reasons = reasons

    def to_dict(self) -> dict:
        return {
            "risk_tier": self.risk_tier,
            "signals": self.signals,
            "reasons": self.reasons,
        }


class RiskAssessor:
    """Assigns deterministic risk tiers to calculation recipes."""

    def assess(
        self,
        rule_spec: dict,
        source_sections: list[str],
        formula_type: str,
        article_number: int,
        has_worked_examples: bool = False,
        has_cross_article_refs: bool = False,
    ) -> RiskAssessment:
        """Assess the risk tier for a recipe.

        Returns:
            RiskAssessment with tier and explanatory signals.
        """
        signals: dict[str, Any] = {}
        reasons: list[str] = []
        score = 0

        # 1. Source section count
        section_count = len(source_sections)
        signals["source_section_count"] = section_count
        if section_count > 5:
            score += 2
            reasons.append(f"High section count ({section_count})")
        elif section_count > 3:
            score += 1
            reasons.append(f"Moderate section count ({section_count})")

        # 2. Conditional branch count
        branch_count = self._count_branches(rule_spec.get("calculation", {}))
        signals["branch_count"] = branch_count
        if branch_count > 4:
            score += 2
            reasons.append(f"Complex branching ({branch_count} branches)")
        elif branch_count > 2:
            score += 1
            reasons.append(f"Moderate branching ({branch_count} branches)")

        # 3. Historical/effective-date complexity
        has_date_branching = self._has_date_operations(rule_spec.get("calculation", {}))
        signals["has_date_branching"] = has_date_branching
        if has_date_branching:
            score += 1
            reasons.append("Contains date-dependent branching")

        # 4. Cross-Article dependencies
        signals["has_cross_article_refs"] = has_cross_article_refs
        if has_cross_article_refs:
            score += 2
            reasons.append("Cross-Article or reciprocal-service dependencies")

        # 5. Custom handlers
        is_custom = rule_spec.get("execution_type") == "custom"
        signals["is_custom_handler"] = is_custom
        if is_custom:
            score += 3
            reasons.append("Requires custom actuarial handler")

        # 6. High-risk topics
        formula_lower = formula_type.lower()
        is_high_risk_topic = any(
            topic in formula_lower for topic in HIGH_RISK_TOPICS
        )
        signals["is_high_risk_topic"] = is_high_risk_topic
        if is_high_risk_topic:
            score += 2
            reasons.append(f"High-risk topic: {formula_type}")

        # 7. Complex formula type
        if formula_type in COMPLEX_FORMULA_TYPES:
            score += 1
            reasons.append(f"Complex formula type: {formula_type}")

        # 8. Worked-example evidence (mitigating)
        signals["has_worked_examples"] = has_worked_examples
        if has_worked_examples:
            score -= 1
            reasons.append("Has worked-example verification (mitigating)")

        # 9. Input ambiguity
        input_count = len(rule_spec.get("inputs", []))
        signals["input_count"] = input_count
        if input_count > 8:
            score += 1
            reasons.append(f"High input count ({input_count})")

        # Determine tier
        signals["raw_score"] = score
        if score >= 4:
            risk_tier = "high"
        elif score >= 2:
            risk_tier = "medium"
        else:
            risk_tier = "low"

        return RiskAssessment(
            risk_tier=risk_tier,
            signals=signals,
            reasons=reasons,
        )

    def _count_branches(self, node: dict, count: int = 0) -> int:
        """Count IF/conditional branches in a calculation tree."""
        if not isinstance(node, dict):
            return count

        if node.get("operation") == "if":
            count += 1

        for operand in node.get("operands", []):
            if isinstance(operand, dict):
                count = self._count_branches(operand, count)

        for key in ("condition", "then_branch", "else_branch"):
            child = node.get(key)
            if isinstance(child, dict):
                count = self._count_branches(child, count)

        return count

    def _has_date_operations(self, node: dict) -> bool:
        """Check if the calculation tree contains date-dependent operations."""
        if not isinstance(node, dict):
            return False

        op = node.get("operation", "")
        if op in ("date_diff_years", "date_diff_months", "date_compare"):
            return True

        for operand in node.get("operands", []):
            if isinstance(operand, dict) and self._has_date_operations(operand):
                return True

        for key in ("condition", "then_branch", "else_branch"):
            child = node.get(key)
            if isinstance(child, dict) and self._has_date_operations(child):
                return True

        return False
