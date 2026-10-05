"""Run the eval suite. See docs/superpowers/specs/2026-09-30-eval-suite-design.md.

    uv run python scripts/run_evals.py                      # smoke tier
    uv run python scripts/run_evals.py --tier full
    uv run python scripts/run_evals.py --changed            # what this branch touched, plus criticals
    uv run python scripts/run_evals.py --family S4 --family S4b
    uv run python scripts/run_evals.py --id S1-003
    uv run python scripts/run_evals.py --tier full --accept # make this run the baseline
    uv run python scripts/run_evals.py --calibrate --from evals/runs/<ts>
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import date
from pathlib import Path

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("LOG_LEVEL", "WARNING")


def _force_no_writes(save_formulas: bool) -> None:
    """What a run may write: derived formulas, and nothing else.

    Derived formulas are the knowledge the system exists to keep and reuse
    (product owner, 2026-09-30), and they are guarded before they are saved --
    every constant found in its cited text, no recipe resting on a conditional
    section. Conversations stay in memory; answer memory and failure capture
    stay off. Assigned, not defaulted, so a shell exporting them cannot change
    what a run writes. `--no-save-formulas` makes a run write nothing at all.
    """
    formulas = "true" if save_formulas else "false"
    os.environ["CAPTURE_FAILURES"] = "false"
    os.environ["ANSWER_MEMORY_ENABLED"] = "false"
    for key in ("PERSIST_DERIVED_RECIPES", "REGISTRY_WRITES_ENABLED"):
        os.environ[key] = formulas


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
EVALS = ROOT / "evals"


def _args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--tier", choices=["smoke", "full"], default=None,
                   help="default: smoke, unless --id/--family/--changed narrow the run (then every tier)")
    p.add_argument("--family", action="append")
    p.add_argument("--id", action="append")
    p.add_argument("--changed", action="store_true")
    p.add_argument("--base", default="main")
    p.add_argument("--concurrency", type=int, default=2,
                   help="default: 2 -- at 8 the model deployment returns 429s mid-run")
    p.add_argument("--no-judge", action="store_true")
    p.add_argument("--judge-all", action="store_true",
                   help="judge every turn, even ones the code checks failed (for calibration)")
    p.add_argument("--latency-gate", action="store_true")
    p.add_argument("--no-save-formulas", action="store_true",
                   help="write nothing at all; by default derived formulas are saved for reuse")
    p.add_argument("--accept", action="store_true")
    p.add_argument("--calibrate", action="store_true")
    p.add_argument("--from", dest="from_run", type=Path)
    return p.parse_args()


async def _calibrate(run_dir: Path) -> int:
    from pension_chat.evals.judge import Verdict, calibration, rubric_version
    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    verdicts = {k: Verdict(**t["verdict"]) for k, t in report.get("verdicts", {}).items()}
    agreed, total = calibration(EVALS / "labels", verdicts)
    (EVALS / "calibration.json").write_text(json.dumps(
        {"rubric_version": rubric_version(EVALS / "rubric.yaml"), "agreed": agreed, "total": total}), encoding="utf-8")
    print(f"Judge agreed with {agreed}/{total} labels ({'gating' if total >= 20 and agreed >= 18 else 'not gating'}).")
    return 0


async def main() -> int:
    args = _args()
    _force_no_writes(save_formulas=not args.no_save_formulas)
    if args.calibrate:
        return await _calibrate(args.from_run)

    from dataclasses import asdict

    from pension_chat.agent.llm import close_clients
    from pension_chat.config import get_settings
    from pension_chat.evals.cases import load_cases
    from pension_chat.evals.fixtures import load_fixtures
    from pension_chat.evals.judge import is_calibrated
    from pension_chat.evals.report import diff, exit_code, render_markdown, summarise
    from pension_chat.evals.runner import RunOptions, run_suite
    from pension_chat.evals.select import changed_files, select
    from pension_chat.evals.timing import probe

    fixtures = load_fixtures(EVALS / "fixtures" / "employees", date.today())
    cases = select(load_cases(EVALS / "cases", set(fixtures)), tier=args.tier, families=args.family,
                   ids=args.id, changed=changed_files(args.base) if args.changed else None)
    print(f"{len(cases)} conversations, {sum(len(c.turns) for c in cases)} turns", flush=True)
    measured = await probe(get_settings())
    calibrated = is_calibrated(EVALS, EVALS / "rubric.yaml")
    options = RunOptions(concurrency=args.concurrency, judge=not args.no_judge, judge_gates=calibrated,
                         judge_all=args.judge_all, progress=True,
                         latency_gate=args.latency_gate, rubric_path=EVALS / "rubric.yaml",
                         cache_dir=EVALS / ".judge_cache")
    started = time.perf_counter()
    try:
        results = await run_suite(cases, fixtures=fixtures, probe=measured, options=options)
    finally:
        await close_clients()

    report = summarise(results, judge_calibrated=calibrated, probe=measured)
    report["verdicts"] = {f"{r.case.id}#{t.turn_index}": asdict(t.verdict)
                          for r in results for t in r.turns if t.verdict}
    report["wall_seconds"] = round(time.perf_counter() - started, 1)
    baseline_path = EVALS / "baseline.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8")) if baseline_path.exists() else None
    changes = diff(report, baseline)

    run_dir = EVALS / "runs" / time.strftime("%Y%m%dT%H%M%S")
    (run_dir / "traces").mkdir(parents=True)
    for r in results:
        for t in r.turns:
            (run_dir / "traces" / f"{r.case.id}#{t.turn_index}.json").write_text(
                json.dumps(t.trace.to_dict(), indent=1), encoding="utf-8")
    (run_dir / "report.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    print(render_markdown(report, changes))
    print(f"\nWall time {report['wall_seconds']} s. Saved to {run_dir}")
    if args.accept:
        baseline_path.write_text(json.dumps({k: report[k] for k in ("turns", "latency", "families")},
                                            indent=1, default=str), encoding="utf-8")
        print("Accepted as the new baseline.")
    return exit_code(report, changes)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
