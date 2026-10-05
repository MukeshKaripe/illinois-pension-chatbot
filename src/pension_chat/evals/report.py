"""Score a run and say what changed since the last accepted one.

The report opens with the diff against evals/baseline.json, because a run
repeated after every change is read for what moved, not re-read in full.
Then: the critical block (any failure fails the run), pass rates per family
with the first-class families first, latency measured and estimated per class
and per stage, verification, semantic-ranker fallbacks, and the judge's soft
scores.
"""

from __future__ import annotations

import statistics
from typing import Any

from pension_chat.evals.cases import FAMILIES, FIRST_CLASS
from pension_chat.evals.runner import ConversationResult

SCORED = ("pass", "fail", "error", "unjudged")


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))]


def _why_no_figure(trace: Any) -> str:
    """What stopped a calculation, in one line, from the trace."""
    parts = trace.derivation_failures[:1] + (
        [f"missing {', '.join(trace.missing_inputs)}"] if trace.missing_inputs else []
    ) + ([f"refused: {', '.join(sorted(set(trace.validation_codes)))}"] if trace.validation_codes else [])
    return "; ".join(parts)[:300]


def summarise(results: list[ConversationResult], *, judge_calibrated: bool,
              probe: dict[str, Any]) -> dict[str, Any]:
    families: dict[str, dict[str, Any]] = {}
    turns: dict[str, dict[str, Any]] = {}
    latency: dict[str, dict[str, list[float]]] = {}
    by_type: dict[str, dict[str, list[float]]] = {}
    prompt: dict[str, list[int]] = {}
    critical: list[str] = []
    soft: dict[str, list[int]] = {"voice": [], "hedging": [], "clarity": []}
    fallbacks = verified = replaced = total = 0
    formulas = {"derived": 0, "registry": 0}
    for conv in results:
        fam = families.setdefault(conv.case.family, {"passed": 0, "scored": 0,
                                                      "first_class": conv.case.family in FIRST_CLASS})
        for tr in conv.turns:
            key = f"{conv.case.id}#{tr.turn_index}"
            turns[key] = {"status": tr.status, "family": conv.case.family, "flaky": conv.flaky,
                          "failed_checks": [f"{c.name}: {c.detail}" for c in tr.checks if not c.passed],
                          "ttft": tr.trace.ttft, "estimate": tr.estimate,
                          "why_no_figure": _why_no_figure(tr.trace)}
            if tr.status in SCORED:
                fam["scored"] += 1
                fam["passed"] += tr.status == "pass"
                if conv.case.critical and tr.status != "pass":
                    critical.append(key)
            cls = conv.case.turns[tr.turn_index].checks.get("ttft_class")
            if cls and tr.trace.ttft is not None:
                slot = latency.setdefault(cls, {"measured": [], "estimated": []})
                slot["measured"].append(tr.trace.ttft)
                if tr.estimate is not None:
                    slot["estimated"].append(tr.estimate)
            fallbacks += sum(1 for n in tr.trace.notes if n.get("kind") == "semantic_fallback")
            for source in tr.trace.formula_sources.values():
                if source in formulas:
                    formulas[source] += 1
            total += 1
            verified += bool(tr.trace.guardrail_passed)
            replaced += bool(tr.trace.replacements)
            if tr.verdict:
                for k in soft:
                    soft[k].append(getattr(tr.verdict, k))
    # Latency per turn type reads the timing-only repeats too; pass/fail and
    # the per-class latency above read the judged run only.
    for conv in results:
        for tr in [*conv.turns, *(t for again in conv.repeats for t in again.turns)]:
            if tr.trace.turn_type and tr.trace.ttft is not None:
                tslot = by_type.setdefault(tr.trace.turn_type, {"measured": [], "estimated": []})
                tslot["measured"].append(tr.trace.ttft)
                if tr.estimate is not None:
                    tslot["estimated"].append(tr.estimate)
            for node, used in ((tr.trace.usage or {}).get("by_node") or {}).items():
                if used.get("prompt"):
                    prompt.setdefault(node, []).append(int(used["prompt"]))
    return {
        "families": {f: families[f] for f in FAMILIES if f in families},
        "critical_failures": critical,
        "turns": turns,
        "latency": {cls: {kind: {"median": _pct(v, 0.5), "p90": _pct(v, 0.9), "n": len(v)}
                          for kind, v in slot.items()} for cls, slot in latency.items()},
        "latency_by_turn_type": {t: {kind: {"p50": _pct(v, 0.5), "p95": _pct(v, 0.95), "n": len(v)}
                                     for kind, v in slot.items()} for t, slot in by_type.items()},
        "prompt_tokens": {node: int(statistics.median(v)) for node, v in prompt.items()},
        "soft_scores": {k: (statistics.mean(v) if v else None) for k, v in soft.items()},
        "verification": {"passed": verified, "replaced": replaced, "turns": total},
        "semantic_fallbacks": fallbacks,
        "formulas": formulas,
        "flaky": sorted({k.split("#")[0] for k, t in turns.items() if t["flaky"]}),
        "judge_calibrated": judge_calibrated,
        "probe": probe,
    }


def diff(report: dict[str, Any], baseline: dict[str, Any] | None) -> dict[str, Any]:
    if not baseline:
        return {"new_failures": [], "fixed": [], "latency_moves": {}, "type_moves": {}, "no_baseline": True}
    before, now = baseline.get("turns", {}), report["turns"]
    bad = ("fail", "error", "unjudged")
    new = [k for k, t in now.items() if t["status"] in bad and before.get(k, {}).get("status") == "pass"]
    fixed = [k for k, t in now.items() if t["status"] == "pass" and before.get(k, {}).get("status") in bad]
    moves = {}
    for cls, slot in report["latency"].items():
        old = baseline.get("latency", {}).get(cls, {}).get("measured", {}).get("median")
        new_median = slot.get("measured", {}).get("median")
        if old is not None and new_median is not None:
            moves[cls] = round(new_median - old, 2)
    type_moves = {}
    for t, tslot in report.get("latency_by_turn_type", {}).items():
        was = baseline.get("latency_by_turn_type", {}).get(t, {}).get("estimated", {})
        now_est = tslot.get("estimated", {})
        if was.get("p50") is not None and now_est.get("p50") is not None:
            type_moves[t] = {"p50": round(now_est["p50"] - was["p50"], 2),
                             "p95": round((now_est.get("p95") or 0) - (was.get("p95") or 0), 2)}
    return {"new_failures": new, "fixed": fixed, "latency_moves": moves, "type_moves": type_moves,
            "no_baseline": False}


#: Report keys a baseline keeps. Each is a mapping keyed by turn, family or
#: class, so a run over a few families updates those keys and keeps the rest.
BASELINE_KEYS = ("turns", "latency", "families", "latency_by_turn_type", "prompt_tokens")


def accepted(report: dict[str, Any], baseline: dict[str, Any] | None) -> dict[str, Any]:
    """The baseline after accepting ``report``: this run's entries replace the
    old ones they name; entries this run didn't touch are kept."""
    old = baseline or {}
    return {k: {**(old.get(k) or {}), **(report.get(k) or {})} for k in BASELINE_KEYS}


def exit_code(report: dict[str, Any], changes: dict[str, Any]) -> int:
    flaky = set(report.get("flaky") or [])
    real_new = [k for k in changes["new_failures"] if k.split("#")[0] not in flaky]
    return 1 if report["critical_failures"] or real_new else 0


def render_markdown(report: dict[str, Any], changes: dict[str, Any]) -> str:
    lines = ["## Changes since baseline"]
    if changes.get("no_baseline"):
        lines.append("No baseline yet — accept this run with `--accept` to make it one.")
    else:
        lines.append(f"New failures: {', '.join(changes['new_failures']) or 'none'}")
        lines.append(f"Fixed: {', '.join(changes['fixed']) or 'none'}")
        lines.append(f"Median TTFT moves (s): {changes['latency_moves'] or 'none'}")
    lines += ["", "## Critical", ", ".join(report["critical_failures"]) or "all critical cases passed", "",
              "## Families"]
    for fam, f in report["families"].items():
        mark = " (first-class)" if f["first_class"] else ""
        rate = f"{f['passed']}/{f['scored']}" if f["scored"] else "pending"
        lines.append(f"- {fam}{mark}: {rate}")
    lines += ["", "## Latency (time to first answer token; the estimate is an in-region *estimated* figure)"]
    for cls, slot in report["latency"].items():
        m, e = slot.get("measured", {}), slot.get("estimated", {})
        lines.append(f"- {cls}: measured median {m.get('median')} s, p90 {m.get('p90')} s; "
                     f"estimated median {e.get('median')} s, p90 {e.get('p90')} s")
    lines += ["", "## Latency by turn type (estimated in-region p50 / p95, s)"]
    for t, tslot in report.get("latency_by_turn_type", {}).items():
        e, m = tslot.get("estimated", {}), tslot.get("measured", {})
        lines.append(f"- {t}: estimated {e.get('p50')} / {e.get('p95')}; "
                     f"measured {m.get('p50')} / {m.get('p95')} (n={m.get('n')})")
    lines.append(f"Prompt tokens per call (median): {report.get('prompt_tokens') or {}}")
    if not changes.get("no_baseline"):
        lines.append(f"Turn-type moves vs baseline (s): {changes.get('type_moves') or 'none'}")
    v = report["verification"]
    lines += ["", f"Verification: {v['passed']}/{v['turns']} passed, {v['replaced']} replaced; "
                  f"semantic-ranker fallbacks: {report['semantic_fallbacks']}",
              f"Formulas: {report['formulas']['derived']} derived, {report['formulas']['registry']} reused",
              f"Judge soft scores: {report['soft_scores']}"]
    if not report["judge_calibrated"]:
        lines.append("Judge not calibrated — its verdicts are reported, not gating (see evals/labels/README.md).")
    lines += ["", "## Failures"]
    for key, t in report["turns"].items():
        if t["status"] in ("fail", "error", "unjudged"):
            why = f" -- no figure because: {t['why_no_figure']}" if t.get("why_no_figure") else ""
            lines.append(f"- {key} [{t['status']}]{' (flaky)' if t['flaky'] else ''}: "
                         f"{'; '.join(t['failed_checks'])}{why}")
    return "\n".join(lines)
