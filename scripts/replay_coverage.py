"""Replay the reported conversation and show what coverage now decides each turn.

The conversation in `my_test.txt`, turn for turn, with the state that was missing
when it happened printed alongside each answer. Run before and after to see the
difference; the interesting columns are the act read off the member's words, the
basis coverage ended up resting on, and whether a figure was produced.

    uv run python scripts/replay_coverage.py
    uv run python scripts/replay_coverage.py --hypothetical
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

logging.basicConfig(level=logging.WARNING)

from eval_agent import TEST_EMPLOYEE, run_turn  # noqa: E402

# Verbatim from my_test.txt.
REPORTED = [
    "i am a park employee, how is my pension calculated?",
    "i thought park employee's came under article 12, is it correct, if not what "
    "type of employees come under article 12?",
    'so when i said i was a park employee why did you say this "As a park employee '
    'covered by Article 11, your pension is calculated under Section 11-134(a)?"',
    "i am an employee of a board of park commissioners, how is my pension calculated?",
]

# The other half of the design: a premise, explored across turns, then left.
HYPOTHETICAL = [
    "if I were a park employee, how would my pension be calculated?",
    "and what if I had 30 years of service?",
    "Now go back to my actual record. What is my pension really?",
]


def _money(result: dict) -> str:
    for entry in (result or {}).values():
        base = entry.get("base") or {}
        value = base.get("result") or base.get("value")
        if value:
            return f"${value}"
    return "—"


async def main(turns: list[str]) -> None:
    thread = str(uuid.uuid4())
    for index, question in enumerate(turns, 1):
        result = await run_turn(question, thread, TEST_EMPLOYEE)
        coverage = result["coverage"]
        reference = result["self_reference"]
        frame = result["hypothetical_frame"]

        print("=" * 78)
        print(f"TURN {index}  {question}")
        print("-" * 78)
        print(
            f"  said about self : {reference.get('text') or '—'!r}"
            f"  ({reference.get('act') or 'nothing'})"
        )
        print(
            f"  coverage        : article={coverage.get('article')} "
            f"basis={coverage.get('basis')} conflict={coverage.get('conflict')}"
            f" {'(' + coverage['why'] + ')' if coverage.get('why') else ''}"
        )
        if coverage.get("candidates"):
            for candidate in coverage["candidates"]:
                print(
                    f"                    candidate art {candidate.get('article')} "
                    f"per {candidate.get('section')}: {(candidate.get('test') or '')[:56]}"
                )
        print(f"  supposing       : {frame.get('premise') or '—'}")
        print(f"  asked so far    : {result['clarifications_asked']}")
        print(f"  asking this turn: {bool(result['clarification'])}")
        print(f"  articles read   : {result['articles']}")
        print(f"  figure          : {_money(result['calculation'])}")
        print(f"  guardrail       : {'passed' if result['guardrail_passed'] else 'FAILED'}")
        for failure in result["guardrail_report"].get("hard_failures", []):
            print(f"                    {failure['code']}")
        print("-" * 78)
        print(result["answer"][:1100])
        print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--hypothetical", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(HYPOTHETICAL if args.hypothetical else REPORTED))
