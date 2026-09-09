"""ILGA statute text parser.

Parses the raw .txt files from ILGA into structured records matching the
§5.2 index schema. Handles the (40 ILCS 5/X-XXX) format with (Source: ...)
markers, subsections (a), (b), etc.

Handles:
- Article → Section → Subsection hierarchy
- Effective dates from source Public Acts
- Status (active/repealed) detection
- Cross-reference extraction from section text
- The repealed-text hazard and effective-date trap from §4
- Sections ILGA prints more than once under a single header, each copy
  introduced by a "(Text of Section ...)" marker — see `_split_variants`
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger("pension_chat.ingestion.parser")


@dataclass
class ParsedSubsection:
    """A single subsection within a section."""
    subsection_id: str  # e.g. "(a)", "(b)", "(c)(1)"
    text: str


@dataclass
class ParsedSection:
    """A fully parsed section of the pension code."""
    article_number: int
    section_id: str          # e.g. "11-134"
    section_title: str       # e.g. "Retirement annuity amount"
    full_text: str           # Complete section text
    subsections: list[ParsedSubsection] = field(default_factory=list)
    source_public_act: str | None = None
    status: str = "active"   # active | repealed
    referenced_sections: list[str] = field(default_factory=list)
    referenced_subsections: list[str] = field(default_factory=list)
    effective_date: str | None = None
    division: str | None = None
    chapter: str = "40"
    act: str = "5"
    # Set when ILGA prints this section more than once under one header — e.g.
    # "from P.A. 102-719", "after amendment by P.A. 104-457". `None` means the
    # section has a single text, which is the ordinary case.
    variant_label: str | None = None


# ── Regex patterns ────────────────────────────────────────────────

# Match section headers like "(40 ILCS 5/11-134)".
#
# The trailing `[a-z]?` is not cosmetic. Section 1-113.4a exists and sits
# directly after Section 1-113.4; without it the header did not match at all,
# so that section was never opened and its text was appended to the section
# before it — a whole section silently absorbed into its neighbour, and its
# subsections colliding with that neighbour's on upload.
SECTION_HEADER_RE = re.compile(
    r"^\(40 ILCS 5/(\d+)-(\d+(?:\.\d+)?[a-z]?)\)"
)

# Match section title like "Sec. 11-134. Retirement annuity amount."
SECTION_TITLE_RE = re.compile(
    r"Sec\.\s+(\d+-\d+(?:\.\d+)?[a-z]?)\.\s+(.+?)(?:\.|$)"
)

# Match source line like "(Source: P.A. 96-0889.)"
SOURCE_RE = re.compile(
    r"\(Source:\s*(.+?)\.\s*\)"
)

# Match subsection markers like (a), (b), (c)(1), etc.
SUBSECTION_RE = re.compile(
    r"^\s*\(([a-z](?:-\d+)?)\)\s"
)

# Match nested subsection markers like (1), (2), etc. within a subsection
NESTED_SUBSECTION_RE = re.compile(
    r"^\s*\((\d+)\)\s"
)

# Match article heading
ARTICLE_HEADING_RE = re.compile(
    r"^\(40 ILCS 5/Art\.\s+(\d+)\s+heading\)"
)

# Match cross-references to other sections like "Section 11-134" or "11-134(a)"
CROSS_REF_RE = re.compile(
    r"(?:Section|Sections?)\s+(\d+-\d+(?:\.\d+)?[a-z]?(?:\([a-z0-9]+\))?)"
)

# Match inline section references like "11-134"
INLINE_REF_RE = re.compile(
    r"\b(\d{1,2}-\d{3}(?:\.\d+)?[a-z]?)\b"
)

# Detect repealed sections
REPEALED_RE = re.compile(
    r"(?:repealed|was repealed|Section repealed)",
    re.IGNORECASE,
)

# A section printed more than once under one header, each copy introduced by a
# marker line. See `_split_variants` for what each form means.
VARIANT_MARKER_RE = re.compile(r"^\(Text of Section\s+(.+?)\)\s*$", re.IGNORECASE)

# "WITH the changes made by P.A. 98-599, which has been held unconstitutional"
VOID_VARIANT_RE = re.compile(
    r"^WITH the changes made by .+?held unconstitutional$", re.IGNORECASE
)

# "WITHOUT the changes made by P.A. 98-599, which has been held unconstitutional"
OPERATIVE_VARIANT_RE = re.compile(
    r"^WITHOUT the changes made by .+?held unconstitutional$", re.IGNORECASE
)

# Division headers
DIVISION_RE = re.compile(
    r"^\(40 ILCS 5/Art\.\s+\d+,\s+div\.\s+(\w+)\s+heading\)",
    re.IGNORECASE,
)


def parse_statute_file(filepath: str, article_number: int) -> list[ParsedSection]:
    """Parse an ILGA statute text file into structured sections.

    Args:
        filepath: Path to the .txt file.
        article_number: The article number (e.g. 11, 12).

    Returns:
        List of ParsedSection objects.
    """
    with open(filepath, encoding="utf-8") as f:
        content = f.read()

    # Normalize line endings
    content = content.replace("\r\n", "\n").replace("\r", "\n")

    sections: list[ParsedSection] = []
    current_division: str | None = None

    # Split into raw section blocks
    lines = content.split("\n")
    section_blocks: list[tuple[int, str, list[str]]] = []

    current_section_id: str | None = None
    current_block_lines: list[str] = []
    current_header_line: str = ""

    for line in lines:
        # Check for division headers
        div_match = DIVISION_RE.match(line.strip())
        if div_match:
            current_division = div_match.group(1)
            continue

        # Check for article heading
        art_match = ARTICLE_HEADING_RE.match(line.strip())
        if art_match:
            continue

        # Check for new section header
        header_match = SECTION_HEADER_RE.match(line.strip())
        if header_match:
            # Save previous block
            if current_section_id and current_block_lines:
                section_blocks.append(
                    (article_number, current_section_id, current_block_lines.copy())
                )

            article_num = int(header_match.group(1))
            section_num = header_match.group(2)
            current_section_id = f"{article_num}-{section_num}"
            current_block_lines = [line]
            current_header_line = line
        elif current_section_id:
            current_block_lines.append(line)

    # Don't forget the last block
    if current_section_id and current_block_lines:
        section_blocks.append(
            (article_number, current_section_id, current_block_lines.copy())
        )

    # Parse each section block, one ParsedSection per surviving variant
    for art_num, section_id, block_lines in section_blocks:
        for label, variant_lines in _split_variants(block_lines):
            section = _parse_section_block(
                art_num, section_id, variant_lines, current_division, label
            )
            if section:
                sections.append(section)

    logger.info(
        "Parsed Article %d: %d sections from %s",
        article_number,
        len(sections),
        filepath,
    )

    return sections


def _split_variants(lines: list[str]) -> list[tuple[str | None, list[str]]]:
    """Split one section header's block into the texts it actually contains.

    ILGA prints a section several times under a single header when it has been
    amended more than once, marking each copy with a "(Text of Section ...)"
    line. Concatenating them, which is what happens if you ignore the markers,
    produces one section whose text contradicts itself and whose subsection
    letters repeat — and since the index is keyed on section + subsection, the
    repeats silently overwrite each other on upload. Section 11-134.1, the
    automatic annuity increase, has been indexed that way since the pilot began.

    Three forms, and they do not mean the same thing:

    * "WITH the changes made by P.A. X, which has been held unconstitutional" —
      **dropped**. A court has struck it; it is not law, and the retrieval layer
      has no way to mark text as void once it is in the index.
    * "WITHOUT the changes made by P.A. X, which has been held unconstitutional"
      — kept, unlabelled. With its counterpart gone this is simply the section.
    * anything else ("from P.A. 102-719", "before/after amendment by P.A.
      104-457") — kept and **labelled**. These are concurrent or pending
      amendments that ILGA has not reconciled; all of them are on the books, and
      choosing between them is a reading of the law, not a parsing decision.

    Returns `[(None, lines)]` unchanged when the block carries no marker, which
    is the overwhelming majority of sections.
    """
    marker_positions = [
        (i, m.group(1).strip())
        for i, line in enumerate(lines)
        if (m := VARIANT_MARKER_RE.match(line.strip()))
    ]
    if not marker_positions:
        return [(None, lines)]

    # Everything above the first marker is the header, and belongs to each copy.
    header = lines[: marker_positions[0][0]]
    variants: list[tuple[str | None, list[str]]] = []
    for idx, (start, descriptor) in enumerate(marker_positions):
        end = (
            marker_positions[idx + 1][0]
            if idx + 1 < len(marker_positions)
            else len(lines)
        )
        if VOID_VARIANT_RE.match(descriptor):
            continue
        label = None if OPERATIVE_VARIANT_RE.match(descriptor) else descriptor
        variants.append((label, header + lines[start + 1 : end]))

    # A block whose only copy was the struck one would otherwise vanish; keep it
    # whole rather than lose the section, and let `status` carry the warning.
    return variants or [(None, lines)]


def _parse_section_block(
    article_number: int,
    section_id: str,
    lines: list[str],
    division: str | None,
    variant_label: str | None = None,
) -> ParsedSection | None:
    """Parse a single section block into a ParsedSection."""
    full_text = "\n".join(lines).strip()

    if not full_text:
        return None

    # Extract section title
    section_title = ""
    for line in lines:
        title_match = SECTION_TITLE_RE.search(line)
        if title_match:
            section_title = title_match.group(2).strip()
            break

    if not section_title:
        # Try extracting from the first substantial line after the header
        for line in lines[1:]:
            stripped = line.strip()
            if stripped and not stripped.startswith("(40 ILCS") and not stripped.startswith("(from Ch."):
                # Use the first few words as title
                section_title = stripped[:100]
                break

    # Extract source Public Act
    source_pa: str | None = None
    source_match = SOURCE_RE.search(full_text)
    if source_match:
        source_pa = source_match.group(1).strip()

    # Detect repealed status
    status = "active"
    if REPEALED_RE.search(full_text):
        status = "repealed"

    # Extract cross-references
    referenced_sections: list[str] = []
    referenced_subsections: list[str] = []

    for match in CROSS_REF_RE.finditer(full_text):
        ref = match.group(1)
        if "(" in ref:
            referenced_subsections.append(ref)
            base = ref.split("(")[0]
            if base != section_id and base not in referenced_sections:
                referenced_sections.append(base)
        else:
            if ref != section_id and ref not in referenced_sections:
                referenced_sections.append(ref)

    # Also find inline references (section numbers mentioned without "Section" prefix)
    for match in INLINE_REF_RE.finditer(full_text):
        ref = match.group(1)
        article_prefix = f"{article_number}-"
        if ref.startswith(article_prefix) and ref != section_id:
            if ref not in referenced_sections:
                referenced_sections.append(ref)

    # Parse subsections
    subsections = _parse_subsections(lines)

    return ParsedSection(
        article_number=article_number,
        section_id=section_id,
        section_title=section_title,
        full_text=full_text,
        subsections=subsections,
        source_public_act=source_pa,
        status=status,
        referenced_sections=referenced_sections,
        referenced_subsections=referenced_subsections,
        division=division,
        variant_label=variant_label,
    )


def _parse_subsections(lines: list[str]) -> list[ParsedSubsection]:
    """Extract subsections from section lines."""
    subsections: list[ParsedSubsection] = []
    current_subsection_id: str | None = None
    current_subsection_lines: list[str] = []

    # Skip the header line(s) to find where the section body starts
    body_started = False
    for line in lines:
        stripped = line.strip()

        if not body_started:
            # Skip header lines
            if SECTION_TITLE_RE.search(stripped):
                body_started = True
                # Check if the title line itself starts the content
                after_title = SECTION_TITLE_RE.sub("", stripped).strip()
                if after_title:
                    sub_match = SUBSECTION_RE.match(after_title)
                    if sub_match:
                        if current_subsection_id and current_subsection_lines:
                            subsections.append(ParsedSubsection(
                                subsection_id=f"({current_subsection_id})",
                                text="\n".join(current_subsection_lines).strip(),
                            ))
                        current_subsection_id = sub_match.group(1)
                        current_subsection_lines = [after_title]
                continue
            if stripped.startswith("(40 ILCS") or stripped.startswith("(from Ch."):
                continue
            if not stripped:
                continue
            body_started = True

        # Check for subsection marker
        sub_match = SUBSECTION_RE.match(stripped)
        if sub_match:
            # Save previous subsection
            if current_subsection_id and current_subsection_lines:
                subsections.append(ParsedSubsection(
                    subsection_id=f"({current_subsection_id})",
                    text="\n".join(current_subsection_lines).strip(),
                ))
            current_subsection_id = sub_match.group(1)
            current_subsection_lines = [stripped]
        elif current_subsection_id:
            # Skip source lines
            if not SOURCE_RE.match(stripped):
                current_subsection_lines.append(stripped)

    # Save last subsection
    if current_subsection_id and current_subsection_lines:
        subsections.append(ParsedSubsection(
            subsection_id=f"({current_subsection_id})",
            text="\n".join(current_subsection_lines).strip(),
        ))

    return subsections
