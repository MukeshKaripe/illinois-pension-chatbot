"""Regression harness for the agent.

Cases live in ``evals/*.yaml``, not in this file. A case is a conversation: one
thread, one or more turns, each turn carrying its own expectations.

That shape matters more than it sounds. The interesting failures in this system
have not been wrong answers to single questions. They have been a correction
that did not propagate, a scenario that leaked into an unrelated follow-up, a
provision chosen on turn two and quietly forgotten by turn five, and a member
asked for their date of birth on the turn after it was used. None of those are
visible one question at a time.

Usage::

    uv run python scripts/eval_agent.py                    # everything runnable
    uv run python scripts/eval_agent.py 6 G14              # selected cases
    uv run python scripts/eval_agent.py --category state   # one category
    uv run python scripts/eval_agent.py --anon             # unauthenticated
    uv run python scripts/eval_agent.py --json out.json    # machine-readable
    uv run python scripts/eval_agent.py --include-blocked  # run the skipped ones

**Read a single run with that in mind.** Four consecutive runs of the same
commit scored 53, 49, 52 and 53 out of 54. The model is not deterministic, and
across ~100 turns a handful of them sit close enough to a threshold — a figure
the answer states in prose rather than copying, a citation the ranker surfaces
on one run and not the next — that they land differently each time. A drop of a
case or two between runs is variance; a category falling over, a critical case
failing, or the same case failing twice, is a finding. Every failure prints its
offending values, so telling the two apart means reading the line rather than
the score.

Two case-level flags carry weight. ``critical: true`` fails the run on its own,
however well everything else scored: an overall percentage that hides an Article
12 provision being applied to an Article 11 member is worse than no score at
all. ``status: expected_fail`` records something the system is known not to
handle yet, with the reason; it does not count against the run, but a case that
starts passing is reported, because that is worth knowing too.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
import uuid
from collections import defaultdict
from pathlib import Path

import yaml

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("LOG_LEVEL", "WARNING")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pension_chat import personas  # noqa: E402
from pension_chat.agent.graph import run_agent_stream  # noqa: E402
from pension_chat.agent.guardrails import _CONDITIONAL_MARKER  # noqa: E402
from pension_chat.agent.llm import close_clients  # noqa: E402
from pension_chat.calculation.applicability import normalize_fact_name  # noqa: E402

EVALS = Path(__file__).resolve().parents[1] / "evals"

# The default member every case runs as unless it names another. Taken from
# `personas.py` rather than pasted, because it used to be pasted: the column
# defaulted to gen_random_uuid(), so a freshly seeded database produced an id
# this constant did not match, and every case then ran anonymously -- passing or
# failing on the wrong footing, without anything reporting that the member was
# missing.
TEST_EMPLOYEE = str(personas.ARTICLE_11_STANDARD.id)


# ── Loading ───────────────────────────────────────────────────────


def load_cases() -> list[dict]:
    """Every case from every file in evals/, in filename then file order."""
    cases: list[dict] = []
    seen: dict[str, str] = {}
    for path in sorted(EVALS.glob("*.yaml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for case in document.get("cases", []):
            case_id = str(case["id"])
            if case_id in seen:
                raise SystemExit(
                    f"duplicate case id {case_id!r} in {path.name} and {seen[case_id]}"
                )
            seen[case_id] = path.name
            case["id"] = case_id
            case.setdefault("category", document.get("category", "uncategorised"))
            case.setdefault("critical", False)
            case.setdefault("status", "active")
            case.setdefault("employee", "default")
            case["source"] = path.name
            cases.append(case)
    return cases


# ── Running ───────────────────────────────────────────────────────


async def run_turn(question: str, thread: str, employee_id: str | None) -> dict:
    started = time.perf_counter()
    ttft = None
    parts: list[str] = []
    replaced = None
    state: dict = {}
    error = None

    async for event in run_agent_stream(question, thread, employee_id, []):
        kind = event["type"]
        if kind == "token":
            if ttft is None:
                ttft = time.perf_counter() - started
            parts.append(event["text"])
        elif kind == "replace":
            replaced = event["text"]
        elif kind == "done":
            state = event["state"]
        elif kind == "error":
            error = event["message"]

    return {
        "question": question,
        "answer": replaced or "".join(parts),
        "error": error,
        "ttft": ttft,
        "total": time.perf_counter() - started,
        "guardrail_passed": state.get("guardrail_passed", True),
        "guardrail_report": state.get("guardrail_report") or {},
        "requires_calculation": state.get("requires_calculation"),
        "sections": [s["section"] for s in state.get("retrieved_sections") or []],
        "articles": sorted({s.get("article") for s in state.get("retrieved_sections") or []}),
        "recipe_provenance": state.get("recipe_provenance") or [],
        "derivation_failures": state.get("derivation_failures") or [],
        "validation_errors": state.get("validation_errors") or [],
        "missing_inputs": [m["name"] for m in state.get("missing_inputs") or []],
        "member_facts": state.get("member_facts") or {},
        "coverage": state.get("coverage") or {},
        "hypothetical_frame": state.get("hypothetical_frame") or {},
        "self_reference": state.get("self_reference") or {},
        "clarification": state.get("clarification") or {},
        "clarifications_asked": state.get("clarifications_asked") or 0,
        "candidate_articles": state.get("candidate_articles") or [],
        "can_execute": state.get("can_execute"),
        "execution_blocked": state.get("execution_blocked"),
        "calculation": state.get("calculation_result") or {},
        "selected_provision": state.get("selected_provision") or {},
        "scenario_set": state.get("scenario_set") or {},
        "tokens": state.get("token_usage") or {},
        "timings": state.get("timings") or {},
        "in_scope": state.get("in_scope"),
        "stale_figures": state.get("stale_figures") or [],
        "answer_rung": state.get("answer_rung"),
    }


def resolve_employee(employee: str, default_employee: str | None) -> str | None:
    """Turn a case's `employee:` into an id.

    A case may name a persona key (`article-12-standard`), which is what new
    cases should do — a failure report then says which member it ran as instead
    of a UUID nobody can place. Raw ids still resolve, since older cases carry
    them, and `default` is whichever persona the run was started with.
    """
    if employee == "default":
        return default_employee
    persona = personas.by_key(employee)
    return str(persona.id) if persona else employee


async def run_case(case: dict, default_employee: str | None) -> list[dict]:
    """Run every turn of one case, on one thread.

    The thread is what makes a multi-turn case a conversation rather than a
    list of questions: the graph is checkpointed, so turn two resumes from what
    turn one established instead of being handed a transcript to re-read.
    """
    employee = case.get("employee", "default")
    employee_id = None if employee == "anon" else resolve_employee(employee, default_employee)
    thread = str(uuid.uuid4())

    turns: list[dict] = []
    for index, turn in enumerate(case["turns"]):
        result = await run_turn(turn["q"], thread, employee_id)
        result["expect"] = turn.get("expect") or {}
        result["index"] = index
        turns.append(result)
    return turns


# ── Checking ──────────────────────────────────────────────────────

_NEGATION = re.compile(
    r"\b(not|no|never|isn't|does not|doesn't|cannot|can't|rather than|instead of|"
    r"unsupported|incorrect|does not govern|without)\b",
    re.IGNORECASE,
)
# Deliberately money only. "Do not calculate yet" forbids quoting the member a
# benefit amount; it does not forbid saying the accrual rate is 2.40% a year or
# that the reduction is 0.25% a month, which is the rule, not the result.
_MONEY = re.compile(r"\$\s?[\d,]+(?:\.\d+)?")
_ASKING = re.compile(
    r"\b(i (?:would |will )?need|please (?:tell|give|provide|send)|could you (?:tell|give)"
    r"|tell me|let me know|i don't have|i do not have)\b",
    re.IGNORECASE,
)
# A fact the record already carries, and the words an answer uses to ask for it.
_KNOWN_FACT_PHRASES = {
    "birth_date": ("date of birth", "birth date"),
    "service_years": ("years of service", "your service", "service credit"),
    "final_average_salary": ("salary history", "your salary"),
}


def _asserted(answer: str, figure: str) -> bool:
    """True if ``figure`` is stated as a result rather than being rejected.

    "Your pension is $36,000" is a failure; "the Code does not provide $36,000"
    is the behaviour the adversarial cases are checking for. The distinction is
    made per sentence, which is where the negation lives.
    """
    for sentence in re.split(r"(?<=[.!?])\s+|\n", answer or ""):
        if figure in sentence and not _NEGATION.search(sentence):
            return True
    return False


def _inputs_used(result: dict) -> dict:
    """Inputs a turn calculated on, under canonical names.

    A recipe's input names are chosen by whichever derivation produced it, and
    they are not stable: clearing the registry and re-deriving the same
    provision renamed `creditable_service_years` to `service_years`, and every
    expectation written against the old name silently started reporting None.
    An expectation about which numbers were used should not be a bet on the
    vocabulary the model happened to pick, so they go through the same alias
    table the applicability gate uses.
    """
    used: dict = {}
    for entry in (result["calculation"] or {}).values():
        for name, value in ((entry.get("base") or {}).get("inputs_used") or {}).items():
            used[normalize_fact_name(name)] = value
    return used


def fund_figure_problems(result: dict, article: str) -> list[str]:
    """Spec §9's smoke rule for a fund: a figure from its own law, or an honest gap.

    The failure this exists to catch is the one the spec's risk list names: a
    fund whose formula rests on a different salary basis (police, fire) getting
    a figure from another Article's recipe. A missing input or a validator
    rejection is an honest answer; a borrowed formula is a wrong number with a
    real citation on it.
    """
    ran = [
        entry
        for entry in (result["calculation"] or {}).values()
        if (entry.get("base") or {}).get("ok")
    ]
    if ran:
        foreign = sorted(
            {
                str(entry.get("section"))
                for entry in ran
                if not str(entry.get("section") or "").startswith(f"{article}-")
            }
        )
        return [f"figure computed under {foreign}, not Article {article}"] if foreign else []
    if result["missing_inputs"] or result["execution_blocked"]:
        return []
    return ["no figure, and no missing input or blocked recipe to say why"]


def check(result: dict, earlier: list[dict]) -> list[str]:
    """Deterministic pass/fail signals. Judgement calls stay with the reader."""
    problems: list[str] = []
    expect = result["expect"]
    answer = result["answer"] or ""

    if result["error"]:
        return [f"ERROR: {result['error']}"]
    if not answer.strip():
        return ["empty answer"]

    want_scope = expect.get("in_scope")
    if want_scope is not None and result["in_scope"] != want_scope:
        problems.append(
            f"scope was {result['in_scope']}, expected {want_scope}"
        )

    if expect.get("recompute") and not result["stale_figures"]:
        problems.append("did not treat this as a correction to an earlier figure")

    if expect.get("rung") and result["answer_rung"] != expect["rung"]:
        problems.append(
            f"answered at rung {result['answer_rung']}, expected {expect['rung']}"
        )

    for section in expect.get("cites", []):
        # "11-215|11-216" passes on either. Some questions have more than one
        # right citation -- creditable service is defined across the basis of
        # service credit and its computation -- and demanding a particular one
        # tests the answer's phrasing rather than its law.
        if not any(option in answer for option in str(section).split("|")):
            problems.append(f"did not cite {section}")

    for phrase in expect.get("forbids", []):
        if phrase.lower() in answer.lower():
            problems.append(f"said {phrase!r}")

    # At least one, not all: an answer may disclose an assumption in any of
    # several natural phrasings, and demanding a particular one tests the
    # wording rather than the behaviour.
    if wanted := expect.get("mentions"):
        lowered = answer.lower()
        if not any(str(phrase).lower() in lowered for phrase in wanted):
            problems.append(f"said none of {list(wanted)}")

    # The runtime's own definition, not a second one. A benchmark that decides
    # separately what counts as conditional will eventually disagree with the
    # guardrail, and then one of them is wrong with no way to tell which.
    if expect.get("conditional") and not _CONDITIONAL_MARKER.search(answer):
        problems.append("answered a supposition in the plain indicative")

    if expect.get("article_only"):
        stray = [a for a in result["articles"] if a and a != expect["article_only"]]
        if stray:
            problems.append(f"retrieved other articles: {stray}")

    if expect.get("no_calculation") and result["requires_calculation"]:
        problems.append("planned a calculation for a lookup question")

    if expect.get("calculation"):
        ran = [
            formula_type
            for formula_type, entry in (result["calculation"] or {}).items()
            if (entry.get("base") or {}).get("ok")
        ]
        if not ran:
            detail = ""
            if result["execution_blocked"]:
                detail += f" (blocked: {result['execution_blocked']})"
            if result["missing_inputs"]:
                detail += f" (missing: {result['missing_inputs']})"
            problems.append("no calculation ran" + detail)

    if expect.get("provision"):
        chosen = {p.get("section") for p in result["recipe_provenance"]}
        if expect["provision"] not in chosen:
            problems.append(f"resolved {sorted(chosen)} rather than {expect['provision']}")

    if expect.get("scenarios") and not any(
        entry.get("scenarios") for entry in (result["calculation"] or {}).values()
    ):
        problems.append("no scenario comparison was produced")

    if expect.get("reuse") and not any(p.get("reused") for p in result["recipe_provenance"]):
        problems.append("derived a new recipe instead of reusing one")

    if expect.get("stated_input"):
        used = [str(v) for v in _inputs_used(result).values()]
        if expect["stated_input"] not in used:
            problems.append(
                f"calculated on {used} rather than the stated {expect['stated_input']}"
            )

    for name, value in (expect.get("uses_input") or {}).items():
        actual = _inputs_used(result).get(normalize_fact_name(name))
        if actual is None or str(actual) != str(value):
            problems.append(f"{name} was {actual!r}, expected {value!r}")

    # Only the named variable may move: the whole point of a one-variable
    # scenario is that everything else is held still.
    for raw in expect.get("unchanged", []):
        name = normalize_fact_name(raw)
        now = _inputs_used(result).get(name)
        before = next(
            (_inputs_used(turn)[name] for turn in reversed(earlier) if name in _inputs_used(turn)),
            None,
        )
        if before is None:
            problems.append(f"no earlier turn used {name}, so nothing was held still")
        elif str(now) != str(before):
            problems.append(f"{name} changed from {before!r} to {now!r} unasked")

    if expect.get("no_figures") and (found := _MONEY.findall(answer)):
        problems.append(f"quoted a benefit amount when asked not to: {found[:4]}")

    if expect.get("asks_for_nothing_known"):
        known = result["member_facts"] or {}
        for sentence in re.split(r"(?<=[.!?])\s+|\n", answer):
            if not _ASKING.search(sentence):
                continue
            for fact, phrases in _KNOWN_FACT_PHRASES.items():
                if known.get(fact) is None:
                    continue
                if any(phrase in sentence.lower() for phrase in phrases):
                    problems.append(f"asked for {fact}, which the record already has")

    if expect.get("rejects_asserted_formula"):
        for figure in ("36,000", "36000", "500,000", "500000"):
            if _asserted(answer, figure):
                problems.append(f"asserted the member's unsupported figure {figure}")
                break

    if expect.get("declines") and len(answer.split()) > 90:
        problems.append("declined at length; a refusal should be short")

    if expect.get("no_llm") and (result["tokens"].get("calls") or 0) > 0:
        problems.append("spent a model call on something it should refuse outright")

    if expect.get("no_llm") is False and (result["tokens"].get("calls") or 0) == 0:
        problems.append("refused without reading the question")

    if expect.get("admits_gap"):
        lowered = answer.lower()
        if not any(
            phrase in lowered
            for phrase in ("not", "article 8", "cannot", "unable", "don't have", "do not have")
        ):
            problems.append("did not say what it could not cover")

    if expect.get("refuses_as_ineligible"):
        computed = [
            formula_type
            for formula_type, entry in (result["calculation"] or {}).items()
            if (entry.get("base") or {}).get("ok")
        ]
        if computed:
            problems.append(f"computed {computed} for a member who does not qualify")
        if result["execution_blocked"] != "applicability_not_satisfied":
            problems.append(
                f"did not refuse on applicability grounds (blocked={result['execution_blocked']})"
            )

    # ── Coverage ──────────────────────────────────────────────────
    # Asserted separately from `no_figures` because the failure being caught is
    # not that a figure appeared — it is that the assistant answered at all,
    # when the one thing everything rests on was still open.
    if (asks := expect.get("asks")) is not None:
        asked = bool(result["clarification"])
        if asks and not asked:
            problems.append("answered where it should have asked")
        if not asks and asked:
            problems.append("asked where it should have answered")

    if (basis := expect.get("coverage_basis")) is not None:
        actual = (result["coverage"] or {}).get("basis")
        if actual != basis:
            problems.append(f"coverage basis {actual!r}, expected {basis!r}")

    if (article := expect.get("coverage_article")) is not None:
        actual = (result["coverage"] or {}).get("article")
        if str(actual) != str(article):
            problems.append(f"coverage article {actual!r}, expected {article!r}")

    if (fund := expect.get("fund_figure")) is not None:
        problems.extend(fund_figure_problems(result, str(fund)))

    if (frame := expect.get("hypothetical")) is not None:
        held = bool(result["hypothetical_frame"])
        if frame and not held:
            problems.append("dropped the supposition the member was still exploring")
        if not frame and held:
            problems.append("still inside a supposition the member left")

    if expect.get("act"):
        actual = (result["self_reference"] or {}).get("act")
        if actual != expect["act"]:
            problems.append(f"read the member's words as {actual!r}, expected {expect['act']!r}")

    if not result["guardrail_passed"]:
        report = result["guardrail_report"]
        # The offending values come with it. A bare "UNVERIFIED_FIGURE" says a
        # number could not be traced but not which one, and the answer it came
        # from has already been replaced by the fallback — so the one piece of
        # evidence needed to diagnose it is gone by the time anyone reads the
        # run.
        offenders = (
            (report.get("unverified_numbers") or [])
            + (report.get("unverified_citations") or [])
        )
        problems.append(
            "guardrail rejected: "
            + "; ".join(f["code"] for f in report.get("hard_failures", []))
            + (f"  {offenders}" if offenders else "")
        )

    return problems


# ── Reporting ─────────────────────────────────────────────────────


def _print_case(
    case: dict,
    turns: list[dict],
    verdict: str,
    quiet: bool,
    position: int,
    total: int,
    started: float,
) -> None:
    ttfts = [t["ttft"] for t in turns if t["ttft"]]
    tokens = sum(t["tokens"].get("total_tokens", 0) for t in turns)
    flag = "  CRITICAL" if case["critical"] else ""
    # Elapsed and projected, because the useful question halfway through a long
    # run is how much longer it will be.
    elapsed = time.perf_counter() - started
    projected = elapsed / position * total
    print(
        f"[{verdict:9}] {position:>2}/{total}  {case['id']:>4}  turns={len(turns)}  "
        f"ttft={(max(ttfts) if ttfts else 0):5.2f}s  tok={tokens:>6}  "
        f"[{elapsed / 60:4.1f}/{projected / 60:4.1f}m]  "
        f"{case['category']:<16} {case['name']}{flag}"
    )
    for turn in turns:
        if turn["problems"]:
            print(f"         turn {turn['index'] + 1}: {turn['question'][:88]}")
            for problem in turn["problems"]:
                print(f"           ! {problem}")
    if not quiet:
        for turn in turns:
            body = (turn["answer"] or "").strip()
            print(f"         > {turn['question'][:100]}")
            for line in body.splitlines()[:8]:
                print(f"           | {line[:140]}")
            if len(body.splitlines()) > 8:
                print("           | ...")
        print()


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("ids", nargs="*", help="case ids to run (default: all)")
    parser.add_argument("--category", help="run one category only")
    parser.add_argument("--anon", action="store_true", help="run unauthenticated")
    parser.add_argument("--json", dest="json_path", help="write the full report here")
    parser.add_argument("--quiet", action="store_true", help="omit answer text")
    parser.add_argument(
        "--mode",
        choices=("pipeline", "loop"),
        default="pipeline",
        help="which agent to run the suite against (loop lands in Phase 3)",
    )
    parser.add_argument(
        "--include-blocked",
        action="store_true",
        help="also run cases needing corpus that is not ingested yet",
    )
    args = parser.parse_args()

    if args.mode == "loop":
        print(
            "--mode loop is not available yet: the agent loop lands in Phase 3. "
            "Run without --mode to measure the pipeline."
        )
        return 2

    # Redirected to a file, Python buffers stdout and a twenty-minute run shows
    # nothing at all until it exits. Progress on a long run is most of what the
    # output is for, so each case is flushed as it finishes.
    sys.stdout.reconfigure(line_buffering=True)

    cases = load_cases()
    if args.ids:
        cases = [c for c in cases if c["id"] in args.ids]
    if args.category:
        cases = [c for c in cases if c["category"] == args.category]

    blocked = [c for c in cases if c["status"] == "blocked" and not args.include_blocked]
    runnable = [c for c in cases if c not in blocked]

    report: list[dict] = []
    failures: list[dict] = []
    criticals: list[dict] = []
    surprises: list[dict] = []
    by_category: dict[str, list[bool]] = defaultdict(list)

    started = time.perf_counter()
    for position, case in enumerate(runnable, start=1):
        turns = await run_case(case, None if args.anon else TEST_EMPLOYEE)
        for index, turn in enumerate(turns):
            turn["problems"] = check(turn, turns[:index])

        broke = any(turn["problems"] for turn in turns)

        if case["status"] == "expected_fail":
            verdict = "XPASS" if not broke else "known gap"
            if not broke:
                surprises.append(case)
        else:
            verdict = "FAIL" if broke else "PASS"
            by_category[case["category"]].append(not broke)
            if broke:
                failures.append(case)
                if case["critical"]:
                    criticals.append(case)

        _print_case(case, turns, verdict, args.quiet, position, len(runnable), started)
        report.append({**case, "verdict": verdict, "turns": turns})

    for case in blocked:
        print(
            f"[{'blocked':9}] {case['id']:>4}  {case['category']:<16} {case['name']}"
            f"  — {case.get('blocked_by', 'not yet supported')}"
        )

    # ── Summary ───────────────────────────────────────────────────
    scored = [ok for results in by_category.values() for ok in results]
    print("=" * 78)
    print(f"{sum(scored)}/{len(scored)} cases passed their checks")

    if by_category:
        print("\nby category")
        for category in sorted(by_category):
            results = by_category[category]
            print(
                f"  {category:<20} {sum(results):>2}/{len(results):<3}"
                f"{100 * sum(results) // len(results):>4}%"
            )

    known = [c for c in runnable if c["status"] == "expected_fail"]
    if known:
        print(f"\n{len(known)} case(s) recorded as known gaps, not counted:")
        for case in known:
            print(f"  {case['id']:>4} {case['name']} — {case.get('gap', 'no reason recorded')}")
    if surprises:
        print("\nnow passing, and no longer a known gap — update its status:")
        for case in surprises:
            print(f"  {case['id']:>4} {case['name']}")

    # An overall percentage must never bury one of these.
    if criticals:
        print("\n" + "!" * 78)
        print("CRITICAL FAILURE — this run does not pass, whatever the score says")
        for case in criticals:
            print(f"  {case['id']:>4} {case['name']}  ({case['source']})")
        print("!" * 78)

    turns_run = [t for case in report for t in case["turns"]]
    timed = sorted(t["ttft"] for t in turns_run if t["ttft"])
    if timed:
        print(
            f"\nTTFT  median={timed[len(timed) // 2]:.2f}s  min={timed[0]:.2f}s  "
            f"max={timed[-1]:.2f}s  under_2s={sum(1 for t in timed if t < 2.0)}/{len(timed)}"
        )
    total_tokens = sum(t["tokens"].get("total_tokens", 0) for t in turns_run)
    print(
        f"tokens total={total_tokens}  mean/turn={total_tokens // max(1, len(turns_run))}  "
        f"turns={len(turns_run)}"
    )

    if args.json_path:
        # A report that cannot say which agent produced it is not a baseline.
        # `report` is a list of cases, so the mode needs a wrapper; nothing
        # reads these files today, and a future reader takes ["cases"].
        Path(args.json_path).write_text(
            json.dumps({"mode": args.mode, "cases": report}, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"report written to {args.json_path}")

    await close_clients()
    return 1 if (failures or criticals) else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
