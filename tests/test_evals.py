"""Every eval case must be able to fail.

`check()` reads expectations with `expect.get(name)`, so a mistyped key returns
None and the case passes for the rest of its life. That is worse than no case:
it reports a behaviour as covered when nothing is looking at it -- and suites
written without being run against the live index are exactly when that happens.

A bug class rather than a behaviour, in the pattern `test_coverage.py`
established: parse the harness for what it implements, and hold the suites to
it.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

EVALS = Path("evals")
HARNESS = Path("scripts/eval_agent.py")


def _implemented() -> set[str]:
    source = HARNESS.read_text(encoding="utf-8")
    keys = set(re.findall(r'expect\.get\(\s*"([a-z_]+)"', source))
    keys |= set(re.findall(r'expect\[\s*"([a-z_]+)"\s*\]', source))
    return keys


def _cases():
    for path in sorted(EVALS.glob("*.yaml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for case in document.get("cases") or []:
            yield path.name, document, case


def test_the_harness_implements_something():
    """Guards the guard: if the pattern stops matching the source, every test
    below passes vacuously."""
    assert len(_implemented()) > 20, "the pattern has drifted from the source"


def test_every_expectation_is_one_the_harness_checks():
    implemented = _implemented()
    unknown: dict[str, list[str]] = {}
    for filename, _document, case in _cases():
        for turn in case.get("turns") or []:
            for key in turn.get("expect") or {}:
                if key not in implemented:
                    unknown.setdefault(key, []).append(f"{filename}:{case['id']}")
    assert not unknown, f"expectations nothing checks: {unknown}"


def test_every_case_can_be_identified_in_a_failure_report():
    for filename, _document, case in _cases():
        assert case.get("id"), f"{filename}: a case with no id"
        assert case.get("name"), f"{filename}:{case['id']} has no name"


def test_every_turn_asks_something():
    for filename, _document, case in _cases():
        turns = case.get("turns") or []
        assert turns, f"{filename}:{case['id']} has no turns"
        for index, turn in enumerate(turns):
            assert str(turn.get("q") or "").strip(), (
                f"{filename}:{case['id']} turn {index} has no question"
            )


def test_no_case_id_is_used_twice():
    seen: dict[str, str] = {}
    for filename, _document, case in _cases():
        case_id = str(case["id"])
        assert case_id not in seen, f"{case_id} in both {filename} and {seen[case_id]}"
        seen[case_id] = filename


def test_every_file_declares_its_class():
    """Latency is reported per class, so a file without one lands every turn it
    holds in "uncategorised" and the distribution says nothing."""
    for filename, document, _case in _cases():
        assert document.get("category"), f"{filename} declares no category"


def test_every_running_case_asserts_something():
    """A case asserting nothing runs the agent and looks at nothing. It costs a
    model call every run and reports as a pass, so it makes the suite look
    broader than it is.

    Per case, not per turn: a multi-turn conversation legitimately has setup
    turns that only establish context -- G30's ninth turn is there so the tenth
    has something to refer back to. What is not legitimate is a whole case with
    nothing looking at it.

    `status: blocked` is exempt. Those are parked deliberately and carry a
    `blocked_by` reason, and the runner skips them.
    """
    silent = [
        f"{filename}:{case['id']} ({case.get('name')})"
        for filename, _document, case in _cases()
        if case.get("status") != "blocked"
        and not any((turn.get("expect") or {}) for turn in case.get("turns") or [])
    ]
    assert not silent, f"cases that assert nothing: {silent}"


def test_a_parked_case_says_why_it_is_parked():
    """Otherwise `status: blocked` becomes the way to keep a case that does not
    work, rather than a record of what is missing."""
    for filename, _document, case in _cases():
        if case.get("status") == "blocked":
            assert case.get("blocked_by"), (
                f"{filename}:{case['id']} is blocked with no reason recorded"
            )


#: Keys a case may carry. `source` is added by the loader, not written in YAML.
CASE_KEYS = {
    "id",
    "name",
    "category",
    "critical",
    "status",
    "blocked_by",
    # The counterpart of `blocked_by`, for `status: expected_fail`: what the
    # system is known not to do yet. Read by people, not by the loader, which
    # is exactly why it has to be listed -- otherwise the rule below would
    # delete four pieces of recorded reasoning.
    "gap",
    "employee",
    "turns",
}


def test_documentation_keys_are_only_on_the_cases_they_document():
    """`gap` and `blocked_by` are prose for a reader. A case carrying one while
    running normally is a case whose note nobody will ever see."""
    for filename, _document, case in _cases():
        if case.get("gap"):
            assert case.get("status") == "expected_fail", (
                f"{filename}:{case['id']} records a gap but is not expected_fail"
            )
        if case.get("blocked_by"):
            assert case.get("status") == "blocked", (
                f"{filename}:{case['id']} records a blocker but is not blocked"
            )


def test_a_case_carries_no_key_the_loader_ignores():
    """The other half of the same silent failure.

    `anon: true` looks like it makes a case run unauthenticated. The loader
    reads `employee: anon` and ignores anything else, so the case would have run
    as a member with a full record while asserting it had none -- and passed or
    failed for a reason nobody wrote down.
    """
    stray: dict[str, list[str]] = {}
    for filename, _document, case in _cases():
        for key in case:
            if key not in CASE_KEYS:
                stray.setdefault(key, []).append(f"{filename}:{case['id']}")
    assert not stray, f"case keys the loader ignores: {stray}"


def test_a_turn_carries_no_key_the_runner_ignores():
    for filename, _document, case in _cases():
        for index, turn in enumerate(case.get("turns") or []):
            stray = set(turn) - {"q", "expect"}
            assert not stray, (
                f"{filename}:{case['id']} turn {index} carries {sorted(stray)}"
            )
