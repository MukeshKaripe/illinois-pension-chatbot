"""Add the integrated vectorizer to the live search index, changing nothing else.

    uv run python scripts/enable_vectorizer.py            # show what would change
    uv run python scripts/enable_vectorizer.py --apply    # change it

Reads the index as it is, adds the query vectorizer (indexer.with_vectorizer), and
saves it. Stored documents and vectors are untouched; the change is undone by
removing the vectorizer again. Then set RETRIEVAL_INTEGRATED_VECTORIZER=true.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from azure.core.credentials import AzureKeyCredential  # noqa: E402
from azure.search.documents.indexes import SearchIndexClient  # noqa: E402

from pension_chat.config import get_settings  # noqa: E402
from pension_chat.ingestion.indexer import VECTORIZER_NAME, with_vectorizer  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    client = SearchIndexClient(settings.azure_search_endpoint,
                               AzureKeyCredential(settings.azure_search_api_key))
    index = client.get_index(settings.azure_search_index_name)
    search = index.vector_search
    before = [v.vectorizer_name for v in (search.vectorizers or [])] if search else []
    profiles = [(p.name, p.vectorizer_name) for p in (search.profiles or [])] if search else []
    print(f"index {index.name}: vectorizers {before}, profiles {profiles}")

    updated = with_vectorizer(index)
    print(f"after: vectorizers {[v.vectorizer_name for v in updated.vector_search.vectorizers]}, "
          f"profiles use {VECTORIZER_NAME}")
    if not args.apply:
        print("dry run -- pass --apply to save")
        return 0
    client.create_or_update_index(updated)
    print("saved")
    return 0


if __name__ == "__main__":
    sys.exit(main())
