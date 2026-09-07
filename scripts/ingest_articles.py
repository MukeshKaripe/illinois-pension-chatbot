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
from pension_chat.db.session import async_session_factory
from pension_chat.ingestion.chunker import chunk_sections
from pension_chat.ingestion.embedder import generate_embeddings
from pension_chat.ingestion.indexer import create_search_index, get_index_client, upload_records
from pension_chat.ingestion.parser import parse_statute_file

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ingestion_cli")


async def update_article_status(article_number: int, status: str):
    """Update the ingestion status of an article in the database."""
    async with async_session_factory() as db:
        await db.execute(
            update(PensionArticle)
            .where(PensionArticle.article_number == article_number)
            .values(ingestion_status=status)
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

    # Just 11 and 12 for the MVP
    target_articles = [11, 12]

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
