"""Azure AI Search indexer.

Creates the search index (pension-code-dev) with the §5.2 schema
and uploads chunked+embedded records. Configures:
- Vector search with HNSW
- Semantic ranker
- Filterable metadata fields
"""

from __future__ import annotations

import logging

from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    HnswAlgorithmConfiguration,
    SearchableField,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SemanticConfiguration,
    SemanticField,
    SemanticPrioritizedFields,
    SemanticSearch,
    SimpleField,
    VectorSearch,
    VectorSearchProfile,
)

from pension_chat.config import get_settings
from pension_chat.ingestion.chunker import IndexRecord

logger = logging.getLogger("pension_chat.ingestion.indexer")


def get_index_client() -> SearchIndexClient:
    """Create an Azure AI Search index management client."""
    settings = get_settings()
    return SearchIndexClient(
        endpoint=settings.azure_search_endpoint,
        credential=AzureKeyCredential(settings.azure_search_api_key),
    )


def get_search_client() -> SearchClient:
    """Create an Azure AI Search client for document operations."""
    settings = get_settings()
    return SearchClient(
        endpoint=settings.azure_search_endpoint,
        index_name=settings.azure_search_index_name,
        credential=AzureKeyCredential(settings.azure_search_api_key),
    )


def create_search_index() -> None:
    """Create (or update) the search index with the §5.2 schema.

    Index name: pension-code-dev (from settings)
    """
    settings = get_settings()
    index_client = get_index_client()

    # Define fields matching §5.2
    fields = [
        SimpleField(
            name="id",
            type=SearchFieldDataType.String,
            key=True,
            filterable=True,
        ),
        SearchableField(
            name="content",
            type=SearchFieldDataType.String,
            analyzer_name="en.microsoft",
        ),
        SearchField(
            name="content_vector",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            vector_search_dimensions=3072,  # text-embedding-3-large
            vector_search_profile_name="pension-vector-profile",
        ),
        SearchableField(
            name="subsection_id",
            type=SearchFieldDataType.String,
            filterable=True,
        ),
        SearchableField(
            name="subsection_text",
            type=SearchFieldDataType.String,
        ),
        SearchableField(
            name="section_text",
            type=SearchFieldDataType.String,
        ),
        SimpleField(
            name="chapter",
            type=SearchFieldDataType.String,
            filterable=True,
        ),
        SimpleField(
            name="act",
            type=SearchFieldDataType.String,
            filterable=True,
        ),
        SimpleField(
            name="article",
            type=SearchFieldDataType.String,
            filterable=True,
            facetable=True,
        ),
        SimpleField(
            name="division",
            type=SearchFieldDataType.String,
            filterable=True,
        ),
        SimpleField(
            name="section",
            type=SearchFieldDataType.String,
            filterable=True,
            sortable=True,
        ),
        SearchableField(
            name="section_title",
            type=SearchFieldDataType.String,
            filterable=True,
        ),
        SimpleField(
            name="effective_date",
            type=SearchFieldDataType.String,
            filterable=True,
            sortable=True,
        ),
        SimpleField(
            name="status",
            type=SearchFieldDataType.String,
            filterable=True,
        ),
        SimpleField(
            name="calculation_relevance",
            type=SearchFieldDataType.Collection(SearchFieldDataType.String),
            filterable=True,
            facetable=True,
        ),
        SimpleField(
            name="referenced_sections",
            type=SearchFieldDataType.Collection(SearchFieldDataType.String),
            filterable=True,
        ),
        SimpleField(
            name="referenced_subsections",
            type=SearchFieldDataType.Collection(SearchFieldDataType.String),
            filterable=True,
        ),
        SimpleField(
            name="source_public_act",
            type=SearchFieldDataType.String,
            filterable=True,
        ),
    ]

    # Vector search configuration
    vector_search = VectorSearch(
        algorithms=[
            HnswAlgorithmConfiguration(
                name="pension-hnsw",
            ),
        ],
        profiles=[
            VectorSearchProfile(
                name="pension-vector-profile",
                algorithm_configuration_name="pension-hnsw",
            ),
        ],
    )

    # Semantic search configuration
    semantic_config = SemanticConfiguration(
        name="pension-semantic-config",
        prioritized_fields=SemanticPrioritizedFields(
            title_field=SemanticField(field_name="section_title"),
            content_fields=[
                SemanticField(field_name="content"),
            ],
            keywords_fields=[
                SemanticField(field_name="section"),
                SemanticField(field_name="article"),
            ],
        ),
    )

    semantic_search = SemanticSearch(configurations=[semantic_config])

    # Create the index
    index = SearchIndex(
        name=settings.azure_search_index_name,
        fields=fields,
        vector_search=vector_search,
        semantic_search=semantic_search,
    )

    try:
        index_client.create_or_update_index(index)
        logger.info("✓ Search index '%s' created/updated", settings.azure_search_index_name)
    except Exception as e:
        logger.error("Failed to create search index: %s", e)
        raise


def upload_records(records: list[IndexRecord], batch_size: int = 100) -> int:
    """Upload records to the search index.

    Args:
        records: Embedded IndexRecord objects.
        batch_size: Number of records per upload batch.

    Returns:
        Number of records successfully uploaded.
    """
    search_client = get_search_client()
    total = len(records)
    uploaded = 0

    logger.info("Uploading %d records to search index...", total)

    for i in range(0, total, batch_size):
        batch = records[i:i + batch_size]
        documents = [r.to_dict() for r in batch]

        try:
            result = search_client.upload_documents(documents=documents)
            succeeded = sum(1 for r in result if r.succeeded)
            failed = sum(1 for r in result if not r.succeeded)
            uploaded += succeeded

            if failed > 0:
                for r in result:
                    if not r.succeeded:
                        logger.warning("Failed to upload %s: %s", r.key, r.error_message)

            logger.info(
                "  Uploaded batch %d-%d: %d succeeded, %d failed",
                i + 1, min(i + batch_size, total), succeeded, failed,
            )
        except Exception as e:
            logger.error("Upload batch %d-%d failed: %s", i, i + batch_size, e)
            raise

    logger.info("✓ Uploaded %d/%d records to search index", uploaded, total)
    return uploaded
