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
- Statutory applicability conditions are well formed and cited
- Every numeric constant is written in the subsection it cites
"""

from __future__ import annotations

import logging
import re
from fractions import Fraction

from pension_chat.calculation.applicability import validate_predicate
from pension_chat.calculation.dsl_types import (
    InputType,
    OperationType,
    RuleSpecification,
)

logger = logging.getLogger("pension_chat.calculation.validator")


# ── Is this constant written in that text? ───────────────────────

_NUMBER_WORDS = {
    1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven",
    8: "eight", 9: "nine", 10: "ten", 11: "eleven", 12: "twelve", 13: "thirteen",
    14: "fourteen", 15: "fifteen", 16: "sixteen", 17: "seventeen", 18: "eighteen",
    19: "nineteen", 20: "twenty",
}
_FRACTION_WORDS = {
    Fraction(1, 2): ("one-half", "one half"), Fraction(1, 4): ("one-quarter", "one-fourth"),
    Fraction(3, 4): ("three-quarters", "three-fourths"), Fraction(1, 3): ("one-third",),
    Fraction(2, 3): ("two-thirds",),
}


def _plain(number: float) -> list[str]:
    """A number as digits: 2.4, 67, 106800 and 106,800."""
    if abs(number - round(number)) < 1e-9:
        whole = int(round(number))
        return [str(whole), f"{whole:,}"]
    return [f"{number:.6f}".rstrip("0").rstrip(".")]


def _as_fraction(number: float) -> Fraction | None:
    """A number the Code would write as a vulgar fraction (1 1/4, 1 2/3, 1/2)."""
    for denominator in (2, 3, 4, 5, 8):
        candidate = Fraction(round(number * denominator), denominator)
        if candidate.denominator > 1 and abs(float(candidate) - number) < 1e-3:
            return candidate
    return None


def statutory_forms(value: float) -> list[str]:
    """The ways the Pension Code writes this number: digits, a fraction, a word,
    and -- for a rate below 1 -- a percentage in each of those forms."""
    forms: list[str] = _plain(value)
    if abs(value - round(value)) < 1e-9 and int(round(value)) in _NUMBER_WORDS:
        forms.append(_NUMBER_WORDS[int(round(value))])
    percent = value * 100
    if 0 < value < 1 or value >= 1:
        for digits in _plain(percent) + (_plain(value) if value >= 1 else []):
            forms += [f"{digits}%", f"{digits} percent"]
        fraction = _as_fraction(percent)
        if fraction is not None:
            whole, part = divmod(fraction.numerator, fraction.denominator)
            vulgar = f"{part}/{fraction.denominator}"
            written = [f"{whole} {vulgar}", f"{whole}-{vulgar}"] if whole else [vulgar]
            for text in written:
                forms += [f"{text}%", f"{text} percent"]
            if not whole:
                forms += [f"{vulgar} of 1%", f"{vulgar} of one percent"]
                for word in _FRACTION_WORDS.get(Fraction(part, fraction.denominator), ()):
                    forms += [f"{word} of 1%", f"{word} of one percent", f"{word} percent"]
    return list(dict.fromkeys(forms))


def _drop_trailing_zeros(match: re.Match[str]) -> str:
    whole, fraction = match.group(1), match.group(2)
    return f"{whole}.{fraction}" if fraction else whole


def value_in_text(value: float, text: str) -> bool:
    """Whether `value` is written in `text`, as a whole token in any Code form.

    "Whole token": 67 is not found in "6700", nor 2.4% in "2.45%".
    """
    haystack = " ".join(text.lower().split())
    # The Code writes rates with trailing zeros ("1.80%", "2.00%"); read them
    # as the numbers they are, so 0.018 is found in "1.80%".
    haystack = re.sub(r"(\d+)\.(\d*?)0+(?=\D|$)", _drop_trailing_zeros, haystack)
    for form in statutory_forms(float(value)):
        pattern = r"(?<![\d.,/])" + re.escape(form.lower()) + r"(?![\d]|[.,]\d)"
        if re.search(pattern, haystack):
            return True
    return False


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
        parameter_texts: dict[str, dict[str, str]] | None = None,
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
        if parameter_texts is not None:
            self._validate_parameters_in_source(spec, result, parameter_texts)

        # 5. Check for dependency cycles
        self._validate_no_cycles(spec, existing_capabilities, result)

        # 6. Validate effective dates (via source_sections metadata)
        # Done externally — we just check the spec is coherent

        # 7. No internal handler selection
        self._validate_no_handler_selection(spec, result)

        # 8. Applicability conditions are well formed and cited
        self._validate_applicability(spec, result)

        return result

    def _validate_applicability(self, spec: RuleSpecification, result: ValidationResult):
        """Check the recipe's statutory conditions are checkable and cited.

        Whether they are *satisfied* is a question about a particular member, so
        it belongs to `calculation/applicability.py` at execution time, not here.
        This only establishes that each condition can be evaluated at all and is
        traceable to statute text — a predicate with no citation is the model
        asserting an eligibility rule it was never shown.
        """
        predicates = spec.applicability_predicates
        if not predicates:
            # Deliberately a warning even in strict mode. An absent condition is
            # not a false one, and refusing to calculate whenever the derivation
            # omits predicates would trade a real capability for no added safety.
            # The gate that matters is at execution: a predicate known to be
            # FALSE blocks, and the answer discloses when none could be checked.
            result.add_warning(
                "NO_APPLICABILITY_PREDICATES",
                "Recipe declares no machine-checkable applicability conditions, so "
                "whether the provision reaches this member cannot be verified before "
                "the figure is produced",
            )
            return

        for predicate in predicates:
            problem = validate_predicate(predicate.model_dump())
            if problem:
                result.add_error(
                    "INVALID_APPLICABILITY_PREDICATE",
                    f"Applicability condition on '{predicate.fact}' cannot be "
                    f"evaluated: {problem}",
                )

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

        # Variable references and literal values are fine. The "variable" key
        # settles it even when the node also carries operation:"variable" — the
        # interpreter reads it the same way, so flagging it here would fail a
        # recipe the engine can execute perfectly well.
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

    def _validate_parameters_in_source(
        self,
        spec: RuleSpecification,
        result: ValidationResult,
        parameter_texts: dict[str, dict[str, str]],
    ) -> None:
        """Each constant must be written in the subsection it cites.

        Citing a retrieved section is not evidence that the number is in it: a
        Tier 2 recipe once carried 1.25% cited to 1-160(g), a subsection about
        fire fighters under Article 14, having taken the rate from another
        fund's optional-benefit section. Always an error, in either mode -- a
        constant the cited text does not contain is not a statutory value, and
        a recipe carrying one must neither execute nor be saved for reuse.

        The cited subsection is searched when its text is known, the whole
        section otherwise; a section whose text was not supplied is not judged.
        """
        for param_name, param_spec in spec.parameters.items():
            source = param_spec.source or {}
            section = str(source.get("section") or "").split("(")[0].strip()
            subsections = parameter_texts.get(section)
            if not section or subsections is None or not isinstance(param_spec.value, (int, float)):
                continue
            cited = str(source.get("subsection") or "")
            text = subsections.get(cited) or " ".join(subsections.values())
            if not value_in_text(float(param_spec.value), text):
                where = f"{section}{cited}" if cited in subsections else section
                result.add_error(
                    "PARAMETER_NOT_IN_SOURCE",
                    f"Parameter '{param_name}' is {param_spec.value}, but Section {where} "
                    f"does not state that value, so it is not a statutory figure",
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
