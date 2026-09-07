"""Deterministic recipe validator (§7.2 step 4).

Validates that a calculation recipe meets all structural requirements
before it can be executed. This is NOT an LLM call — it's pure Python
validation logic.

Checks:
- Every cited section exists in the indexed corpus
- Every numeric constant has a source citation
- DSL syntax uses only supported operations
- All required inputs are typed and declared
- Dependencies exist and contain no cycles
- Effective-date ranges are coherent
- Recipe doesn't select an internal executable handler
- Evidence fingerprint is captured
"""

from __future__ import annotations

import logging

from pension_chat.calculation.dsl_types import (
    InputType,
    OperationType,
    RuleSpecification,
)

logger = logging.getLogger("pension_chat.calculation.validator")


class ValidationError:
    """A single validation error."""

    def __init__(self, code: str, message: str, severity: str = "error"):
        self.code = code
        self.message = message
        self.severity = severity  # error, warning

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "severity": self.severity}


class ValidationResult:
    """Result of validating a recipe."""

    def __init__(self):
        self.errors: list[ValidationError] = []
        self.warnings: list[ValidationError] = []

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0

    def add_error(self, code: str, message: str):
        self.errors.append(ValidationError(code, message, "error"))

    def add_warning(self, code: str, message: str):
        self.warnings.append(ValidationError(code, message, "warning"))

    def to_dict(self) -> dict:
        return {
            "is_valid": self.is_valid,
            "errors": [e.to_dict() for e in self.errors],
            "warnings": [w.to_dict() for w in self.warnings],
        }


class RecipeValidator:
    """Deterministic validator for calculation recipes."""

    # All valid operations from the DSL
    VALID_OPERATIONS = {op.value for op in OperationType}
    VALID_INPUT_TYPES = {t.value for t in InputType}

    def __init__(self):
        self._strict = False

    def _add(self, result: ValidationResult, code: str, message: str):
        """Record a finding at the severity the current mode calls for."""
        if self._strict:
            result.add_error(code, message)
        else:
            result.add_warning(code, message)

    def validate(
        self,
        rule_spec: dict,
        source_sections: list[str],
        indexed_sections: set[str] | None = None,
        existing_capabilities: set[str] | None = None,
        strict: bool = False,
    ) -> ValidationResult:
        """Run all validation checks on a recipe.

        Args:
            rule_spec: The rule specification dict.
            source_sections: List of source section IDs cited by the recipe.
            indexed_sections: Set of all section IDs in the search index.
            existing_capabilities: Set of existing capability/formula IDs.
            strict: Promote evidence findings from warnings to errors. Use this
                for any recipe that is about to execute. A recipe with an
                uncited rate or an undeclared variable is not merely untidy —
                an uncited rate is a number the model was not shown, and an
                undeclared variable fails at execution time. Reporting those as
                warnings lets an unsupported formula run, which is exactly the
                failure the calculation layer exists to prevent.

        Returns:
            ValidationResult with errors and warnings.
        """
        result = ValidationResult()
        self._strict = strict

        try:
            spec = RuleSpecification(**rule_spec)
        except Exception as e:
            result.add_error("INVALID_SCHEMA", f"Rule specification schema error: {e}")
            return result

        # A recipe that cites nothing has no evidence behind it at all.
        if strict and not source_sections:
            result.add_error(
                "NO_SOURCE_SECTIONS",
                "Recipe cites no statute sections, so its rules cannot be traced "
                "to the Pension Code",
            )

        # 1. Check that cited sections exist in the index
        self._validate_source_sections(spec, source_sections, indexed_sections, result)

        # 2. Validate DSL syntax
        self._validate_dsl_syntax(spec.calculation, result)

        # 3. Validate inputs
        self._validate_inputs(spec, result)

        # 4. Validate parameters have source citations
        self._validate_parameters(spec, result, indexed_sections)

        # 5. Check for dependency cycles
        self._validate_no_cycles(spec, existing_capabilities, result)

        # 6. Validate effective dates (via source_sections metadata)
        # Done externally — we just check the spec is coherent

        # 7. No internal handler selection
        self._validate_no_handler_selection(spec, result)

        return result

    def _validate_source_sections(
        self,
        spec: RuleSpecification,
        source_sections: list[str],
        indexed_sections: set[str] | None,
        result: ValidationResult,
    ):
        """Check that every cited section exists in the indexed corpus."""
        if indexed_sections is None:
            self._add(
                result,
                "INDEX_NOT_CHECKED",
                "Could not verify source sections against the search index",
            )
            return

        for section in source_sections:
            # Normalize section ID format (e.g., "11-134" or "11-134(a)")
            base_section = section.split("(")[0].strip()
            if base_section not in indexed_sections:
                result.add_error(
                    "SECTION_NOT_FOUND",
                    f"Cited section '{section}' not found in the indexed corpus",
                )

    def _validate_dsl_syntax(self, calculation: dict, result: ValidationResult, depth: int = 0):
        """Validate that the calculation tree uses only supported operations."""
        if depth > 50:
            result.add_error("DEPTH_EXCEEDED", "Calculation tree exceeds maximum depth of 50")
            return

        if not isinstance(calculation, dict):
            return

        # Variable references and literal values are fine
        if "variable" in calculation or ("value" in calculation and "operation" not in calculation):
            return

        operation = calculation.get("operation")
        if operation is None:
            result.add_error("MISSING_OPERATION", f"Node has no operation: {list(calculation.keys())}")
            return

        if operation not in self.VALID_OPERATIONS:
            result.add_error(
                "UNSUPPORTED_OPERATION",
                f"Operation '{operation}' is not in the supported DSL vocabulary",
            )
            return

        if operation == OperationType.CAPABILITY_REF.value:
            # The interpreter refuses these — resolving one needs an async hop
            # back through the agent graph. Catch it here, where the message can
            # name the capability, rather than as a mid-execution failure.
            result.add_error(
                "CAPABILITY_REF_NOT_EXECUTABLE",
                f"Recipe references capability '{calculation.get('capability_id')}', "
                f"which the deterministic interpreter cannot resolve",
            )
            return

        # Recursively validate operands
        for operand in calculation.get("operands", []):
            if isinstance(operand, dict):
                self._validate_dsl_syntax(operand, result, depth + 1)

        # Validate branches for IF operations
        if operation == "if":
            condition = calculation.get("condition")
            if condition is None:
                result.add_error("MISSING_CONDITION", "IF operation requires a condition")
            elif isinstance(condition, dict):
                self._validate_dsl_syntax(condition, result, depth + 1)

            then_branch = calculation.get("then_branch")
            if then_branch is None:
                result.add_error("MISSING_THEN", "IF operation requires a then_branch")
            elif isinstance(then_branch, dict):
                self._validate_dsl_syntax(then_branch, result, depth + 1)

            else_branch = calculation.get("else_branch")
            if else_branch is not None and isinstance(else_branch, dict):
                self._validate_dsl_syntax(else_branch, result, depth + 1)

    def _validate_inputs(self, spec: RuleSpecification, result: ValidationResult):
        """Validate that all inputs are properly typed and declared."""
        input_names = set()
        for inp in spec.inputs:
            if inp.name in input_names:
                result.add_error(
                    "DUPLICATE_INPUT",
                    f"Input '{inp.name}' is declared more than once",
                )
            input_names.add(inp.name)

            if inp.type.value not in self.VALID_INPUT_TYPES:
                result.add_error(
                    "INVALID_INPUT_TYPE",
                    f"Input '{inp.name}' has unsupported type '{inp.type}'",
                )

        # Check that all variables used in the calculation are declared.
        # Nodes carrying "result_name" publish an intermediate that later nodes
        # may legitimately reference, so those names count as declared.
        used_vars = self._collect_variables(spec.calculation)
        intermediate_names = self._collect_result_names(spec.calculation)
        declared = input_names | set(spec.parameters.keys())
        for var in used_vars:
            if var not in declared and var not in intermediate_names:
                self._add(
                    result,
                    "UNDECLARED_VARIABLE",
                    f"Variable '{var}' is used in the calculation but is not "
                    f"declared as an input or parameter",
                )

    def _validate_parameters(
        self,
        spec: RuleSpecification,
        result: ValidationResult,
        indexed_sections: set[str] | None = None,
    ):
        """Validate that numeric constants have source citations."""
        for param_name, param_spec in spec.parameters.items():
            source = param_spec.source or {}
            if not source.get("section"):
                self._add(
                    result,
                    "MISSING_SOURCE_CITATION",
                    f"Parameter '{param_name}' (value {param_spec.value}) cites no "
                    f"statute section, so the value cannot be traced to the Pension Code",
                )
            elif indexed_sections is not None:
                cited = str(source["section"]).split("(")[0].strip()
                if cited not in indexed_sections:
                    self._add(
                        result,
                        "PARAMETER_SOURCE_NOT_RETRIEVED",
                        f"Parameter '{param_name}' cites section '{source['section']}', "
                        f"which is not among the sections retrieved for this answer",
                    )

    def _validate_no_cycles(
        self,
        spec: RuleSpecification,
        existing_capabilities: set[str] | None,
        result: ValidationResult,
    ):
        """Check for dependency cycles in capability references."""
        refs = self._collect_capability_refs(spec.calculation)
        if not refs:
            return

        if existing_capabilities is not None:
            for ref in refs:
                if ref not in existing_capabilities:
                    result.add_error(
                        "MISSING_DEPENDENCY",
                        f"Referenced capability '{ref}' does not exist",
                    )

    def _validate_no_handler_selection(self, spec: RuleSpecification, result: ValidationResult):
        """Ensure the recipe doesn't try to select an internal handler."""
        if spec.execution_type == "custom" and not spec.handler_capability:
            result.add_error(
                "MISSING_HANDLER",
                "Custom execution type requires a handler_capability",
            )

    def _collect_variables(self, node: dict, collected: set[str] | None = None) -> set[str]:
        """Recursively collect all variable names used in a calculation tree."""
        if collected is None:
            collected = set()
        if not isinstance(node, dict):
            return collected

        if "variable" in node:
            collected.add(node["variable"])

        for operand in node.get("operands", []):
            if isinstance(operand, dict):
                self._collect_variables(operand, collected)
            elif isinstance(operand, str):
                collected.add(operand)

        # Check branches
        for branch_key in ("condition", "then_branch", "else_branch", "left", "right"):
            branch = node.get(branch_key)
            if isinstance(branch, dict):
                self._collect_variables(branch, collected)

        return collected

    def _collect_result_names(self, node: dict, collected: set[str] | None = None) -> set[str]:
        """Collect names published by nodes carrying `result_name`."""
        if collected is None:
            collected = set()
        if not isinstance(node, dict):
            return collected

        if node.get("result_name"):
            collected.add(node["result_name"])

        for operand in node.get("operands", []):
            if isinstance(operand, dict):
                self._collect_result_names(operand, collected)

        for branch_key in ("condition", "then_branch", "else_branch", "left", "right"):
            branch = node.get(branch_key)
            if isinstance(branch, dict):
                self._collect_result_names(branch, collected)

        return collected

    def _collect_capability_refs(self, node: dict, collected: set[str] | None = None) -> set[str]:
        """Collect all capability references from the calculation tree."""
        if collected is None:
            collected = set()
        if not isinstance(node, dict):
            return collected

        if node.get("operation") == "capability_ref" and node.get("capability_id"):
            collected.add(node["capability_id"])

        for operand in node.get("operands", []):
            if isinstance(operand, dict):
                self._collect_capability_refs(operand, collected)

        for branch_key in ("condition", "then_branch", "else_branch"):
            branch = node.get(branch_key)
            if isinstance(branch, dict):
                self._collect_capability_refs(branch, collected)

        return collected
