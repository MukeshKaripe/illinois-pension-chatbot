"""The eval command forces the no-write flags; a shell that exports them on cannot override that."""
from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "run_evals.py"


def _script():
    spec = importlib.util.spec_from_file_location("run_evals", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FLAGS = ("CAPTURE_FAILURES", "ANSWER_MEMORY_ENABLED", "PERSIST_DERIVED_RECIPES", "REGISTRY_WRITES_ENABLED")


def test_by_default_formulas_are_saved_and_nothing_else_is(monkeypatch) -> None:
    # Derived formulas are the knowledge the system is built to keep and reuse
    # (product owner, 2026-09-30); conversations, answer memory and failure
    # captures are not written by a run -- whatever the shell exports.
    import os
    for flag in FLAGS:
        monkeypatch.setenv(flag, "true")
    _script()._force_no_writes(save_formulas=True)
    assert os.environ["PERSIST_DERIVED_RECIPES"] == os.environ["REGISTRY_WRITES_ENABLED"] == "true"
    assert os.environ["CAPTURE_FAILURES"] == os.environ["ANSWER_MEMORY_ENABLED"] == "false"


def test_no_save_formulas_writes_nothing(monkeypatch) -> None:
    import os
    for flag in FLAGS:
        monkeypatch.setenv(flag, "true")
    _script()._force_no_writes(save_formulas=False)
    assert {flag: os.environ[flag] for flag in FLAGS} == dict.fromkeys(FLAGS, "false")
