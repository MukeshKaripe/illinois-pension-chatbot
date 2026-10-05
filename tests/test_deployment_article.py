"""An assistant deployed for one fund treats a question with no record as that fund's.

Scope decision D4 (2026-09-29): until the client says otherwise, the assistant
serves MEABF, so every employee is Article 8. With no record and nothing said,
coverage stayed "unresolved": the search ran unfiltered, sections scoped to
other funds were not withheld (§1-161 reached an anonymous Tier 2 question), and
a described employee's calculation answered "I couldn't establish which Pension
Code rule governs". `deployment_article` (DEPLOYMENT_ARTICLE) closes that gap and
nothing else: a record, a confirmation or a supposition still decides first.
"""
from __future__ import annotations

from types import SimpleNamespace

from pension_chat.agent import coverage as coverage_mod
from pension_chat.agent import nodes


def test_with_no_record_the_deployment_article_governs(monkeypatch) -> None:
    monkeypatch.setattr(nodes, "get_settings", lambda: SimpleNamespace(deployment_article=8))
    cov = nodes._record_coverage(None, 0)
    assert cov["article"] == "8" and cov["basis"] == coverage_mod.BASIS_DEPLOYMENT
    assert cov["record_article"] is None


def test_a_record_still_decides(monkeypatch) -> None:
    monkeypatch.setattr(nodes, "get_settings", lambda: SimpleNamespace(deployment_article=8))
    cov = nodes._record_coverage("11", 0)
    assert cov["article"] == "11" and cov["basis"] == coverage_mod.BASIS_RECORD


def test_without_a_deployment_article_nothing_changes(monkeypatch) -> None:
    monkeypatch.setattr(nodes, "get_settings", lambda: SimpleNamespace(deployment_article=None))
    assert nodes._record_coverage(None, 0)["basis"] == coverage_mod.BASIS_UNRESOLVED


def test_the_deployment_establishes_coverage_for_the_guardrail() -> None:
    # An answer about an MEABF employee may say they are under Article 8: the
    # assistant serves no other fund.
    assert coverage_mod.BASIS_DEPLOYMENT in coverage_mod.ESTABLISHED_BASES


def test_the_setting_exists_and_is_off_by_default() -> None:
    from pension_chat.config import Settings

    assert Settings.model_fields["deployment_article"].default is None
