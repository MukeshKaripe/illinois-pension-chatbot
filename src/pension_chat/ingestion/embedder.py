"""Embedding generator using text-embedding-3-large.

Generates vector embeddings for index records using the Azure OpenAI
embedding deployment specified in config.
"""

from __future__ import annotations

import logging

from openai import AzureOpenAI

from pension_chat.config import get_settings
from pension_chat.ingestion.chunker import IndexRecord

logger = logging.getLogger("pension_chat.ingestion.embedder")


def get_embedding_client() -> AzureOpenAI:
    """Create an Azure OpenAI client for embeddings."""
    settings = get_settings()
    return AzureOpenAI(
        api_key=settings.azure_openai_api_key.get_secret_value(),
        api_version=settings.azure_openai_api_version,
        azure_endpoint=settings.azure_openai_endpoint,
    )


def generate_embeddings(
    records: list[IndexRecord],
    batch_size: int = 16,
) -> list[IndexRecord]:
    """Generate embeddings for all records using text-embedding-3-large.

    Modifies records in-place and returns them.

    Args:
        records: List of IndexRecord objects to embed.
        batch_size: Number of texts to embed per API call.
    """
    settings = get_settings()
    client = get_embedding_client()
    deployment = settings.azure_openai_embedding_deployment

    total = len(records)
    logger.info("Generating embeddings for %d records (batch_size=%d)", total, batch_size)

    for i in range(0, total, batch_size):
        batch = records[i:i + batch_size]
        texts = [r.content[:8191] for r in batch]  # text-embedding-3-large max input

        try:
            response = client.embeddings.create(
                input=texts,
                model=deployment,
            )

            for j, embedding_data in enumerate(response.data):
                batch[j].content_vector = embedding_data.embedding

            logger.info(
                "  Embedded batch %d-%d of %d",
                i + 1,
                min(i + batch_size, total),
                total,
            )
        except Exception as e:
            logger.error("Embedding batch %d-%d failed: %s", i, i + batch_size, e)
            raise

    logger.info("✓ All %d records embedded", total)
    return records
