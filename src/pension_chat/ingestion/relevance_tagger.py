"""Calculation relevance tagger (§5.2).

Tags sections with calculation_relevance roles based on content analysis.
Roles: eligibility_rule, input_definition, service_calculation,
       benefit_formula, reduction, limit, parameter, actuarial_method,
       refund, cross_system_rule, coverage_rule
"""

from __future__ import annotations

import re

from pension_chat.ingestion.parser import ParsedSection

# Keyword patterns for each relevance role
_RELEVANCE_PATTERNS: dict[str, list[re.Pattern]] = {
    "eligibility_rule": [
        re.compile(r"eligib|qualify|qualif|entitled to|right to|shall be eligible", re.I),
        re.compile(r"at least \d+ years? of service", re.I),
        re.compile(r"upon attaining age \d+", re.I),
        re.compile(r"years? of service", re.I),
    ],
    "input_definition": [
        re.compile(r"\"[A-Z][a-z]+\":\s", re.I),
        re.compile(r"shall mean|is defined as|for purposes of", re.I),
        re.compile(r"salary|compensation|earnings", re.I),
    ],
    "service_calculation": [
        re.compile(r"years? of service|service credit|credited service", re.I),
        re.compile(r"period of service|continuous service|total service", re.I),
    ],
    "benefit_formula": [
        re.compile(r"annuit(?:y|ies)\s+(?:shall|amount|equal|computed|calculated)", re.I),
        re.compile(r"retirement annuity|pension amount|benefit amount", re.I),
        re.compile(r"percent.*(?:salary|compensation|earnings)", re.I),
        re.compile(r"(?:2|2\.4|1\.67|1\.5)\s*%|percent(?:um)?", re.I),
        re.compile(r"final average salary|highest.*salary", re.I),
        re.compile(r"accrual rate|multiplier", re.I),
    ],
    "reduction": [
        re.compile(r"reduc(?:ed|tion)|penalty|decrease", re.I),
        re.compile(r"one-half of one percent|0\.5%", re.I),
        re.compile(r"early retirement|before age \d+", re.I),
    ],
    "limit": [
        re.compile(r"maximum|minimum|cap(?:ped)?|ceiling|floor|shall not exceed", re.I),
        re.compile(r"the greater of|the lesser of|whichever is (?:greater|less)", re.I),
        re.compile(r"limitation|limit(?:ed)?\s+to", re.I),
    ],
    "parameter": [
        re.compile(r"consumer price index|CPI|cost.of.living|COLA", re.I),
        re.compile(r"interest rate|actuarial.*rate", re.I),
        re.compile(r"contribution rate|\d+(?:\.\d+)?%", re.I),
    ],
    "actuarial_method": [
        re.compile(r"actuari(?:al|y)|annuity table|mortality table", re.I),
        re.compile(r"present value|reserve|funded ratio", re.I),
    ],
    "refund": [
        re.compile(r"refund|withdrawal|separation.*benefit", re.I),
        re.compile(r"return of contributions|repay|repayment", re.I),
    ],
    "coverage_rule": [
        # Who an Article covers is the determination every other determination
        # rests on, and it is written the same way in every Article: a "Creation
        # of fund" section naming the employer, and a definition section saying
        # who counts as an "Employee". Article 11 and Article 12 differ by
        # exactly these sections -- 11-110 says labor-service employees of a
        # city over 500,000, 12-118 says anyone in the service of a board of
        # park commissioners -- and nothing in the pilot ever consulted either.
        #
        # Titles are matched anchored, because a title match alone earns the tag
        # and the corpus is full of "Employee contributions" and "Employee
        # annuity" sections that are about money, not about coverage.
        re.compile(r'^"?employees?"?\s*(?:[",:]|or\b|$)', re.I),
        re.compile(r"^(?:creation|establishment) of (?:the )?fund", re.I),
        re.compile(r"^transferred employee", re.I),
        re.compile(r"^employer\s*$", re.I),
        re.compile(r'^"?(?:present employee|future entrant|re-?entrant)', re.I),
        re.compile(r"^persons? (?:included|covered|excluded|eligible)", re.I),
        re.compile(r"^(?:applicability|application) of (?:this )?article", re.I),
        # Body cues. Two must hit, so a passing mention of "employer" is not
        # enough on its own.
        re.compile(r'"employees?"\s*(?:[,:]|means|shall mean|includes)', re.I),
        re.compile(r"\bany person (?:employed|in the service of)\b", re.I),
        re.compile(r"\bany employee (?:of an employer|in the service of|employed)\b", re.I),
        re.compile(r"\bin each (?:city|county|village|town|municipality) of (?:more than|over)\b", re.I),
        re.compile(r"\bfor the benefit of the .{0,80}?employees\b", re.I),
        re.compile(r"\bboard of park commissioners\b", re.I),
        re.compile(r"\bshall not be included as being\b", re.I),
        re.compile(r"\balready a member of the fund\b", re.I),
    ],
    "cross_system_rule": [
        re.compile(r"reciproc(?:al|ity)|transfer.*(?:credit|service)", re.I),
        re.compile(r"Article\s+\d+|another.*fund|other.*system", re.I),
    ],
}


def tag_calculation_relevance(section: ParsedSection) -> list[str]:
    """Tag a section with its calculation relevance roles.

    A section can have multiple roles (e.g., both eligibility_rule
    and benefit_formula). This is a discovery signal, not a claim
    that the section is a standalone formula.
    """
    text = section.full_text
    title = section.section_title.lower()

    tags: list[str] = []

    for role, patterns in _RELEVANCE_PATTERNS.items():
        # Check title first (stronger signal)
        title_match = any(p.search(title) for p in patterns)
        # Then check full text
        text_match = sum(1 for p in patterns if p.search(text))

        # Title match is a strong signal; text needs multiple pattern hits
        if title_match or text_match >= 2:
            tags.append(role)

    return tags
