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
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

# The coverage vocabulary lives with the module that produces it, so the
# check here and the adjudication there cannot drift apart.
from pension_chat.agent.coverage import BASIS_UNRESOLVED, ESTABLISHED_BASES
from pension_chat.calculation.assumption_policy import ASSUMPTION_SIGNALS, rule_for

logger = logging.getLogger("pension_chat.agent.guardrails")


# ── Scope ─────────────────────────────────────────────────────────


# Asks that are plainly somebody else's job. High precision by construction:
# when it fires it is right, so it refuses at zero cost. It is not the mechanism
# that keeps the assistant on topic -- `understand_turn` is -- and it is not
# meant to be exhaustive. Anything it does not name reaches the model, which is
# the right direction to fail in: the list that tried to be exhaustive is what
# refused "I'm starting to think about retiring".
_OUT_OF_SCOPE = re.compile(
    r"\b(write|generate|compose|draft)\s+(me\s+)?(a\s+)?"
    r"(poem|song|story|essay|joke|script|email|cover letter|resume)\b"
    r"|\b(recipe for|weather|stock price|medical advice|diagnose|"
    r"book (?:me )?a|order (?:me )?(?:some )?food|help me sleep)\b"
    r"|\bwho (won|is the president)\b",
    re.IGNORECASE,
)


#: A message that is only a pleasantry, and cannot be an answer to anything.
#: Matched against the whole message, so a greeting with a question attached
#: ("hi, how much will I get?") is a question. Deliberately a closed list: the
#: cost of missing one is a model call, and the cost of over-matching is
#: refusing a real question.
_PLEASANTRY = re.compile(
    r"\s*(?:hi|hey|hello|yo|greetings|good\s+(?:morning|afternoon|evening|day)"
    r"|bye|goodbye|see\s+you)(?:\s+there|\s+again)?[\s.!,?]*",
    re.IGNORECASE,
)

#: A bare acknowledgement. On a first turn there is nothing for it to
#: acknowledge, so it is a pleasantry. Mid-conversation it is the opposite of
#: one: "yes" is how a member answers "shall I take your retirement date as
#: today?", and declining it as a greeting throws their answer away. So these
#: are only ever a greeting when nothing precedes them -- which is what
#: `has_history` is for, and why `_CONTINUATION` matched them before.
_ACKNOWLEDGEMENT = re.compile(
    r"\s*(?:ok(?:ay)?|yes|yep|yeah|no|nope|sure|got\s+it|understood"
    r"|thanks?|thank\s+you|ta|cheers)[\s.!,?]*",
    re.IGNORECASE,
)


@dataclass
class ScopeVerdict:
    in_scope: bool
    reason: str = ""


def classify_scope(question: str, *, has_history: bool = False) -> ScopeVerdict:
    """Cheap deterministic scope check run before any model call.

    This used to be the mechanism that kept the assistant on topic, and it was
    **default-deny**: a message that matched no word in a pension vocabulary was
    refused outright. That caught "i want an ice cream" for free, and it also
    refused "I'm starting to think seriously about retiring" (because "retiring"
    does not contain "retire"), "my wife is worried about what happens to her"
    (the list held "spouse" and "widow", not "wife") and "does any of this get
    taxed" -- three members turned away in 1.75s at zero cost, which is the
    cheapest possible way to fail somebody.

    So the default is now **in scope**, and what a member meant is decided by
    `understand_turn`. Two things stay here, and both are the kind of check a
    regular expression is actually good for:

    * `_OUT_OF_SCOPE` -- high precision, so it refuses at no cost when it fires,
      and defers to the model when it does not.
    * `_PLEASANTRY` / `_ACKNOWLEDGEMENT` -- "hi" needs no model call to classify,
      and routing it onward loses the reason code as well as the round trip:
      `decline` picks GREETING_MESSAGE off `out_of_scope_reason == "greeting"`,
      and a model-written clause in its place answers "hi" with "that is outside
      the Pension Code". Both are matched whole-message, so "hi, how much will I
      get?" is a question.

    The measured cost of the change is one routing call (~570 tokens) on an
    off-topic aside the deny list does not name. The measured gain is the routing
    suite going from 1/7 to 7/7.

    Args:
        question: The member's message.
        has_history: Whether this session already has turns behind it. A bare
            "yes" or "ok" is an answer to whatever was asked last turn; only
            with nothing before it is it a pleasantry.
    """
    text = (question or "").strip()
    if not text:
        return ScopeVerdict(False, "The message was empty.")

    if _OUT_OF_SCOPE.search(text):
        return ScopeVerdict(False, "out_of_scope")

    if _PLEASANTRY.fullmatch(text):
        return ScopeVerdict(False, "greeting")
    if not has_history and _ACKNOWLEDGEMENT.fullmatch(text):
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


#: What a failure costs, worst first by `_COST`. A report carrying several
#: failures pays the worst of them.
RETRACT = "retract"
REPAIR = "repair"
EXCISE = "excise"

_COST = {RETRACT: 0, REPAIR: 1, EXCISE: 2}

#: A consequence per failure code. Anything absent retracts: a check added
#: without an entry here fails safe rather than quietly becoming excisable, and
#: `test_downgrade_ladder.py` fails until somebody decides which it is.
CONSEQUENCE: dict[str, str] = {
    "EMPTY_ANSWER": RETRACT,
    # The one failure that harms a member, so it is the one that costs
    # everything. A figure nobody can trace is not improved by deleting the
    # sentence around it.
    "UNVERIFIED_FIGURE": RETRACT,
    # A sentence naming a section nobody retrieved, beside four naming sections
    # that were. Withdrawing all five is how a sound explanation of teacher
    # pensions became "I have taken that answer back -- sorry."
    "UNVERIFIED_CITATION": EXCISE,
    "UNSUPPORTED_COVERAGE": EXCISE,
    # Nothing in the answer is wrong; something is missing. Cutting text cannot
    # add a disclosure, and cannot make a flat statement conditional.
    "ASSUMPTION_NOT_DISCLOSED": REPAIR,
    "UNMARKED_HYPOTHETICAL": REPAIR,
}


@dataclass
class GuardrailReport:
    """Outcome of verifying one generated answer."""

    passed: bool = True
    hard_failures: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    verified_citations: list[str] = field(default_factory=list)
    unverified_citations: list[str] = field(default_factory=list)
    unverified_numbers: list[str] = field(default_factory=list)

    def fail(self, code: str, message: str, spans: Sequence[str] = ()):
        """Record a hard failure.

        ``spans`` are verbatim substrings of the answer this failure objects
        to. They have to be verbatim: excision removes a span by string match,
        so one that does not appear would cut nothing and publish the sentence
        it objected to under a passing report.
        """
        self.passed = False
        self.hard_failures.append(
            {"code": code, "message": message, "spans": list(spans)}
        )

    @property
    def consequence(self) -> str:
        """The worst thing this report's failures cost; "" when it passed.

        An excisable failure that recorded no span is not excisable: knowing a
        sentence is wrong is not knowing which sentence, so it retracts.
        """
        worst = ""
        for failure in self.hard_failures:
            cost = CONSEQUENCE.get(failure["code"], RETRACT)
            if cost == EXCISE and not failure.get("spans"):
                cost = RETRACT
            if not worst or _COST[cost] < _COST[worst]:
                worst = cost
        return worst

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


# Rates the statute writes as fractions of a percent. Older Articles are drafted
# this way throughout — Article 12 reduces an early annuity by "1/4 of 1%" per
# month where Article 11 says "0.25%" for the identical rule.
_FRACTIONAL_PERCENT = re.compile(
    r"(?:(\d+)\s+)?(\d+)\s*/\s*(\d+)\s*(?:of\s+(\d+(?:\.\d+)?)\s*)?(?:%|percent)",
    re.IGNORECASE,
)


def _fractional_percentages(text: str) -> set[Decimal]:
    """Decimal readings of percentages the statute states as fractions.

    An answer that restates "1/4 of 1%" as "0.25%" has not introduced a figure —
    it has written the same rate the way a member reads rates. But 0.25 appears
    nowhere in the text, so numeric provenance rejected it and withdrew an
    otherwise correct answer. This only ever bit on Article 12, because Article
    11 happens to write the same reduction in decimal, which is why it surfaced
    the moment a second Article became reachable.
    """
    found: set[Decimal] = set()
    for match in _FRACTIONAL_PERCENT.finditer(text or ""):
        whole, numerator, denominator, of = match.groups()
        try:
            value = Decimal(numerator) / Decimal(denominator)
            if whole:
                value += Decimal(whole)
            if of:
                value *= Decimal(of)
        except (InvalidOperation, TypeError, ValueError, ZeroDivisionError):
            continue
        found.add(value)
        found.add(value / 100)
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
    allowed |= _fractional_percentages(sections_text)

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
    established_sections: Iterable[str] = (),
    coverage: dict | None = None,
    assumed_facts: Mapping[str, str] | None = None,
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
        established_sections: Sections earlier turns of this conversation
            already cited and had verified. "Why did you rule that one out?"
            is about a provision the previous turn retrieved, not this one.
        coverage: What this conversation believes about which Article covers
            the member, and on what evidence. Governs whether the answer may
            state that coverage as a fact about them.
        assumed_facts: Inputs this turn filled in rather than being told,
            keyed by fact name with the assumption's basis as the value (see
            ``calculation.assumption_policy``). A figure resting on one of
            these must disclose it or the answer fails verification.

    Returns:
        A ``GuardrailReport``. ``passed`` is False only for hard failures —
        a citation to a section that was never retrieved, or a money figure
        with no traceable source.
    """
    report = GuardrailReport()
    sections = list(retrieved_sections or [])
    available = {str(s.get("section")) for s in sections if s.get("section")}
    # A follow-up asking about a provision the conversation has already been
    # through retrieves on the follow-up's own words, which need not surface
    # that provision again. Those sections were verified when they were first
    # cited, so they stay available -- unlike a figure, a section id cannot be
    # invented into existence by being repeated.
    available |= {str(section) for section in established_sections if section}
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

    # 4. Coverage may only be asserted on evidence that supports it.
    _verify_coverage(report, answer, coverage)

    # 5. A figure resting on an assumed value must say so.
    _check_assumptions_disclosed(report, answer, calculation_result, assumed_facts or {})

    return report


# Frames that tell a member which fund they belong to, as opposed to describing
# what an Article provides. "Article 12 covers employees of a board of park
# commissioners" is a statement about the law; "you are covered by Article 12"
# is a statement about them, and only one of the two needs evidence about them.
_COVERAGE_ASSERTION = re.compile(
    r"\b(?:you(?:'re| are)?|your)\b[^.?!]{0,80}?"
    r"\b(?:covered by|come under|comes under|fall under|falls under|"
    r"governed by|belong to|belongs to|a member of|participate in)\b"
    r"[^.?!]{0,40}?\b(?:article|section)\s*\d{1,2}\b"
    r"|\bas an?\b[^.?!]{0,60}?\bcovered by\b[^.?!]{0,30}?\barticle\s*\d{1,2}\b"
    r"|\byour pension is (?:an? )?article\s*\d{1,2}\b",
    re.IGNORECASE,
)

# Saying where the belief came from. An answer that names its source is making a
# statement about the file, which is true and is what it was asked to do.
_ATTRIBUTED_TO_RECORD = re.compile(
    r"\b(?:your (?:record|file|records|account|membership record)|on file|"
    r"according to (?:your|the) (?:record|file|records)|"
    r"(?:is |are |been )?(?:currently )?(?:record|registr|regist|list|treat|hold|"
    r"administer)(?:ed|s)?\b|"
    r"what (?:your|the) (?:record|file) shows|the record (?:says|shows|places))\b",
    re.IGNORECASE,
)


def _sentence_around(text: str, index: int) -> str:
    """The sentence containing ``index``, so attribution is judged in context."""
    start = max(
        (text.rfind(mark, 0, index) for mark in (". ", "? ", "! ", "\n")), default=-1
    )
    end = min(
        (pos for pos in (text.find(mark, index) for mark in (". ", "? ", "!", "\n")) if pos != -1),
        default=len(text),
    )
    return text[start + 1 : end + 1]


# A quoted span, straight or curly. Used to take quotations out of the answer
# before judging what the answer claims.
_QUOTED_SPAN = re.compile(r"[\"“‘][^\"“”]{4,600}?[\"”’]", re.DOTALL)

# Markers that keep a supposition visibly a supposition.
_CONDITIONAL_MARKER = re.compile(
    r"\b(?:if you (?:were|are|had|worked)|assuming|were you |on that assumption|"
    r"in that (?:case|scenario)|under that (?:scenario|assumption|premise)|"
    r"hypothetical\w*|supposing|as you describe|on the supposition|"
    r"that would|you would be|would apply)\b",
    re.IGNORECASE,
)


def _verify_coverage(report: GuardrailReport, answer: str, coverage: dict | None) -> None:
    """Check what the answer claims about which fund the member is in.

    Two directions, and the second is the one that bites hardest.

    **An answer may not assert coverage the evidence does not support.** The
    conversation that prompted this opened with *"As a park employee covered by
    Article 11, your pension is calculated under Section 11-134(a)"* — a causal
    claim welded out of the member's description of their job and a column on
    their record, which nothing in the Pension Code connects. Every other check
    here passed it, because it contains no bad citation and no untraceable
    figure. It was simply not true.

    **An answer to a supposition may not read as fact.** The converse costs more
    than it looks: a member who asks "if I were a park employee" and is answered
    in the plain indicative has been told they are in a fund they were only
    asking about.
    """
    if not coverage:
        return

    basis = coverage.get("basis")

    # Quoting a sentence is not asserting it, and this check would otherwise
    # punish the assistant for repeating its own error in order to disown it.
    # The whole subject of that turn is the wrong sentence — a member asks "why
    # did you say 'As a park employee covered by Article 11…'" — so an answer
    # that cannot repeat the words cannot explain them.
    plain = _QUOTED_SPAN.sub(" ", answer or "")
    asserted = _COVERAGE_ASSERTION.search(plain)

    if basis == "hypothetical":
        opening = " ".join(plain.split()[:120])
        if not _CONDITIONAL_MARKER.search(opening):
            report.fail(
                "UNMARKED_HYPOTHETICAL",
                "The member asked about a supposed situation and the answer states it "
                "as fact. It has to stay visibly conditional.",
            )
        return

    # Only where the member has actually described themselves. The failure being
    # caught is a *bridge* — "as a park employee, you are covered by Article 11"
    # — and with nothing on one side of it there is nothing to weld: an answer
    # telling an ordinary member which Article governs them is reporting their
    # record, which is correct and is usually the question.
    #
    # Without this gate the check fires on `basis: record`, which is every
    # member who never mentioned their job — so "which retirement provision
    # applies to me?", close to this assistant's whole purpose, had its answer
    # withdrawn.
    # With no record at all the justification above disappears. There is no file
    # to be reporting, so an answer naming an Article as the member's is not
    # attributing anything — it is inventing a fund for someone who never said
    # which one they are in, and `described_as` is empty precisely because they
    # said nothing. That is the case the gate above lets through untouched.
    if asserted and not coverage.get("record_article") and basis == BASIS_UNRESOLVED:
        report.fail(
            "UNSUPPORTED_COVERAGE",
            "The answer tells the member which Article covers them, but there is "
            "no record for this member and they have not said which fund they are "
            "in. Ask which fund or employer they are under before treating any "
            "Article as theirs.",
        )
        return

    if asserted and coverage.get("described_as") and basis not in ESTABLISHED_BASES:
        # Attribution is the whole difference. "Your record places you under
        # Article 11" is a true statement about the file and is exactly what the
        # answer is asked to say; "as a park employee you are covered by Article
        # 11" is a claim about the law that nothing supports. Both mention the
        # member and an Article in one breath, so only the attribution separates
        # them — and an answer that attributes correctly must not be withdrawn
        # for it, or the honest phrasing becomes the punished one.
        sentence = _sentence_around(plain, asserted.start())
        if _ATTRIBUTED_TO_RECORD.search(sentence):
            return

        report.fail(
            "UNSUPPORTED_COVERAGE",
            "The answer tells the member which Article covers them, but that rests "
            "only on the Article recorded against their file — not on anything they "
            "said or on a coverage test in the Code. Say what the record shows and "
            "what the Code's test is, rather than presenting one as the other.",
        )


def _check_assumptions_disclosed(
    report: GuardrailReport,
    answer: str,
    calculation_result: dict[str, Any] | None,
    assumed_facts: Mapping[str, str],
) -> None:
    """A figure resting on a value nobody supplied must say so.

    Disclosure is a conjunction, not a single cue lookup: the answer must
    contain both a topic cue for the assumed input (from the policy rule's
    ``disclosure_cues``) *and* a word that marks the sentence as disclosing an
    assumption rather than merely mentioning the topic (from the module-level
    ``ASSUMPTION_SIGNALS``). A bare topic cue -- "as of today your record shows
    21.5 years of service" -- talks about the topic without ever telling the
    member anything was assumed, so it must not satisfy this check on its own.
    An answer with no figure in it has nothing to disclose.
    """
    # Only figures **this turn** computed. `computed` also carries earlier
    # turns' results, under `_carried_*` keys, so that a number this
    # conversation worked out before stays traceable when the answer refers back
    # to it. Traceability and disclosure are different questions: the carried
    # baseline is there so the member may be told the old number again, not so
    # its assumptions fall due a second time. They were disclosed on the turn
    # that made them.
    #
    # Without this, L05's second turn -- "what about my wife?", which computed
    # nothing and quoted nothing -- was rejected twice for failing to disclose
    # the retirement date assumed on turn one, and the member saw the fallback
    # instead of an answer about survivor benefits.
    this_turn = {
        name: entry
        for name, entry in (calculation_result or {}).items()
        if not str(name).startswith("_carried_")
    }
    if not assumed_facts or not this_turn:
        return

    lowered = answer.lower()
    has_signal = any(signal in lowered for signal in ASSUMPTION_SIGNALS)

    for name, basis in assumed_facts.items():
        rule = rule_for(name)
        cues = rule.disclosure_cues if rule else ()
        has_topic = any(cue.lower() in lowered for cue in cues)
        if has_topic and has_signal:
            continue
        report.fail(
            "ASSUMPTION_NOT_DISCLOSED",
            f"The answer states a figure that rests on an assumed value "
            f"({name}: {basis}) without telling the member it was assumed.",
        )


REPAIR_SYSTEM = """Your previous answer did not pass verification.

Rewrite it so that every problem listed is gone, changing as little else as
possible. Keep the voice, the shape, and every figure that was verified. A figure
you cannot trace to the worked figures, the statute text in front of you, the
member's record or the member's own messages must go -- say what would be needed
instead, or state the rule without totalling it. Never do arithmetic to replace
it. A provision whose text you do not have must be named in words, not cited in
brackets.

Return the corrected answer only. No preamble, no apology, no mention of the
correction."""


def _established_line(sheet: Any) -> str:
    """One sentence naming what the conversation already holds.

    Written as a list of noun phrases rather than clauses. Joining clauses onto
    "I still have" produced "I still have you are 45, and you have 21.5 years of
    service" -- and this is the one message whose whole job is to read as care
    rather than as a malfunction.
    """
    if sheet is None:
        return ""
    parts: list[str] = []
    values = sheet.usable_values()
    if (age := values.get("age")) is not None:
        parts.append(f"your age ({age})")
    if (service := values.get("service_years")) is not None:
        parts.append(f"your {service} years of service")
    if (fas := values.get("final_average_salary")) is not None:
        parts.append(f"your highest four-year average salary (${float(fas):,.2f})")
    if not parts:
        return ""
    if len(parts) == 1:
        return f"I still have {parts[0]}."
    return "I still have " + ", ".join(parts[:-1]) + f" and {parts[-1]}."


def fallback_answer(report: GuardrailReport, sheet: Any | None = None) -> str:
    """The message shown when verification fails and repair could not fix it.

    This replaces an answer the member has already begun reading, so it has to
    be honest that the answer was withdrawn and still be a reply a person would
    want to receive.

    It used to be a fixed template asking for the member's age, years of service
    and salary history regardless of what was known -- which, for a member whose
    record holds all three, reads as amnesia rather than as care. What it says
    now is built from the sheet.
    """
    kinds = {failure["code"] for failure in report.hard_failures}
    established = _established_line(sheet)

    if "UNVERIFIED_FIGURE" in kinds:
        detail = (
            "I had a figure in that answer I could not trace back to the Pension Code, "
            "and an estimate of your pension is not worth having unless every number in "
            "it comes from the statute and your own record."
        )
        next_step = (
            established or
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
            "the text of Articles 1, 8, 9, 11, 12 and 20."
        )
    else:
        detail = (
            "Something in that answer did not hold up when I checked it back against "
            "the Pension Code."
        )
        next_step = established or "It may help to ask again about one Section."

    return (
        "I have taken that answer back — sorry.\n\n"
        f"{detail}\n\n"
        f"{next_step} For anything binding, your retirement fund is the one to ask."
    )
