"""One reference-guided judge call per turn.

It decides three things -- whether the user's goal was accomplished, whether
the answer was relevant, and whether it stayed within the boundary -- against
the turn's own GOAL and REFERENCE, and quotes the answer for each. It runs only
after every code check passed; code can veto a turn, and the judge cannot
overrule it. It gates only once calibrated against the product owner's labels
(>=18 of 20 for this rubric version); until then it is reported alone.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from pension_chat.evals.cases import BEHAVIOURS, Turn
from pension_chat.evals.trace import TurnTrace

GOALS = ("yes", "partly", "no")


@dataclass
class Verdict:
    goal: str
    relevant: bool
    within_boundary: bool
    behaviour: str
    voice: int
    hedging: int
    clarity: int
    quotes: dict[str, str] = field(default_factory=dict)
    reason: str = ""

    def passes(self, acceptable: list[str]) -> bool:
        return (self.goal == "yes" and self.relevant and self.within_boundary
                and (not acceptable or self.behaviour in acceptable))


def rubric_version(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def _parse(text: str) -> Verdict | None:
    try:
        raw = json.loads(text)
        v = Verdict(
            goal=str(raw["goal"]), relevant=bool(raw["relevant"]),
            within_boundary=bool(raw["within_boundary"]), behaviour=str(raw["behaviour"]),
            voice=int(raw["voice"]), hedging=int(raw["hedging"]), clarity=int(raw["clarity"]),
            quotes=dict(raw.get("quotes") or {}), reason=str(raw.get("reason") or ""),
        )
    except (ValueError, KeyError, TypeError):
        return None
    if v.goal not in GOALS or v.behaviour not in BEHAVIOURS:
        return None
    return v


def _messages(rubric: dict[str, Any], turn: Turn, trace: TurnTrace,
              history: list[tuple[str, str]]) -> list[dict[str, str]]:
    criteria = "\n".join(f"{k}: {rubric[k]}" for k in
                         ("goal", "relevant", "within_boundary", "behaviour", "voice", "hedging", "clarity"))
    earlier = "\n".join(f"USER: {q}\nASSISTANT: {a}" for q, a in history) or "(none)"
    evidence = ", ".join(f"{s}{v}" for s, v in trace.rendered.items()) or "(none recorded)"
    user = (
        f"CONVERSATION SO FAR:\n{earlier}\n\nQUESTION:\n{trace.question}\n\n"
        f"GOAL:\n{turn.goal}\n\nREFERENCE:\n{turn.reference}\n\n"
        f"STATUTE EVIDENCE THE ASSISTANT HAD (sections and subsections): {evidence}\n\n"
        f"ANSWER:\n{trace.final_answer}\n\n"
        'Reply as JSON: {"goal": "yes|partly|no", "relevant": bool, "within_boundary": bool, '
        '"behaviour": "...", "voice": 1-5, "hedging": 1-5, "clarity": 1-5, '
        '"quotes": {"goal": "...", "relevant": "...", "within_boundary": "..."}, "reason": "..."}'
    )
    return [{"role": "system", "content": f"{rubric['instructions']}\n\n{criteria}"},
            {"role": "user", "content": user}]


async def judge_turn(
    turn: Turn, trace: TurnTrace, history: list[tuple[str, str]], *,
    rubric_path: Path, cache_dir: Path,
    complete: Callable[..., Awaitable[str]] | None = None,
) -> Verdict | None:
    if complete is None:
        from pension_chat.agent.llm import complete as llm_complete
        complete = llm_complete
    version = rubric_version(rubric_path)
    key = hashlib.sha256(json.dumps(
        [version, turn.goal, turn.reference, trace.question, trace.final_answer, history]).encode()).hexdigest()
    cached = cache_dir / f"{key}.json"
    if cached.exists():
        return Verdict(**json.loads(cached.read_text(encoding="utf-8")))
    messages = _messages(yaml.safe_load(rubric_path.read_text(encoding="utf-8")), turn, trace, history)
    for _ in range(2):
        verdict = _parse(await complete(node="eval_judge", messages=messages, json_mode=True,
                                        reasoning_effort="low"))
        if verdict is not None:
            cache_dir.mkdir(parents=True, exist_ok=True)
            cached.write_text(json.dumps(asdict(verdict)), encoding="utf-8")
            return verdict
    return None


def calibration(labels_dir: Path, verdicts: dict[str, Verdict]) -> tuple[int, int]:
    """Labels are YAML files of {id: "<case>#<turn>", goal, relevant, within_boundary}."""
    agreed = total = 0
    for path in sorted(labels_dir.glob("*.yaml")):
        for label in yaml.safe_load(path.read_text(encoding="utf-8")) or []:
            verdict = verdicts.get(label["id"])
            if verdict is None:
                continue
            total += 1
            agreed += (verdict.goal == label["goal"] and verdict.relevant == label["relevant"]
                       and verdict.within_boundary == label["within_boundary"])
    return agreed, total


def is_calibrated(eval_root: Path, rubric_path: Path) -> bool:
    path = eval_root / "calibration.json"
    if not path.exists():
        return False
    data = json.loads(path.read_text(encoding="utf-8"))
    return (data.get("rubric_version") == rubric_version(rubric_path)
            and data.get("total", 0) >= 20 and data.get("agreed", 0) >= 18)
