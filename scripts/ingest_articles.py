"""Ingest statute text into Azure AI Search.

Usage:
    uv run python scripts/ingest_articles.py --corpus meabf --dry-run
    uv run python scripts/ingest_articles.py --corpus meabf
    uv run python scripts/ingest_articles.py --corpus meabf --source blob
    uv run python scripts/ingest_articles.py --corpus ilga-all      # every integer Article, as before

Always run --dry-run first: it converts, checks and parses everything and
prints what each Article would add or remove, without embedding or writing.
"""

import argparse
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dotenv import load_dotenv

load_dotenv()

from azure.core.exceptions import ResourceNotFoundError
from sqlalchemy import update

from pension_chat.config import get_settings
from pension_chat.db.models import PensionArticle
from pension_chat.db.session import get_admin_session
from pension_chat.ingestion.embedder import generate_embeddings
from pension_chat.ingestion.indexer import (
    create_search_index,
    get_index_client,
    list_indexed_sections,
    prune_stale_records,
    upload_records,
)
from pension_chat.ingestion.pipeline import CORPORA, RunReport, Sinks, run
from pension_chat.ingestion.sources import BlobSource, LocalDirSource

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ingestion_cli")

STATUTE_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "statutes")


# The name each Article gives itself, from its own heading. Ingesting an
# Article the reference table has never heard of used to be a silent no-op — the
# UPDATE matched no row, the search index filled up, and `pension_articles` went
# on saying the corpus was two Articles wide.
ARTICLE_NAMES = {
    1: "General Provisions",
    8: "Municipal Employees', Officers', and Officials' Annuity and Benefit Fund "
       "— Cities over 500,000 Inhabitants",
    9: "County Employees' and Officers' Annuity and Benefit Fund "
       "— Counties over 3,000,000 Inhabitants",
    11: "Laborers' and Retirement Board Employees' Annuity and Benefit Fund "
        "— Cities over 500,000 Inhabitants",
    12: "Park Employees' and Retirement Board Employees' Annuity and Benefit Fund "
        "— Cities over 500,000",
    20: "Retirement Systems Reciprocal Act",
    # The rest of the Code. Names taken from each Article's own heading rather
    # than paraphrased, because `system_name` is what the admin UI shows and a
    # paraphrase is a second source of truth.
    2: "General Assembly Retirement System",
    3: "Police Pension Fund — Municipalities",
    4: "Firefighters' Pension Fund",
    5: "Policemen's Annuity and Benefit Fund — Cities over 500,000",
    6: "Firemen's Annuity and Benefit Fund — Cities over 500,000",
    7: "Illinois Municipal Retirement Fund",
    10: "Forest Preserve District Employees' Annuity and Benefit Fund",
    13: "Metropolitan Water Reclamation District Retirement Fund",
    14: "State Employees' Retirement System of Illinois",
    15: "State Universities Retirement System",
    16: "Teachers' Retirement System of the State of Illinois",
    17: "Public School Teachers' Pension and Retirement Fund "
        "— Cities over 500,000 Inhabitants",
    18: "Judges Retirement System of Illinois",
    19: "Closed Funds",
    21: "Social Security Enabling Act",
    22: "Miscellaneous Collateral Provisions",
    23: "Purpose — Savings Provisions — Repeal",
    24: "Public Employees' Deferred Compensation",
}

# Articles whose number is not an integer (1A, 22A, 22B, 22C). They are parsed
# fine as text but cannot be *recorded*: `pension_articles.article_number` is a
# SmallInteger primary key, so there is nowhere to put "22A" until that column
# and the search index's `article` field agree on a wider type.
#
# Listed explicitly and refused loudly rather than left to fail quietly. The
# section-header pattern anchors the article on `(\d+)`, so `(40 ILCS 5/22A-101)`
# matches nothing and every one of these files parses to ZERO sections — which
# `upload_records` accepts happily, leaving `ingestion_status` saying "ingested"
# over an empty article. That is the same silent-collision family as the three
# ILGA formatting bugs in `test_ingestion.py`.
UNSUPPORTED_ARTICLES = ("1a", "22a", "22b", "22c")


async def update_article_status(article_number: int, status: str):
    """Record the ingestion status of an article, creating its row if needed."""
    async with get_admin_session() as db:
        result = await db.execute(
            update(PensionArticle)
            .where(PensionArticle.article_number == article_number)
            .values(ingestion_status=status)
        )
        if result.rowcount == 0:
            db.add(
                PensionArticle(
                    article_number=article_number,
                    system_name=ARTICLE_NAMES.get(
                        article_number, f"Article {article_number}"
                    ),
                    ingestion_status=status,
                )
            )
        await db.commit()


def index_exists() -> bool:
    try:
        get_index_client().get_index(get_settings().azure_search_index_name)
        return True
    except ResourceNotFoundError:
        return False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    # Required, not defaulted: a bare run against the client's index would widen
    # it to all 24 Articles and replace the client's Article 8 with ILGA's.
    parser.add_argument("--corpus", choices=sorted(CORPORA), required=True)
    parser.add_argument("--source", choices=["local", "blob"], default="local",
                        help="where the corpus's primary documents come from (ILGA texts are always local)")
    parser.add_argument("--dry-run", action="store_true", help="convert, check and parse; change nothing")
    parser.add_argument("--allow-shrink", type=int, action="append", default=[], metavar="N",
                        help="let Article N lose indexed sections (repeatable)")
    parser.add_argument("--article", type=int, action="append", default=[], metavar="N",
                        help="only process Article N (repeatable)")
    return parser


def print_report(report: RunReport) -> None:
    print("\n=== Ingestion report")
    for name, reason in report.file_errors:
        print(f"  FILE REFUSED  {name}: {reason}")
    for a in report.articles:
        line = f"  Art {a.article:>2} {a.scope:<8} {a.outcome:<8} sections={a.sections:<4} records={a.records:<5}"
        line += f" +{len(a.added)} -{len(a.missing)}  {', '.join(a.sources)}"
        print(line + (f"\n           reason: {a.reason}" if a.reason else ""))
    if report.unavailable:
        print(f"  Cited but not available: {', '.join(sorted(report.unavailable))}")
    if report.other_acts:
        print(f"  Cited from other Acts (not the Pension Code; not ingested): {', '.join(report.other_acts)}")
    print(f"=== {'FAILED' if report.failed else 'OK'}")


async def main() -> int:
    args = build_parser().parse_args()
    settings = get_settings()
    logger.info("Corpus %s into index '%s'%s", args.corpus, settings.azure_search_index_name,
                " (dry run)" if args.dry_run else "")

    if not args.dry_run and not index_exists():
        logger.info("Creating search index...")
        create_search_index()

    primary = BlobSource.from_settings() if args.source == "blob" else LocalDirSource(STATUTE_DIR)
    sinks = Sinks(
        list_indexed_sections=list_indexed_sections,
        embed=generate_embeddings,
        upload=upload_records,
        prune=prune_stale_records,
        record_status=update_article_status,
    )
    report = await run(
        CORPORA[args.corpus], primary, LocalDirSource(STATUTE_DIR), sinks,
        dry_run=args.dry_run,
        allow_shrink=frozenset(args.allow_shrink),
        only_articles=set(args.article) or None,
    )
    print_report(report)
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
