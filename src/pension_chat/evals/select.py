"""Run what a change touched. A path maps to the areas it can affect; the
shared core (the loop, the graph, the guardrails, the tools, the model
client, the eval harness itself) can affect every area, so it selects
everything. Critical cases always run.
"""

from __future__ import annotations

import subprocess

from pension_chat.evals.cases import AREAS, Case

P = "src/pension_chat/"
AREA_PATHS: dict[str, tuple[str, ...]] = {
    "retrieval": (P + "agent/retrieval.py", P + "agent/corpus.py", P + "ingestion/"),
    "citations": (P + "agent/grounding.py",),
    "calculation": (P + "calculation/", P + "agent/registry.py", P + "agent/derivation.py",
                    P + "agent/facts.py", P + "agent/card.py"),
    "clarification": (P + "agent/gaps.py",),
    "assumptions": (P + "agent/worksheet.py",),
    "boundary": (P + "agent/coverage.py", P + "funds.py"),
    "continuity": (P + "agent/ledger.py", P + "agent/memory.py", P + "agent/checkpointer.py"),
    "recommendation": (P + "agent/recommend.py", P + "agent/objectives.py"),
    "records": (P + "records.py",),
    "voice": (P + "agent/presentation.py", P + "agent/blocks.py"),
}
CORE: tuple[str, ...] = (
    P + "agent/loop.py", P + "agent/graph.py", P + "agent/guardrails.py", P + "agent/tools.py",
    P + "agent/llm.py", P + "agent/state.py", P + "agent/nodes.py", P + "config.py", P + "evals/", "evals/rubric.yaml",
    "evals/fixtures/",
)


def areas_for(changed_paths: list[str]) -> set[str]:
    areas: set[str] = set()
    for path in changed_paths:
        path = path.replace("\\", "/")
        if any(path.startswith(core) for core in CORE):
            return set(AREAS)
        areas |= {area for area, prefixes in AREA_PATHS.items() if any(path.startswith(p) for p in prefixes)}
    return areas


def changed_files(base: str = "main") -> list[str]:
    committed = subprocess.run(["git", "diff", "--name-only", f"{base}...HEAD"],
                               capture_output=True, text=True, check=True).stdout.split()
    working = subprocess.run(["git", "diff", "--name-only", "HEAD"],
                             capture_output=True, text=True, check=True).stdout.split()
    return sorted(set(committed) | set(working))


def select(cases: list[Case], *, tier: str | None = "full", families: list[str] | None = None,
           ids: list[str] | None = None, changed: list[str] | None = None) -> list[Case]:
    """`tier=None` means: smoke when nothing else narrows the run, otherwise every
    tier -- a case named by id, family or a change is found whatever its tier."""
    if tier is None:
        tier = "full" if (families or ids or changed is not None) else "smoke"
    picked = [c for c in cases if tier in c.tiers or tier == "full"]
    if families:
        picked = [c for c in picked if c.family in families]
    if ids:
        picked = [c for c in picked if c.id in ids]
    if changed is not None:
        areas = areas_for(changed)
        touched = {p.replace("\\", "/") for p in changed}
        picked = [c for c in picked if c.critical or set(c.areas) & areas
                  or c.path.as_posix() in touched or any(c.path.as_posix().endswith(t) for t in touched)]
    return picked
