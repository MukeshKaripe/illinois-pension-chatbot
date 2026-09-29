"""Group converted text by Pension Code Article, using the Code's own numbers.

A cut happens at an Article or division heading, or at a section header whose
Article differs from the current one. Lines before the first cut belong to the
Article after it. Only 40 ILCS 5 is accepted: another act is a different corpus
with no Article numbering for `pension_articles` or the index to hold.
"""

from __future__ import annotations

import re

from pension_chat.ingestion.converters import IngestionError

_ART_HEADING_RE = re.compile(r"^\(40 ILCS 5/Art\.\s+(\d+[A-Z]?)\b")
_SECTION_RE = re.compile(r"^\(40 ILCS 5/(\d+[A-Z]?)-")
_ANY_ACT_RE = re.compile(r"^\((\d+)\s+ILCS\s+(\d+)/")


class UnsupportedAct(IngestionError):
    pass


class UnsupportedArticle(IngestionError):
    pass


def split_by_article(text: str) -> dict[int, str]:
    blocks: dict[int, list[str]] = {}
    pending: list[str] = []
    current: int | None = None

    for line in text.split("\n"):
        s = line.strip()
        act = _ANY_ACT_RE.match(s)
        if act and (act.group(1), act.group(2)) != ("40", "5"):
            raise UnsupportedAct(
                f"{act.group(1)} ILCS {act.group(2)} is not the Pension Code (40 ILCS 5); "
                "only the Pension Code can be ingested"
            )
        marker = _ART_HEADING_RE.match(s) or _SECTION_RE.match(s)
        if marker:
            article = _article_number(marker.group(1))
            if current is None:
                blocks.setdefault(article, []).extend(pending)
                pending = []
            current = article
            blocks.setdefault(current, []).append(line)
        elif current is None:
            pending.append(line)
        else:
            blocks[current].append(line)

    return {article: "\n".join(lines) for article, lines in blocks.items()}


def _article_number(raw: str) -> int:
    if not raw.isdigit():
        raise UnsupportedArticle(
            f"Article {raw}: letter-suffixed Articles cannot be recorded "
            "(pension_articles.article_number is an integer)"
        )
    return int(raw)
