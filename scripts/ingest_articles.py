"""CLI script to run the ingestion pipeline.

Parses statute files, chunks them, generates embeddings, and uploads
to Azure AI Search. Also updates the pension_articles ingestion status.

Usage:
    uv run python scripts/ingest_articles.py
"""

import asyncio
import logging
import os
import sys

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dotenv import load_dotenv

load_dotenv()

from azure.core.exceptions import ResourceNotFoundError
from sqlalchemy import update

from pension_chat.config import get_settings
from pension_chat.db.models import PensionArticle
from pension_chat.db.session import get_admin_session
from pension_chat.ingestion.chunker import chunk_sections
from pension_chat.ingestion.embedder import generate_embeddings
from pension_chat.ingestion.indexer import (
    create_search_index,
    get_index_client,
    prune_stale_records,
    upload_records,
)
from pension_chat.ingestion.parser import parse_statute_file

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ingestion_cli")


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


def check_index_exists():
    """Check if the search index exists."""
    settings = get_settings()
    client = get_index_client()
    try:
        client.get_index(settings.azure_search_index_name)
        return True
    except ResourceNotFoundError:
        return False


async def ingest():
    """Run the ingestion pipeline."""
    settings = get_settings()
    logger.info("Starting ingestion pipeline")

    # 1. Ensure index exists
    logger.info("Checking search index '%s'", settings.azure_search_index_name)
    if not check_index_exists():
        logger.info("Creating search index...")
        create_search_index()
    else:
        logger.info("Search index already exists")

    # 2. Process articles
    data_dir = os.path.join(os.path.dirname(__file__), "..", "data", "statutes")

    # Find all article text files
    if not os.path.exists(data_dir):
        logger.error(f"Data directory not found: {data_dir}")
        return

    # The whole Code, as far as an integer article number reaches.
    #
    # Articles 11 and 12 remain the pilot pair — deliberately adjacent and
    # confusable — with 8 (municipal) and 9 (county) as the funds nearest them,
    # and 1 and 20 governing everyone without having members of their own.
    # Widening past those six changes coverage from a five-way choice to a
    # twenty-plus-way one, which is more honest about the Code and is *not*
    # free: Article 10 is the Forest Preserve District fund, which competes
    # directly with Article 12's park employees on any description involving a
    # park. Re-run the `coverage` eval category after changing this list.
    #
    # The four letter-suffixed Articles are in `UNSUPPORTED_ARTICLES` above.
    target_articles = [
        1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13,
        14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24,
    ]

    present = {
        f[len("article_") : -len(".txt")]
        for f in os.listdir(data_dir)
        if f.startswith("article_") and f.endswith(".txt")
    }
    unsupported = sorted(present & set(UNSUPPORTED_ARTICLES))
    if unsupported:
        logger.warning(
            "SKIPPING %d Article(s) with a non-integer number: %s. Their text "
            "parses to zero sections (the section-header pattern anchors the "
            "article on digits) and `pension_articles.article_number` is a "
            "SmallInteger, so there is nowhere to record them. Widening both is "
            "a schema change, not a parser tweak.",
            len(unsupported),
            ", ".join(unsupported),
        )

    for article_num in target_articles:
        filepath = os.path.join(data_dir, f"article_{article_num}.txt")
        if not os.path.exists(filepath):
            logger.warning("File not found for Article %d: %s", article_num, filepath)
            continue

        logger.info("================================================")
        logger.info("Processing Article %d", article_num)

        try:
            # Update DB status
            await update_article_status(article_num, "validating")

            # Step 1: Parse
            logger.info("Parsing %s", filepath)
            sections = parse_statute_file(filepath, article_num)

            if not sections:
                # Never fall through to "ingested" here. Uploading merges by
                # key, so an article that parses to nothing uploads nothing and
                # fails silently -- the status stays at "validating", which is
                # the honest record of what happened.
                logger.error(
                    "Article %d parsed to ZERO sections -- leaving it at "
                    "'validating' rather than claiming it was ingested. The "
                    "header pattern did not match this file's section ids.",
                    article_num,
                )
                continue

            # Step 2: Chunk
            logger.info("Chunking sections...")
            records = chunk_sections(sections)

            # Step 3: Embed
            logger.info("Generating embeddings...")
            records = generate_embeddings(records)

            # Step 4: Index
            logger.info("Uploading to Azure AI Search...")
            uploaded = upload_records(records)

            if uploaded > 0:
                # Anything still indexed under this Article that this run did
                # not produce is text a previous, worse parse left behind.
                prune_stale_records(str(article_num), {r.id for r in records})

                # Mark as fully ingested
                await update_article_status(article_num, "ingested")
                logger.info("✓ Article %d fully ingested", article_num)
            else:
                logger.warning("No records uploaded for Article %d", article_num)

        except Exception as e:
            logger.exception("Failed to ingest Article %d: %s", article_num, e)

    logger.info("================================================")
    logger.info("Ingestion pipeline complete")


if __name__ == "__main__":
    asyncio.run(ingest())
