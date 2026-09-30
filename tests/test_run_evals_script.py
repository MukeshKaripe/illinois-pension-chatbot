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


def test_no_write_flags_are_forced_off_even_when_the_shell_says_on(monkeypatch) -> None:
    for flag in FLAGS:
        monkeypatch.setenv(flag, "true")
    _script()._force_no_writes(with_memory=False)
    import os
    assert {flag: os.environ[flag] for flag in FLAGS} == dict.fromkeys(FLAGS, "false")


def test_with_memory_opens_the_knowledge_path_but_never_capture(monkeypatch) -> None:
    import os
    _script()._force_no_writes(with_memory=True)
    assert os.environ["CAPTURE_FAILURES"] == "false"
    assert os.environ["ANSWER_MEMORY_ENABLED"] == os.environ["PERSIST_DERIVED_RECIPES"] == "true"
    assert os.environ["REGISTRY_WRITES_ENABLED"] == "true"
