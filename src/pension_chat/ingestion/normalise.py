"""Text cleanup shared by every format's converter.

The parser anchors on the start of a line — `(40 ILCS 5/`, `Sec. `, `(a) ` — so
everything here exists to put those anchors back at the start of their lines,
and to avoid ever moving one off it.
"""

from __future__ import annotations

import re
from collections import Counter

#: A line the parser treats as structure. Nothing is ever joined onto the line
#: before one of these: merging a header into the previous line is exactly how a
#: section gets absorbed into its neighbour.
ANCHOR_RE = re.compile(
    r"^\s*(\(40 ILCS 5/|Sec\. |\([a-z](?:-\d+)?\)\s|\(\d+\)\s|\(Source:|\(Text of Section|WITH |WITHOUT )"
)

_INVISIBLE = dict.fromkeys(map(ord, "​‌‍⁠﻿"))
_PAGE_NUMBER_RE = re.compile(r"^(page\s+)?\d+(\s+of\s+\d+)?$", re.IGNORECASE)


def normalise(text: str) -> str:
    """Line endings to LF, invisible characters removed, NBSP to space, trailing space trimmed."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").translate(_INVISIBLE).replace("\xa0", " ")
    return "\n".join(line.rstrip() for line in text.split("\n"))


def strip_markdown(text: str) -> str:
    """Remove the markup that would hide a parser anchor; leave everything else."""
    out: list[str] = []
    for line in text.split("\n"):
        s = re.sub(r"^\s{0,3}#{1,6}\s+", "", line)
        s = s.replace("**", "").replace("__", "")
        s = re.sub(r"\\([\\`*_{}\[\]()#+\-.!>])", r"\1", s)
        bullet = re.match(r"^\s*[-*+]\s+(.*)$", s)
        if bullet and ANCHOR_RE.match(bullet.group(1)):
            s = bullet.group(1)
        out.append(s)
    return "\n".join(out)


def remove_page_furniture(pages: list[list[str]]) -> list[str]:
    """Drop running headers, footers and page numbers from PDF pages.

    A line counts as furniture when it sits in the first or last two non-blank
    lines of a page and either looks like a page number or recurs at a page edge
    on more than half the pages. Fewer than three pages cannot establish
    recurrence, so only page numbers are removed then.
    """
    repeated: set[str] = set()
    if len(pages) >= 3:
        edges: Counter[str] = Counter()
        for page in pages:
            nonblank = [line.strip() for line in page if line.strip()]
            edges.update(set(nonblank[:2] + nonblank[-2:]))
        repeated = {line for line, count in edges.items() if count * 2 > len(pages)}

    out: list[str] = []
    for page in pages:
        nonblank_idx = [i for i, line in enumerate(page) if line.strip()]
        edge_idx = set(nonblank_idx[:2] + nonblank_idx[-2:])
        for i, line in enumerate(page):
            s = line.strip()
            if i in edge_idx and (s in repeated or _PAGE_NUMBER_RE.match(s)):
                continue
            out.append(line)
    return out


def join_broken_lines(text: str) -> str:
    """Rejoin lines a PDF wrapped mid-sentence — only where the join is unambiguous.

    Joined: a line ending in a hyphenated word fragment followed by a lower-case
    continuation (hyphen removed), or a line without terminal punctuation
    followed by a lower-case continuation. Never joined: a line matching
    `ANCHOR_RE`, or anything after terminal punctuation.
    """
    out: list[str] = []
    for line in text.split("\n"):
        if out and out[-1].strip() and line.strip() and not ANCHOR_RE.match(line):
            prev = out[-1].rstrip()
            nxt = line.lstrip()
            if nxt[:1].islower():
                if re.search(r"[A-Za-z]-$", prev):
                    out[-1] = prev[:-1] + nxt
                    continue
                if not re.search(r"[.;:)\"']$", prev):
                    out[-1] = prev + " " + nxt
                    continue
        out.append(line)
    return "\n".join(out)
