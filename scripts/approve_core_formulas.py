"""Show the MEABF core formulas for sign-off, and approve them into the registry.

    uv run python scripts/approve_core_formulas.py                          # print for review
    uv run python scripts/approve_core_formulas.py --approve --reviewer "<name>"

The formulas live in `src/pension_chat/calculation/core_meabf.py`, with the
readings a reviewer should confirm in its docstring; their hand-worked figures
and verbatim quotes are proved by `tests/test_core_formulas_meabf.py`. Approving
saves each one through the same path a derived formula is saved by (so its
evidence fingerprint is taken from the indexed statute and a later amendment
marks it stale), then records it as `approved` with the reviewer's name. An
approved formula is reused on every matching turn instead of being derived.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _show() -> None:
    from pension_chat.calculation.core_meabf import CORE_FORMULAS

    for formula in CORE_FORMULAS:
        spec = formula["spec"]
        print(f"\n=== Tier {formula['tier']}: {formula['formula_name']}")
        print(f"    sections: {', '.join(formula['source_sections'])}   basis: {formula['selection_basis']}")
        for name, param in spec["parameters"].items():
            where = f"{param['source']['section']}{param['source'].get('subsection') or ''}"
            print(f"    {name} = {param['value']}  ({where})  \"{param['quote']}\"")
        for pred in spec["applicability_predicates"]:
            where = f"{pred['source']['section']}{pred['source'].get('subsection') or ''}"
            print(f"    applies if {pred['fact']} {pred['operator']} {pred['value']}  ({where})  \"{pred['quote']}\"")
    print("\nProved against hand-worked figures: uv run pytest tests/test_core_formulas_meabf.py -v")


async def _approve(reviewer: str) -> None:
    os.environ["PERSIST_DERIVED_RECIPES"] = "true"
    os.environ["REGISTRY_WRITES_ENABLED"] = "true"
    from sqlalchemy import text

    from pension_chat.agent.derivation import DerivedRecipe
    from pension_chat.agent.llm import close_clients
    from pension_chat.agent.registry import persist_recipe
    from pension_chat.agent.retrieval import canonical_section_texts
    from pension_chat.calculation.core_meabf import CORE_FORMULAS
    from pension_chat.db.session import get_admin_session

    try:
        for formula in CORE_FORMULAS:
            texts = await canonical_section_texts(formula["source_sections"])
            missing = [s for s in formula["source_sections"] if s not in texts]
            if missing:
                raise SystemExit(f"Tier {formula['tier']}: sections not in the index: {missing}")
            recipe = DerivedRecipe(
                formula_type=formula["formula_type"],
                rule_specification=formula["spec"],
                formula_name=formula["formula_name"],
                controlling_section=formula["controlling_section"],
                controlling_subsection=formula["controlling_subsection"],
                selection_basis=formula["selection_basis"],
                source_sections=formula["source_sections"],
                applicability=json.dumps(formula["spec"]["applicability_predicates"]),
                derivation_notes="Written by hand for sign-off; see calculation/core_meabf.py.",
            )
            formula_id = await persist_recipe(
                recipe, article_number=formula["article_number"], tier=formula["tier"],
                section_texts=texts, risk_tier="low", validation={"errors": [], "warnings": []},
            )
            if formula_id is None:
                raise SystemExit(f"Tier {formula['tier']}: the registry did not save it")
            async with get_admin_session() as session:
                result = await session.execute(
                    text("update formula_registry set knowledge_status = 'approved', "
                         "reviewed_by = :who, reviewed_at = :at where formula_id = :id"),
                    {"who": reviewer, "at": datetime.now(UTC), "id": formula_id},
                )
                await session.commit()
            # `persist_recipe` returns the id on a unique-key clash too, so a row
            # that never landed (a missing pension_articles row, say) would
            # otherwise be reported as approved.
            if result.rowcount != 1:
                raise SystemExit(f"Tier {formula['tier']}: {formula_id} is not in the registry; nothing approved")
            print(f"approved  tier {formula['tier']}  {formula_id}  (reviewer: {reviewer})")
    finally:
        await close_clients()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--approve", action="store_true", help="save them as approved (after sign-off)")
    parser.add_argument("--reviewer", help="who signed them off (required with --approve)")
    args = parser.parse_args()
    if not args.approve:
        _show()
        return
    if not args.reviewer:
        parser.error("--approve needs --reviewer: an approval says who gave it")
    asyncio.run(_approve(args.reviewer))


if __name__ == "__main__":
    main()
