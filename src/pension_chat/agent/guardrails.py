"""Deterministic guardrails around the generated answer.

``guardrail_check`` in the first implementation was a stub that returned
``{"guardrail_passed": True}`` unconditionally, so nothing downstream of the
model was ever checked. Everything here is pure Python — a model asked to grade
its own output shares its own blind spots, and a second model call would put
another second on the critical path.

The checks split by when they can run:

**Before generation** (can block, costs nothing):
    * ``classify_scope`` — is this question about the Pension Code at all?
    * ``detect_instruction_override`` — is the user asking the agent to skip the
      statute and apply a formula they supplied? This does not block; it pins an
      explicit rebuttal instruction into the answer prompt, because refusing
      outright is a worse answer than explaining what the Code actually says.

**After generation** (verifies, annotates):
    * ``verify_citations`` — every section the answer cites must be a section
      that was retrieved. Catches citation of remembered law.
    * ``verify_numbers`` — every money and percentage figure in the answer must
      trace to a calculation result, retrieved statute text, a member fact, or
      the user's own message. This is what stops the model doing benefit
      arithmetic in prose and presenting it as a computed figure.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

logger = logging.getLogger("pension_chat.agent.guardrails")


# ── Scope ─────────────────────────────────────────────────────────

_PENSION_TERMS = {
    "pension", "annuity", "annuitant", "retire", "retirement", "benefit",
    "service credit", "creditable service", "salary", "contribution",
    "withdraw", "withdrawal", "survivor", "widow", "disability", "refund",
    "ilcs", "article", "section", "fund", "vest", "vesting", "beneficiary",
    "cola", "increase", "eligib", "employee", "member", "tier", "years of service",
}

# Asks that are clearly not this system's job. Kept narrow on purpose: a
# borderline question should reach retrieval and be answered or declined on the
# evidence, not turned away by a keyword.
_OUT_OF_SCOPE = re.compile(
    r"\b(write|generate|compose)\s+(me\s+)?(a\s+)?(poem|song|story|essay|joke|script)\b"
    r"|\b(recipe for|weather|stock price|medical advice|diagnose)\b"
    r"|\bwho (won|is the president)\b",
    re.IGNORECASE,
)


@dataclass
class ScopeVerdict:
    in_scope: bool
    reason: str = ""


def classify_scope(question: str) -> ScopeVerdict:
    """Cheap deterministic scope check run before any model call."""
    text = (question or "").strip()
    if not text:
        return ScopeVerdict(False, "The message was empty.")

    if _OUT_OF_SCOPE.search(text):
        return ScopeVerdict(
            False,
            "That is outside what this assistant covers — it answers questions "
            "about the Illinois Pension Code (40 ILCS 5/).",
        )

    lowered = text.lower()
    if any(term in lowered for term in _PENSION_TERMS):
        return ScopeVerdict(True)

    # Short, term-free messages ("hi", "thanks") are conversational, not
    # out-of-scope refusals. Anything longer gets the benefit of the doubt and
    # goes to retrieval, which will come back empty if it truly is unrelated.
    if len(lowered.split()) <= 3:
        return ScopeVerdict(False, "greeting")
    return ScopeVerdict(True)


# ── Adversarial instruction detection ─────────────────────────────

_OVERRIDE_PATTERNS = [
    (re.compile(r"\bwithout\s+(checking|consulting|looking\s+at|reading|verifying)\b", re.I),
     "asks the agent to skip consulting the Pension Code"),
    (re.compile(r"\b(just|simply)\s+(calculate|compute|use|apply|multiply)\b", re.I),
     "asks the agent to apply a supplied formula directly"),
    (re.compile(r"\b(ignore|skip|bypass|disregard|forget)\s+(the\s+)?"
                r"(rules?|statute|law|code|instructions?|guardrails?|validation)\b", re.I),
     "asks the agent to ignore its rules"),
    (re.compile(r"\beveryone\s+says\b|\bi\s+was\s+told\b|\bmy\s+(friend|coworker|colleague)\s+said\b", re.I),
     "supplies a formula on hearsay authority"),
    (re.compile(r"\b(trust\s+me|don'?t\s+bother|no\s+need\s+to)\b", re.I),
     "asks the agent to accept an assertion without evidence"),
    (re.compile(r"\byou\s+are\s+now\b|\bpretend\s+(you|to)\b|\bact\s+as\s+if\b", re.I),
     "attempts to reassign the agent's role"),
]

# "50% of salary", "2.4% per year", "$72,000 x 50%"
_ASSERTED_FORMULA = re.compile(
    r"\d+(?:\.\d+)?\s*%\s*(?:of|x|\*|times)?\s*(?:my\s+|the\s+)?"
    r"(salary|pay|compensation|final average salary|fas)\b",
    re.IGNORECASE,
)


@dataclass
class OverrideVerdict:
    detected: bool = False
    reasons: list[str] = field(default_factory=list)
    asserted_formula: str | None = None

    def to_dict(self) -> dict:
        return {
            "detected": self.detected,
            "reasons": self.reasons,
            "asserted_formula": self.asserted_formula,
        }


def detect_instruction_override(question: str) -> OverrideVerdict:
    """Detect a request to bypass the statute and apply a user-supplied formula.

    The correct response is not refusal. It is to answer from the Code and say
    plainly why the supplied figure does not govern — so this returns a verdict
    the answer prompt turns into an explicit instruction.
    """
    verdict = OverrideVerdict()
    for pattern, reason in _OVERRIDE_PATTERNS:
        if pattern.search(question or ""):
            verdict.detected = True
            verdict.reasons.append(reason)

    match = _ASSERTED_FORMULA.search(question or "")
    if match:
        verdict.asserted_formula = match.group(0).strip()
        if not verdict.detected:
            verdict.detected = True
            verdict.reasons.append("states a benefit formula not drawn from the Code")

    return verdict


# ── Post-generation verification ──────────────────────────────────

_CITATION = re.compile(r"40\s*ILCS\s*5/\s*(\d{1,2}-\d{1,3}(?:\.\d{1,2})?)", re.IGNORECASE)
_SECTION_MENTION = re.compile(
    r"\bSec(?:tion|\.)\s*(\d{1,2}-\d{1,3}(?:\.\d{1,2})?)", re.IGNORECASE
)
_MONEY = re.compile(r"\$\s*([\d,]+(?:\.\d{1,2})?)")
_PERCENT = re.compile(r"(\d{1,3}(?:\.\d{1,3})?)\s*%")


@dataclass
class GuardrailReport:
    """Outcome of verifying one generated answer."""

    passed: bool = True
    hard_failures: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    verified_citations: list[str] = field(default_factory=list)
    unverified_citations: list[str] = field(default_factory=list)
    unverified_numbers: list[str] = field(default_factory=list)

    def fail(self, code: str, message: str):
        self.passed = False
        self.hard_failures.append({"code": code, "message": message})

    def warn(self, code: str, message: str):
        self.warnings.append({"code": code, "message": message})

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "hard_failures": self.hard_failures,
            "warnings": self.warnings,
            "verified_citations": self.verified_citations,
            "unverified_citations": self.unverified_citations,
            "unverified_numbers": self.unverified_numbers,
        }


def _decimals_in(text: str) -> set[Decimal]:
    """Every number appearing in a block of text, as Decimals."""
    found: set[Decimal] = set()
    for raw in re.findall(r"\d[\d,]*(?:\.\d+)?", text or ""):
        try:
            found.add(Decimal(raw.replace(",", "")))
        except InvalidOperation:
            continue
    return found


def _collect_allowed_numbers(
    calculation_result: dict | None,
    sections_text: str,
    facts: dict[str, Any] | None,
    user_text: str,
) -> set[Decimal]:
    """Build the set of numeric values an answer is allowed to state.

    A figure is legitimate if it came out of the deterministic interpreter, was
    read off the statute, is a fact about the member, or was supplied by the
    user in their own message.
    """
    allowed: set[Decimal] = set()

    def add(value: Any):
        if value is None or isinstance(value, bool):
            return
        try:
            allowed.add(Decimal(str(value)))
        except (InvalidOperation, TypeError, ValueError):
            return

    def walk(node: Any):
        if isinstance(node, dict):
            for item in node.values():
                walk(item)
        elif isinstance(node, (list, tuple)):
            for item in node:
                walk(item)
        else:
            add(node)

    walk(calculation_result)
    walk(facts)
    allowed |= _decimals_in(sections_text)
    allowed |= _decimals_in(user_text)

    # Percentages are written both ways: a 0.024 parameter is stated as "2.4%",
    # and an 80% cap is stored as 0.8. Admit both readings of every value.
    for value in list(allowed):
        add(value * 100)
        add(value / 100)

    # Monthly figures are annual/12. The interpreter supplies the monthly amount
    # alongside the annual one, but tolerate the division so an answer that
    # restates it is not flagged.
    for value in list(allowed):
        if value > 100:
            add(value / 12)

    return allowed


def _matches_allowed(value: Decimal, allowed: set[Decimal], tolerance: Decimal) -> bool:
    """True if ``value`` equals an allowed number within rounding tolerance."""
    if value in allowed:
        return True
    for candidate in allowed:
        if candidate == 0:
            if abs(value) <= tolerance:
                return True
            continue
        if abs(value - candidate) <= max(tolerance, abs(candidate) * Decimal("0.005")):
            return True
    return False


def verify_answer(
    answer: str,
    *,
    retrieved_sections: Iterable[dict],
    calculation_result: dict | None = None,
    facts: dict[str, Any] | None = None,
    user_text: str = "",
    check_numbers: bool = True,
) -> GuardrailReport:
    """Verify a generated answer against the evidence it was given.

    Args:
        answer: The generated text.
        retrieved_sections: Section dicts as produced by ``RetrievedSection``.
        calculation_result: Interpreter output for this turn, if any.
        facts: Member facts that were in scope for the answer.
        user_text: The user's message, so figures they supplied are allowed.
        check_numbers: Disable for answers that legitimately contain no
            computed figures.

    Returns:
        A ``GuardrailReport``. ``passed`` is False only for hard failures —
        a citation to a section that was never retrieved, or a money figure
        with no traceable source.
    """
    report = GuardrailReport()
    sections = list(retrieved_sections or [])
    available = {str(s.get("section")) for s in sections if s.get("section")}
    sections_text = "\n".join(str(s.get("text") or "") for s in sections)

    if not (answer or "").strip():
        report.fail("EMPTY_ANSWER", "The model produced no answer text.")
        return report

    # 1. A bracketed citation must point at a section whose text was retrieved.
    #
    # A bare mention is held to a weaker standard, deliberately. Article 11 is
    # written in cross-references — 11-134 computes on service "as service is
    # defined or computed in Section 11-216", and 11-216 in turn points at
    # 11-164 — so naming the provision a retrieved section sends you to is
    # reading the page, not recalling from memory. Requiring every mention to
    # have been retrieved made it impossible to describe a cross-reference at
    # all, and threw away correct answers that did: the multi-hop question in
    # the regression suite asks for exactly this and was failed for answering it.
    named_in_evidence = set(_SECTION_MENTION.findall(sections_text)) | set(
        _CITATION.findall(sections_text)
    )
    cited = set(_CITATION.findall(answer)) | set(_SECTION_MENTION.findall(answer))
    for section in sorted(set(_CITATION.findall(answer))):
        if section in available:
            report.verified_citations.append(section)
        else:
            report.unverified_citations.append(section)

    for section in sorted(set(_SECTION_MENTION.findall(answer))):
        if section in report.verified_citations or section in report.unverified_citations:
            continue
        if section in available or section in named_in_evidence:
            report.verified_citations.append(section)
        else:
            report.unverified_citations.append(section)

    if report.unverified_citations:
        report.fail(
            "UNVERIFIED_CITATION",
            "The answer cites "
            + ", ".join(f"40 ILCS 5/{s}" for s in report.unverified_citations)
            + ", which was not among the sections retrieved for it.",
        )

    # 2. Money and percentage figures must trace to evidence.
    if check_numbers:
        allowed = _collect_allowed_numbers(
            calculation_result, sections_text, facts, user_text
        )
        unverified: list[str] = []

        for raw in _MONEY.findall(answer):
            try:
                value = Decimal(raw.replace(",", ""))
            except InvalidOperation:
                continue
            if not _matches_allowed(value, allowed, Decimal("0.02")):
                unverified.append(f"${raw}")

        for raw in _PERCENT.findall(answer):
            try:
                value = Decimal(raw)
            except InvalidOperation:
                continue
            if not _matches_allowed(value, allowed, Decimal("0.01")):
                unverified.append(f"{raw}%")

        report.unverified_numbers = unverified
        if unverified:
            report.fail(
                "UNVERIFIED_FIGURE",
                "The answer states "
                + ", ".join(unverified[:6])
                + ", which does not trace to a calculation result, the retrieved "
                "statute text, or the member's record.",
            )

    # 3. A legal claim with no citation at all is weak but not wrong.
    if not cited and len(answer.split()) > 60:
        report.warn(
            "NO_CITATIONS",
            "The answer makes statements about the Pension Code without citing a section.",
        )

    return report


def fallback_answer(report: GuardrailReport) -> str:
    """The message shown when verification fails hard.

    This replaces an answer the member has already begun reading, so it has to
    do two things at once: be honest that the answer was withdrawn, and be a
    reply a person would want to receive.

    It used to print the verification codes verbatim — "the answer cites 40 ILCS
    5/11-164, which was not among the sections retrieved for it". That tells the
    member about the machinery instead of about their pension, and it reads as a
    malfunction rather than as care. What they need is what went unverified, in
    their terms, and what to do next; the codes stay in the audit trail.
    """
    kinds = {failure["code"] for failure in report.hard_failures}

    if "UNVERIFIED_FIGURE" in kinds:
        detail = (
            "I had a figure in that answer I could not trace back to the Pension Code, "
            "and an estimate of your pension is not worth having unless every number in "
            "it comes from the statute and your own record."
        )
        next_step = (
            "If you tell me your age, your years of service and your salary history, "
            "I can work it through again from the Code itself."
        )
    elif "UNVERIFIED_CITATION" in kinds:
        detail = (
            "I found myself relying on a provision I do not have the text of, and I "
            "would rather not describe a rule I cannot read to you directly."
        )
        next_step = (
            "Naming the Section you are interested in usually helps — I currently have "
            "the text of Articles 11 and 12."
        )
    else:
        detail = (
            "Something in that answer did not hold up when I checked it back against "
            "the Pension Code."
        )
        next_step = "It may help to ask again more specifically, or about one Section."

    return (
        "I have taken that answer back — sorry.\n\n"
        f"{detail}\n\n"
        f"{next_step} For anything binding, your retirement fund is the one to ask."
    )
