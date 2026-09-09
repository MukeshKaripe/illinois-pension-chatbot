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
}


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

    # Article 11 and 12 are the pilot pair — deliberately adjacent and
    # confusable. Article 8 (municipal employees) and 9 (county employees) are
    # the two funds nearest them, so they are what tests whether coverage
    # reasoning survives more than a binary choice. Article 1 and 20 have no
    # members of their own but govern everyone's: Section 1-160 is Tier 2, and
    # Article 20 is the Reciprocal Act.
    target_articles = [1, 8, 9, 11, 12, 20]

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
                logger.warning("No sections found in %s", filepath)
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
