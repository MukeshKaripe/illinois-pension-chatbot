"""DSL type definitions for the declarative calculation engine (§7.3).

Strongly typed primitives that the rule interpreter understands.
These are the ONLY operations the engine supports — no arbitrary code execution.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class OperationType(str, Enum):
    """Supported DSL operations — the complete set from §7.3."""
    ADD = "add"
    SUBTRACT = "subtract"
    MULTIPLY = "multiply"
    DIVIDE = "divide"
    MIN = "min"
    MAX = "max"
    IF = "if"
    COMPARE = "compare"
    LOOKUP = "lookup"
    PERCENTAGE = "percentage"
    ROUND = "round"
    CAP = "cap"
    FLOOR = "floor"
    CEILING = "ceiling"
    DATE_DIFF_YEARS = "date_diff_years"
    DATE_DIFF_MONTHS = "date_diff_months"
    DATE_COMPARE = "date_compare"
    IN_RANGE = "in_range"
    CAPABILITY_REF = "capability_ref"


class InputType(str, Enum):
    """Supported input types for recipe parameters."""
    MONEY = "money"
    DECIMAL = "decimal"
    INTEGER = "integer"
    DATE = "date"
    BOOLEAN = "boolean"
    STRING = "string"
    YEARS = "years"
    PERCENTAGE = "percentage"


class ComparisonOp(str, Enum):
    """Comparison operators for IF/COMPARE operations."""
    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GE = "ge"
    LT = "lt"
    LE = "le"


class InputSpec(BaseModel):
    """Specification for a single recipe input."""
    name: str
    type: InputType
    description: str | None = None
    source_section: str | None = None
    default_value: Any | None = None
    required: bool = True


class ParameterSpec(BaseModel):
    """A fixed parameter with a statutory source citation."""
    value: Any
    # {section, subsection}. The subsection is genuinely optional -- plenty of
    # provisions have no lettered subdivision -- and `dict[str, str]` refused a
    # null for it, which threw away an entire derived recipe over a field that
    # was never required. The model emitting `"subsection": None` is being more
    # honest than one that omits the key or invents "(a)".
    source: dict[str, str | None] = Field(default_factory=dict)
    effective_date: str | None = None


class ApplicabilityPredicate(BaseModel):
    """One machine-evaluable statutory condition for using a recipe.

    Prose applicability ("applies to an employee withdrawing at age 56 with
    21.5 years of service") cannot be re-checked when the recipe is reused for
    a different member. A predicate can: `calculation/applicability.py`
    evaluates it against the member's facts before execution.
    """
    fact: str
    operator: str
    value: Any | None = None
    description: str | None = None
    # {section, subsection}; see `ParameterSpec.source` for why the value is
    # optional. The section is still checked by `validator._check_citations`,
    # so a parameter that traces to nothing is still refused.
    source: dict[str, str | None] = Field(default_factory=dict)


class OperationNode(BaseModel):
    """A single operation in the calculation tree."""
    operation: OperationType
    operands: list[Any] = Field(default_factory=list)
    # For IF operations
    condition: dict | None = None
    then_branch: dict | None = None
    else_branch: dict | None = None
    # For COMPARE operations
    operator: ComparisonOp | None = None
    left: Any | None = None
    right: Any | None = None
    # For LOOKUP operations
    table: list[dict] | None = None
    key: str | None = None
    # For ROUND/CAP/FLOOR/CEILING
    precision: int | None = None
    limit: Any | None = None
    # For CAPABILITY_REF
    capability_id: str | None = None
    capability_inputs: dict[str, str] | None = None
    # For DATE operations
    date_a: str | None = None
    date_b: str | None = None


class RuleSpecification(BaseModel):
    """Complete rule specification for a calculation recipe (§7.3).

    This is what gets stored as `rule_specification` JSONB in formula_registry.
    """
    formula_id: str
    execution_type: Literal["declarative", "custom"] = "declarative"
    inputs: list[InputSpec] = Field(default_factory=list)
    parameters: dict[str, ParameterSpec] = Field(default_factory=dict)
    calculation: dict = Field(default_factory=dict)  # OperationNode tree
    # Conditions the statute attaches to this provision. Evaluated
    # deterministically before execution and again on every reuse, so a recipe
    # cannot be applied to a member the provision does not reach.
    applicability_predicates: list[ApplicabilityPredicate] = Field(default_factory=list)
    output_name: str = "result"
    output_type: InputType = InputType.MONEY

    # For custom execution type
    handler_capability: str | None = None
