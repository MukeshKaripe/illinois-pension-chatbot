"""Which `needs:` tags exist, and which have shipped.

Adding a tag to AVAILABLE is the act of shipping that capability to the evals:
from then on, the cases that need it count toward the score.
"""
KNOWN = frozenset({
    "statute", "calculation", "clarification", "recommendation",
    "employer_voice", "find_employee", "article_boundary", "pbms_records",
})
AVAILABLE = frozenset({"statute", "calculation", "clarification", "recommendation"})
