"""Read captured failures, and promote reviewed ones into the regression suite.

A capture is evidence, not a test. It says an answer was rejected and why; it
does not say what the right answer was, and nothing here guesses. Promotion
writes the conversation into `evals/_captured.yaml` as an `expected_fail` with
the failing check recorded as its `gap:` — so the case starts out honest about
being a known defect, and a human decides later whether it is fixed.

**Review stays human on purpose.** A rejected answer can be rejected for a bug
in the checker as easily as for a bug in the agent, and a script that promoted
captures automatically would turn the first kind into a permanent assertion
that the bug is correct behaviour.

    uv run python scripts/promote_captures.py                 # list
    uv run python scripts/promote_captures.py --show <file>   # read one
    uv run python scripts/promote_captures.py --promote <file>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pension_chat.config import get_settings  # noqa: E402

SUITE = ROOT / "evals" / "_captured.yaml"


def _captures() -> list[Path]:
    directory = Path(get_settings().capture_dir)
    if not directory.is_absolute():
        directory = ROOT / directory
    return sorted(directory.glob("*.yaml")) if directory.exists() else []


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _listing() -> int:
    captures = _captures()
    if not captures:
        print("No captures. Nothing has been rejected since the directory was last cleared.")
        return 0
    print(f"{len(captures)} capture(s):\n")
    for path in captures:
        doc = _load(path)
        codes = ", ".join(f["code"] for f in doc.get("failed") or []) or "?"
        question = next(
            (t["q"] for t in reversed(doc.get("turns") or []) if "q" in t), ""
        )
        print(f"  {path.name}")
        print(f"      {codes}  ·  {len(doc.get('turns') or [])} turn(s)")
        print(f"      {question[:88]}")
        if offenders := doc.get("offenders"):
            print(f"      offending: {', '.join(map(str, offenders))}")
        print()
    print("Read one with --show, promote with --promote.")
    return 0


def _show(name: str) -> int:
    path = _resolve(name)
    if path is None:
        return 1
    print(path.read_text(encoding="utf-8"))
    return 0


def _resolve(name: str) -> Path | None:
    for path in _captures():
        if path.name == name or path.stem == name:
            return path
    print(f"No capture called {name!r}. Run without arguments to list them.")
    return None


def _promote(name: str) -> int:
    path = _resolve(name)
    if path is None:
        return 1
    doc = _load(path)
    codes = ", ".join(f["code"] for f in doc.get("failed") or []) or "unknown check"

    suite = (
        yaml.safe_load(SUITE.read_text(encoding="utf-8"))
        if SUITE.exists()
        else {"category": "captured", "cases": []}
    )
    case_id = f"C{len(suite.get('cases') or []) + 1:02d}"
    if any(c.get("source_capture") == path.name for c in suite.get("cases") or []):
        print(f"{path.name} is already in {SUITE.name}.")
        return 0

    suite.setdefault("cases", []).append(
        {
            "id": case_id,
            "name": f"Captured: {codes}",
            "status": "expected_fail",
            "source_capture": path.name,
            "gap": (
                f"Captured from a live turn on {doc.get('captured_at', 'an unknown date')}. "
                f"Verification rejected the answer: {codes}"
                + (
                    f" (offending: {', '.join(map(str, doc.get('offenders') or []))})"
                    if doc.get("offenders")
                    else ""
                )
                + ". The member saw the fallback. Promoted unreviewed — decide what "
                "the right answer is before making this `active`."
            ),
            "turns": [
                {"q": turn["q"]} for turn in doc.get("turns") or [] if "q" in turn
            ],
        }
    )
    SUITE.write_text(
        yaml.safe_dump(suite, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    print(f"Promoted {path.name} as {case_id} in {SUITE.relative_to(ROOT)}.")
    print("It has no expectations yet: a capture says what went wrong, not what")
    print("should have happened. Write those, then set status: active.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--show", metavar="FILE", help="print one capture")
    parser.add_argument("--promote", metavar="FILE", help="append one to the suite")
    args = parser.parse_args()

    if args.show:
        return _show(args.show)
    if args.promote:
        return _promote(args.promote)
    return _listing()


if __name__ == "__main__":
    raise SystemExit(main())
