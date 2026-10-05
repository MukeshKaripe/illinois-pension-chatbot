"""Code checks over a TurnTrace -- the vetoes. Structure over substrings: they
read the verified citations, the subsections rendered to the model, and each
input's origin wherever those exist, and the answer text only for stated facts
and questions. Each returns the offending values, so a failure line says what
went wrong without opening the trace.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from pension_chat.agent.facts import aliases_of
from pension_chat.agent.retrieval import CROSS_CUTTING_ARTICLES
from pension_chat.evals.cases import TTFT_CLASSES, Turn
from pension_chat.evals.trace import TurnTrace


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""
    gating: bool = True


ORIGINS = {
    "engine": set(),
    "record": {"record"},
    "user": {"stated", "hypothetical", "stand_in"},
    "statute": {"statutory_parameter"},
    "assumed": {"assumed"},
}
ALWAYS_ALLOWED = {"derived"}

#: Policy names for inputs the engine calls something else. When a member first
#: joined decides the tier, and the engine receives it as the hire date.
CRITICAL_ALIASES = {
    "participation_date": frozenset({"participation_date", "hire_date", "membership_date"}),
}

#: Words that mark a question as asking for this input. Matched only in
#: sentences that end with "?", outside quotation marks.
REASK_CUES = {
    "age": ("how old", "age", "date of birth", "born"),
    "salary": ("salary", "earn", "pay"),
    "service": ("years of service", "service credit", "how long"),
    "participation_date": ("when did", "join", "hire date", "hired", "became a member", "first became"),
    "tier": ("which tier", "what tier"),
}


def _article(section: str) -> str:
    return section.split("-", 1)[0]


def _norm(text: str) -> str:
    text = text.lower().replace("$", "")
    text = re.sub(r"(?<=\d),(?=\d{3})", "", text)
    return " ".join(text.split())


#: A section citation in any of the forms an answer writes one -- "§1-162",
#: "Section 1-162(c)", "40 ILCS 5/1-162" -- removed before facts are matched, so
#: "62" is never satisfied by the 62 inside "1-162".
_CITATION_RE = re.compile(
    r"(?:§\s*|sections?\s+|40 ilcs 5/)?\b\d{1,2}[a-c]?-\d+(?:\.\d+)?[a-z]?(?:\([a-z0-9-]+\))*"
)


def _states(answer: str, fact: str) -> bool:
    """Whether the answer states this fact as a whole token, citations aside."""
    text = _norm(answer)
    if not _CITATION_RE.fullmatch(_norm(fact)):
        # A fact that is itself a section number is looked for as one.
        text = _CITATION_RE.sub(" ", text)
    return re.search(r"(?<![\w.])" + re.escape(_norm(fact)) + r"(?![\w]|\.\d)", text) is not None


def _mentions(question: str, cue: str) -> bool:
    return re.search(r"\b" + re.escape(cue) + r"\b", question.lower()) is not None


def _questions(answer: str) -> list[str]:
    unquoted = re.sub(r"[\"“][^\"”]*[\"”]", " ", answer)
    return [s.strip() for s in re.findall(r"[^.?!\n]*\?", unquoted) if s.strip()]


def _check(name: str, value: Any, trace: TurnTrace, latency_gate: bool) -> CheckResult:
    verified = trace.verified_citations
    if name == "cites_any":
        return CheckResult(name, any(s in verified for s in value), f"verified {verified}")
    if name == "cites_all":
        missing = [s for s in value if s not in verified]
        return CheckResult(name, not missing, f"missing {missing}")
    if name == "cites_none_outside":
        allowed = set(value) | set(CROSS_CUTTING_ARTICLES)
        outside = [s for s in verified if _article(s) not in allowed]
        return CheckResult(name, not outside, f"outside {outside}")
    if name == "evidence_has":
        missing = []
        for item in value:
            m = re.fullmatch(r"([\dA-Z]+-[\d.]+[a-z]?)(\(.+\))?", item)
            section, sub = (m.group(1), m.group(2)) if m else (item, None)
            got = trace.rendered.get(section)
            if got is None or (sub and sub not in got):
                missing.append(item)
        return CheckResult(name, not missing, f"not rendered to the model: {missing}; rendered {trace.rendered}")
    if name in ("must_state", "must_not_state"):
        hits = [f for f in value if _states(trace.final_answer, str(f))]
        if name == "must_state":
            missing = [f for f in value if f not in hits]
            return CheckResult(name, not missing, f"not stated: {missing}")
        return CheckResult(name, not hits, f"stated: {hits}")
    if name == "calculation":
        return CheckResult(name, trace.calculation is value, f"calculation={trace.calculation}")
    if name == "no_calculation":
        return CheckResult(name, not trace.calculation, f"calculation={trace.calculation}")
    if name == "figures_from":
        allowed = set().union(*(ORIGINS[v] for v in value)) | ALWAYS_ALLOWED
        bad = {k: o for k, o in trace.input_sources.items() if o not in allowed}
        ok = not bad and not trace.unverified_numbers
        return CheckResult(name, ok, f"origins {bad}; untraced {trace.unverified_numbers}")
    if name == "worked_from_article":
        section = str((trace.card or {}).get("section") or "")
        return CheckResult(name, _article(section) == value, f"worked from {section or 'nothing'}")
    if name == "answers_from_article":
        allowed = {value} | set(CROSS_CUTTING_ARTICLES)
        section = str((trace.card or {}).get("section") or "")
        outside = [s for s in verified + ([section] if section else []) if _article(s) not in allowed]
        return CheckResult(name, bool(verified or section) and not outside, f"outside {outside}")
    if name == "critical_inputs":
        assumed = [n for n in value for alias in CRITICAL_ALIASES.get(n, aliases_of(n))
                   if trace.input_sources.get(alias) == "assumed"
                   or (trace.assumptions.get(alias) or {}).get("source") == "assumed"]
        return CheckResult(name, not assumed, f"assumed critical inputs: {sorted(set(assumed))}")
    if name == "max_assumed":
        count = sum(1 for o in trace.input_sources.values() if o == "assumed")
        return CheckResult(name, count <= value, f"{count} assumed")
    if name == "assumptions_disclosed":
        codes = [c for c in trace.failure_codes if "assumption" in c.lower()]
        return CheckResult(name, not codes, f"codes {codes}")
    if name == "no_reask":
        asked = [n for n in value for q in _questions(trace.final_answer)
                 if any(_mentions(q, cue) for cue in REASK_CUES.get(n, (n.replace("_", " "),)))]
        return CheckResult(name, not asked, f"asked again for {sorted(set(asked))}")
    if name == "max_questions":
        count = len(_questions(trace.final_answer))
        return CheckResult(name, count <= value, f"{count} questions")
    if name == "refuses":
        return CheckResult(name, (not trace.in_scope) is value, f"in_scope={trace.in_scope}")
    if name == "verification_passed":
        return CheckResult(name, bool(trace.guardrail_passed) is value, f"passed={trace.guardrail_passed}")
    if name == "not_replaced":
        return CheckResult(name, (not trace.replacements) is value, f"replacements {trace.replacements}")
    if name == "about_employee":
        return CheckResult(name, trace.subject == value, f"subject {trace.subject}")
    if name == "expected_figure":
        # A figure worked by hand from the statute, to the cent. `when` picks the
        # figure: "now" (the default) or a word from a scenario's label ("67").
        want = Decimal(str(value["annual"]))
        when = str(value.get("when") or "now")
        candidates = [f for f in trace.figures if when == "now" and f["when"] == "now"
                      or when != "now" and when in f["when"]]
        amounts = [Decimal(f["annual"]) for f in candidates]
        ok = any(abs(a - want) <= Decimal("0.01") for a in amounts)
        return CheckResult(name, ok, f"expected {want} ({when}); computed {[str(a) for a in amounts] or 'no figure'}")
    if name == "expected_figures":
        # One figure per person, each to the cent and each under the right name --
        # a comparison that swaps two people's figures fails here.
        misses = []
        for want in value:
            amount = Decimal(str(want["annual"]))
            mine = [Decimal(f["annual"]) for f in trace.figures
                    if f.get("person") == want["person"] and f["when"] == str(want.get("when") or "now")]
            if not any(abs(a - amount) <= Decimal("0.01") for a in mine):
                misses.append(f"{want['person']}: expected {amount}, computed {[str(a) for a in mine] or 'none'}")
        return CheckResult(name, not misses, "; ".join(misses) or "every person's figure as expected")
    if name == "ttft_class":
        limit = TTFT_CLASSES[value]
        ttft = None if trace.ttft is None else trace.ttft - trace.ttft_rate_limit_wait
        return CheckResult(name, ttft is not None and ttft <= limit,
                           f"ttft {ttft if ttft is None else round(ttft, 2)}s vs {limit}s", gating=latency_gate)
    if name == "model_calls_max":
        calls = trace.model_calls
        return CheckResult(name, calls is not None and calls <= value,
                           f"{calls} sequential model calls before the first word vs {value}")
    raise KeyError(name)


def run_checks(turn: Turn, trace: TurnTrace, *, latency_gate: bool = False) -> list[CheckResult]:
    if trace.error:
        return [CheckResult(name, False, f"turn errored: {trace.error}") for name in turn.checks] or [
            CheckResult("error", False, trace.error)]
    return [_check(name, value, trace, latency_gate) for name, value in turn.checks.items()]
