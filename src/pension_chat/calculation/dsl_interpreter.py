"""Deterministic DSL rule interpreter (§7.3).

Evaluates declarative rule_specification JSONB from the formula registry.
No LLM involvement — pure arithmetic. This is the core calculation engine.

Supported operations: ADD, SUBTRACT, MULTIPLY, DIVIDE, MIN, MAX, IF,
COMPARE, LOOKUP, PERCENTAGE, ROUND, CAP, FLOOR, CEILING, date comparisons,
ranges, and controlled references to other approved capabilities.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from pension_chat.calculation.dsl_types import (
    ComparisonOp,
    InputType,
    OperationType,
    RuleSpecification,
)

logger = logging.getLogger("pension_chat.calculation.interpreter")


class DSLExecutionError(Exception):
    """Raised when a DSL operation cannot be executed."""
    pass


class DSLInterpreter:
    """Deterministic interpreter for the calculation DSL.

    Takes a RuleSpecification and a set of input values, evaluates the
    calculation tree, and returns the result. No model calls, no side effects.
    """

    def __init__(self, capability_resolver=None):
        """
        Args:
            capability_resolver: Optional async callable that resolves
                                 CAPABILITY_REF operations by looking up
                                 and executing another recipe.
        """
        self._capability_resolver = capability_resolver

    def execute(
        self,
        rule_spec: dict | RuleSpecification,
        inputs: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute a rule specification with the given inputs.

        Args:
            rule_spec: The rule specification (dict or RuleSpecification).
            inputs: Input values keyed by input name.

        Returns:
            dict with 'result' (the calculated value), 'intermediate' (values
            published by nodes carrying ``result_name``), 'steps' (those same
            values in evaluation order), and 'inputs_used'/'parameters_used' —
            the values and statutory citations the result was actually built
            from, so an answer can show the path to a figure instead of
            asserting it.
        """
        if isinstance(rule_spec, dict):
            spec = RuleSpecification(**rule_spec)
        else:
            spec = rule_spec

        if spec.execution_type == "custom":
            raise DSLExecutionError(
                f"Custom handlers must be executed through the handler registry, "
                f"not the DSL interpreter. Handler: {spec.handler_capability}"
            )

        # Build the evaluation context: inputs + parameters
        context: dict[str, Any] = {}
        intermediates: dict[str, Any] = {}
        steps: list[dict[str, Any]] = []
        parameters_used: dict[str, Any] = {}
        inputs_used: dict[str, Any] = {}
        input_types: dict[str, str] = {}

        # Load parameters (fixed statutory values)
        for param_name, param_spec in spec.parameters.items():
            context[param_name] = self._coerce_value(param_spec.value, None)
            # Every statutory constant carries the section it was read from.
            # Keeping the citation attached to the value is what lets the answer
            # say where a rate came from without the model recalling it.
            parameters_used[param_name] = {
                "value": param_spec.value,
                "source": dict(param_spec.source or {}),
            }

        # Load inputs (user/employee-provided values)
        for input_spec in spec.inputs:
            if input_spec.name in inputs:
                context[input_spec.name] = self._coerce_value(
                    inputs[input_spec.name], input_spec.type
                )
            elif input_spec.default_value is not None:
                context[input_spec.name] = self._coerce_value(
                    input_spec.default_value, input_spec.type
                )
            elif input_spec.required:
                raise DSLExecutionError(
                    f"Missing required input: {input_spec.name}"
                )

        for input_spec in spec.inputs:
            if input_spec.name in context:
                inputs_used[input_spec.name] = context[input_spec.name]
                # The declared type travels with the value so presentation does
                # not have to guess it from the name: "creditable_service_years"
                # reads as money to any keyword rule that looks for "credit".
                input_types[input_spec.name] = input_spec.type.value

        # Execute the calculation tree
        result = self._evaluate(spec.calculation, context, intermediates, steps)

        return {
            "result": result,
            "output_name": spec.output_name,
            "output_type": spec.output_type.value,
            "intermediate": intermediates,
            "steps": steps,
            "inputs_used": inputs_used,
            "input_types": input_types,
            "parameters_used": parameters_used,
        }

    def _evaluate(
        self,
        node: dict,
        context: dict[str, Any],
        intermediates: dict[str, Any],
        steps: list[dict[str, Any]] | None = None,
    ) -> Any:
        """Recursively evaluate a calculation node."""
        if not isinstance(node, dict):
            return node

        # Direct variable reference. A derivation occasionally wraps one as
        # {"operation": "variable", "variable": "x"} rather than {"variable":
        # "x"}; the intent is not in doubt, and rejecting the whole recipe over
        # the wrapper costs the member a calculation for nothing.
        if "variable" in node:
            var_name = node["variable"]
            if var_name not in context:
                raise DSLExecutionError(f"Undefined variable: {var_name}")
            return context[var_name]

        # Literal value
        if "value" in node and "operation" not in node:
            return self._coerce_value(node["value"], None)

        operation = node.get("operation")
        if operation is None:
            raise DSLExecutionError(f"Node has no operation or variable: {node}")

        op = OperationType(operation)

        # Evaluate operands
        operands = [
            self._evaluate(o, context, intermediates, steps)
            if isinstance(o, dict) else
            (context.get(o, o) if isinstance(o, str) and o in context else o)
            for o in node.get("operands", [])
        ]

        result = self._execute_operation(op, operands, node, context, intermediates, steps)

        # Store intermediate result if named
        if "result_name" in node:
            intermediates[node["result_name"]] = result
            context[node["result_name"]] = result
            if steps is not None:
                # Evaluation order is bottom-up, so appending here yields the
                # steps in the order a reader would work through them.
                steps.append(
                    {
                        "name": node["result_name"],
                        "operation": op.value,
                        "value": result,
                    }
                )

        return result

    def _execute_operation(
        self,
        op: OperationType,
        operands: list[Any],
        node: dict,
        context: dict[str, Any],
        intermediates: dict[str, Any],
        steps: list[dict[str, Any]] | None = None,
    ) -> Any:
        """Execute a single operation."""
        try:
            match op:
                case OperationType.ADD:
                    return sum(Decimal(str(o)) for o in operands)

                case OperationType.SUBTRACT:
                    if len(operands) < 2:
                        raise DSLExecutionError("SUBTRACT requires at least 2 operands")
                    result = Decimal(str(operands[0]))
                    for o in operands[1:]:
                        result -= Decimal(str(o))
                    return result

                case OperationType.MULTIPLY:
                    result = Decimal("1")
                    for o in operands:
                        result *= Decimal(str(o))
                    return result

                case OperationType.DIVIDE:
                    if len(operands) != 2:
                        raise DSLExecutionError("DIVIDE requires exactly 2 operands")
                    divisor = Decimal(str(operands[1]))
                    if divisor == 0:
                        raise DSLExecutionError("Division by zero")
                    return Decimal(str(operands[0])) / divisor

                case OperationType.MIN:
                    return min(Decimal(str(o)) for o in operands)

                case OperationType.MAX:
                    return max(Decimal(str(o)) for o in operands)

                case OperationType.PERCENTAGE:
                    if len(operands) != 2:
                        raise DSLExecutionError("PERCENTAGE requires 2 operands (value, rate)")
                    return Decimal(str(operands[0])) * Decimal(str(operands[1]))

                case OperationType.ROUND:
                    precision = node.get("precision", 2)
                    value = Decimal(str(operands[0]))
                    return value.quantize(
                        Decimal(10) ** -precision, rounding=ROUND_HALF_UP
                    )

                case OperationType.CAP | OperationType.CEILING:
                    value = Decimal(str(operands[0]))
                    limit = self._limit_of(
                        node, operands, context, intermediates, default=value, steps=steps
                    )
                    return min(value, limit)

                case OperationType.FLOOR:
                    value = Decimal(str(operands[0]))
                    limit = self._limit_of(
                        node, operands, context, intermediates,
                        default=Decimal(0), steps=steps,
                    )
                    return max(value, limit)

                case OperationType.IF:
                    condition = self._evaluate_condition(
                        node.get("condition", {}), context, intermediates
                    )
                    if condition:
                        return self._evaluate(
                            node.get("then_branch", {}), context, intermediates, steps
                        )
                    else:
                        return self._evaluate(
                            node.get("else_branch", {}), context, intermediates, steps
                        )

                case OperationType.COMPARE:
                    return self._compare(
                        self._evaluate(node.get("left", operands[0] if operands else None), context, intermediates),
                        ComparisonOp(node.get("operator", "eq")),
                        self._evaluate(node.get("right", operands[1] if len(operands) > 1 else None), context, intermediates),
                    )

                case OperationType.LOOKUP:
                    table = node.get("table", [])
                    key_name = node.get("key", "")
                    key_value = context.get(key_name, operands[0] if operands else None)
                    for row in table:
                        if self._matches_lookup_row(row, key_value):
                            return self._coerce_value(row.get("value"), None)
                    # Return default or raise
                    default = node.get("default")
                    if default is not None:
                        return self._coerce_value(default, None)
                    raise DSLExecutionError(f"No matching lookup entry for {key_name}={key_value}")

                case OperationType.DATE_DIFF_YEARS:
                    date_a = self._to_date(
                        self._evaluate(node.get("date_a", operands[0] if operands else None), context, intermediates)
                    )
                    date_b = self._to_date(
                        self._evaluate(node.get("date_b", operands[1] if len(operands) > 1 else None), context, intermediates)
                    )
                    return self._date_diff_years(date_a, date_b)

                case OperationType.DATE_DIFF_MONTHS:
                    date_a = self._to_date(
                        self._evaluate(node.get("date_a", operands[0] if operands else None), context, intermediates)
                    )
                    date_b = self._to_date(
                        self._evaluate(node.get("date_b", operands[1] if len(operands) > 1 else None), context, intermediates)
                    )
                    return self._date_diff_months(date_a, date_b)

                case OperationType.DATE_COMPARE:
                    date_a = self._to_date(
                        self._evaluate(node.get("date_a", operands[0] if operands else None), context, intermediates)
                    )
                    date_b = self._to_date(
                        self._evaluate(node.get("date_b", operands[1] if len(operands) > 1 else None), context, intermediates)
                    )
                    operator = ComparisonOp(node.get("operator", "ge"))
                    return self._compare(date_a, operator, date_b)

                case OperationType.IN_RANGE:
                    value = Decimal(str(operands[0]))
                    low = self._bound_of(
                        node, "low", operands, 1, context, intermediates, Decimal(0)
                    )
                    high = self._bound_of(
                        node, "high", operands, 2, context, intermediates, value
                    )
                    inclusive = node.get("inclusive", True)
                    if inclusive:
                        return low <= value <= high
                    return low < value < high

                case OperationType.CAPABILITY_REF:
                    raise DSLExecutionError(
                        "CAPABILITY_REF requires async execution through the agent graph"
                    )

                case _:
                    raise DSLExecutionError(f"Unknown operation: {op}")

        except DSLExecutionError:
            raise
        except Exception as e:
            raise DSLExecutionError(f"Error executing {op.value}: {e}") from e

    def _limit_of(
        self,
        node: dict,
        operands: list[Any],
        context: dict[str, Any],
        intermediates: dict[str, Any],
        default: Decimal,
        steps: list[dict[str, Any]] | None = None,
    ) -> Decimal:
        """Resolve the `limit` of a CAP/FLOOR/CEILING node.

        The limit is frequently itself a calculation — Section 11-134(a) caps
        the annuity at "80% of highest average annual salary", which is a
        percentage node, not a constant. Coercing it straight to Decimal (as
        this did originally) raised ConversionSyntax on the dict, so any recipe
        expressing a statutory maximum failed at execution time.
        """
        return self._bound_of(
            node, "limit", operands, 1, context, intermediates, default, steps
        )

    def _bound_of(
        self,
        node: dict,
        key: str,
        operands: list[Any],
        operand_index: int,
        context: dict[str, Any],
        intermediates: dict[str, Any],
        default: Decimal,
        steps: list[dict[str, Any]] | None = None,
    ) -> Decimal:
        """Resolve a named numeric bound that may be a literal or a sub-tree."""
        if key in node:
            raw = node[key]
            resolved = (
                self._evaluate(raw, context, intermediates, steps)
                if isinstance(raw, dict)
                else context.get(raw, raw) if isinstance(raw, str) else raw
            )
        elif len(operands) > operand_index:
            resolved = operands[operand_index]
        else:
            resolved = default

        try:
            return Decimal(str(resolved))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise DSLExecutionError(
                f"Could not resolve '{key}' to a number: {resolved!r}"
            ) from exc

    def _evaluate_condition(
        self,
        condition: dict,
        context: dict[str, Any],
        intermediates: dict[str, Any],
    ) -> bool:
        """Evaluate a condition node to a boolean."""
        if not condition:
            return False

        if "operation" in condition:
            result = self._evaluate(condition, context, intermediates)
            return bool(result)

        # Simple comparison condition
        left = self._evaluate(
            condition.get("left", condition.get("variable")),
            context, intermediates,
        )
        right = self._evaluate(condition.get("right"), context, intermediates)
        operator = ComparisonOp(condition.get("operator", "eq"))
        return self._compare(left, operator, right)

    def _compare(self, left: Any, op: ComparisonOp, right: Any) -> bool:
        """Compare two values."""
        try:
            # Try numeric comparison
            l_val = Decimal(str(left))
            r_val = Decimal(str(right))
        except (InvalidOperation, TypeError, ValueError):
            l_val = left
            r_val = right

        match op:
            case ComparisonOp.EQ:
                return l_val == r_val
            case ComparisonOp.NE:
                return l_val != r_val
            case ComparisonOp.GT:
                return l_val > r_val
            case ComparisonOp.GE:
                return l_val >= r_val
            case ComparisonOp.LT:
                return l_val < r_val
            case ComparisonOp.LE:
                return l_val <= r_val

    def _matches_lookup_row(self, row: dict, key_value: Any) -> bool:
        """Check if a lookup table row matches the key value."""
        if "match" in row:
            return str(row["match"]) == str(key_value)
        if "min" in row and "max" in row:
            val = Decimal(str(key_value))
            return Decimal(str(row["min"])) <= val <= Decimal(str(row["max"]))
        return False

    def _coerce_value(self, value: Any, input_type: InputType | str | None) -> Any:
        """Coerce a value to the appropriate Python type."""
        if value is None:
            return None

        if isinstance(input_type, str):
            try:
                input_type = InputType(input_type)
            except ValueError:
                input_type = None

        match input_type:
            case InputType.MONEY | InputType.DECIMAL | InputType.PERCENTAGE:
                return Decimal(str(value))
            case InputType.INTEGER:
                return int(value)
            case InputType.DATE:
                return self._to_date(value)
            case InputType.BOOLEAN:
                return bool(value)
            case InputType.YEARS:
                return Decimal(str(value))
            case _:
                # Try Decimal for numeric-looking values
                try:
                    return Decimal(str(value))
                except (InvalidOperation, ValueError):
                    return value

    def _to_date(self, value: Any) -> date:
        """Convert a value to a date."""
        if isinstance(value, date):
            return value
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, str):
            return date.fromisoformat(value)
        raise DSLExecutionError(f"Cannot convert to date: {value}")

    def _date_diff_years(self, start: date, end: date) -> Decimal:
        """Calculate the difference in years between two dates."""
        diff = end.year - start.year
        if (end.month, end.day) < (start.month, start.day):
            diff -= 1
        # Add fractional year
        if end >= start.replace(year=start.year + diff):
            remaining_start = start.replace(year=start.year + diff)
            remaining_end = start.replace(year=start.year + diff + 1)
            fraction = (end - remaining_start).days / (remaining_end - remaining_start).days
        else:
            fraction = 0
        return Decimal(str(diff)) + Decimal(str(round(fraction, 4)))

    def _date_diff_months(self, start: date, end: date) -> int:
        """Calculate the difference in months between two dates."""
        return (end.year - start.year) * 12 + (end.month - start.month)
