"""The ingestion CLI must be told which corpus to ingest.

A bare run used to mean `ilga-all`: against the client's index that silently
widens it to all 24 Articles and replaces the client's Article 8 with ILGA's —
and nothing refuses growth. So the corpus is a required choice.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ingest_articles.py"


def _load_cli():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("ingest_articles", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_bare_run_is_refused() -> None:
    with pytest.raises(SystemExit) as exc:
        _load_cli().build_parser().parse_args([])
    assert exc.value.code == 2


@pytest.mark.parametrize("corpus", ["meabf", "ilga-all"])
def test_each_corpus_can_be_named(corpus: str) -> None:
    assert _load_cli().build_parser().parse_args(["--corpus", corpus]).corpus == corpus
