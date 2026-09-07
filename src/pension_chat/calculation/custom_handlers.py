"""Controlled custom handler registry for complex actuarial logic (§7.3).

Handler capabilities are internal; the LLM never selects them.
The registry resolves handler_capability strings to implementations.

Initially empty — populated as needed for complex calculations that
cannot be expressed in the declarative DSL.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("pension_chat.calculation.custom_handlers")


class CustomHandlerError(Exception):
    """Raised when a custom handler fails."""
    pass


class HandlerRegistry:
    """Registry for controlled custom calculation handlers.

    Each handler is a pure function: (inputs: dict) -> dict
    The LLM never selects handlers directly — the formula_registry's
    execution_reference points to a handler_capability string,
    which this registry resolves to the implementation.
    """

    def __init__(self):
        self._handlers: dict[str, Callable[[dict], dict]] = {}

    def register(self, capability_id: str, handler: Callable[[dict], dict]):
        """Register a custom handler for a capability ID."""
        self._handlers[capability_id] = handler
        logger.info("Registered custom handler: %s", capability_id)

    def has_handler(self, capability_id: str) -> bool:
        """Check if a handler exists for the capability."""
        return capability_id in self._handlers

    def execute(self, capability_id: str, inputs: dict[str, Any]) -> dict[str, Any]:
        """Execute a custom handler.

        Args:
            capability_id: The handler capability string.
            inputs: Input values for the calculation.

        Returns:
            Dict with calculation results.

        Raises:
            CustomHandlerError: If the handler doesn't exist or fails.
        """
        if capability_id not in self._handlers:
            raise CustomHandlerError(
                f"No handler registered for capability: {capability_id}"
            )

        try:
            return self._handlers[capability_id](inputs)
        except Exception as e:
            raise CustomHandlerError(
                f"Handler '{capability_id}' failed: {e}"
            ) from e


# Global registry instance
handler_registry = HandlerRegistry()


# ── Example handler registration (for future use) ────────────────
# handler_registry.register(
#     "actuarial.single_life_annuity",
#     lambda inputs: {"annuity_factor": ...}
# )
