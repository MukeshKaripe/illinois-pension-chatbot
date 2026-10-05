"""A model call can't be added without registering its node in agent/budgets.py (spec §4.2).

Every call goes through llm.complete, llm.call_with_tools or llm.stream with a
literal node= name. This scans src/ for those calls; a new node fails here until
it is added to MODEL_NODES -- and, if it runs before the first answer word, to a
turn type's budget -- in the same change, where review sees it.
"""
from __future__ import annotations

import ast
from pathlib import Path

from pension_chat.agent.budgets import MODEL_NODES

SRC = Path(__file__).resolve().parents[1] / "src" / "pension_chat"
ENTRY_POINTS = {"complete", "call_with_tools", "stream", "stream_llm"}


def _call_sites() -> list[tuple[str, int, str | None]]:
    sites = []
    for path in SRC.rglob("*.py"):
        if path.name == "llm.py":   # the entry points themselves forward node=node
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name not in ENTRY_POINTS:
                continue
            keyword = next((k.value for k in node.keywords if k.arg == "node"), None)
            literal = keyword.value if isinstance(keyword, ast.Constant) else None
            sites.append((str(path.relative_to(SRC)), node.lineno, literal))
    return sites


def test_every_model_call_names_a_registered_node() -> None:
    sites = _call_sites()
    assert sites, "the scan found no model calls -- the entry-point names have changed"
    unnamed = [f"{p}:{line}" for p, line, node in sites if node is None]
    assert not unnamed, f"model calls without a literal node= name: {unnamed}"
    unknown = [f"{p}:{line} node={node!r}" for p, line, node in sites if node not in MODEL_NODES]
    assert not unknown, f"register these in agent/budgets.py MODEL_NODES: {unknown}"
