"""Evidence fingerprinting for recipe staleness detection.

Hashes source section content so the system can detect when the underlying
statute has changed and mark recipes as stale.
"""

from __future__ import annotations

import hashlib

import xxhash


def compute_section_fingerprint(section_id: str, content: str) -> str:
    """Compute a fingerprint for a single section's content.

    Uses xxhash for speed (this runs on every reuse check).
    """
    return xxhash.xxh64(f"{section_id}:{content}".encode()).hexdigest()


def compute_evidence_fingerprint(
    sections: list[dict[str, str]],
) -> dict[str, str]:
    """Compute fingerprints for all source sections of a recipe.

    Args:
        sections: List of {"section_id": "...", "content": "..."} dicts.

    Returns:
        Dict mapping section_id to its content fingerprint.
    """
    return {
        s["section_id"]: compute_section_fingerprint(s["section_id"], s["content"])
        for s in sections
    }


def verify_fingerprints(
    stored_fingerprint: dict[str, str],
    current_sections: list[dict[str, str]],
) -> dict[str, str]:
    """Verify that stored fingerprints still match current content.

    Returns:
        Dict of mismatched section_ids with status:
        - "changed": content has changed
        - "missing": section no longer in the index
        Empty dict means all fingerprints match.
    """
    current_fp = compute_evidence_fingerprint(current_sections)
    mismatches: dict[str, str] = {}

    for section_id, stored_hash in stored_fingerprint.items():
        if section_id not in current_fp:
            mismatches[section_id] = "missing"
        elif current_fp[section_id] != stored_hash:
            mismatches[section_id] = "changed"

    return mismatches


def compute_implementation_hash(rule_spec: dict) -> str:
    """Compute a hash of the rule specification for versioning.

    This captures the logical structure of the calculation,
    not just the source sections.
    """
    import json
    canonical = json.dumps(rule_spec, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]
