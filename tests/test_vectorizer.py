"""The search service embeds the query itself (its integrated vectorizer).

One round trip instead of two: the embedding call before every search was about
half a second from this laptop. The vectorizer is an optimisation, never a
dependency -- if the service cannot embed (a rotated key, a quota), the search
falls back to embedding the query here, as before, rather than coming back empty.
"""

from __future__ import annotations

import pytest

from pension_chat.agent import retrieval


class _FakeClient:
    def __init__(self, fail_vectorizer: bool = False) -> None:
        self.sent: list[dict] = []
        self.fail_vectorizer = fail_vectorizer

    async def search(self, **kwargs):
        self.sent.append(kwargs)
        kinds = [type(q).__name__ for q in kwargs.get("vector_queries") or []]
        if self.fail_vectorizer and "VectorizableTextQuery" in kinds:
            raise RuntimeError("vectorizer could not reach the embedding deployment")

        async def docs():
            yield {"section": "8-138", "content": "text"}
        return docs()


@pytest.fixture
def vectorizer_on(monkeypatch):
    monkeypatch.setattr(retrieval.get_settings(), "retrieval_integrated_vectorizer", True)


async def test_with_the_vectorizer_the_query_is_sent_as_text(monkeypatch, vectorizer_on):
    client = _FakeClient()
    monkeypatch.setattr(retrieval, "get_async_search_client", lambda: client)
    docs = await retrieval._search_one("tier 2 age", None, None, 10)
    (query,) = client.sent[0]["vector_queries"]
    assert type(query).__name__ == "VectorizableTextQuery" and query.text == "tier 2 age"
    assert docs == [{"section": "8-138", "content": "text"}]


async def test_a_failing_vectorizer_falls_back_to_embedding_here(monkeypatch, vectorizer_on):
    client = _FakeClient(fail_vectorizer=True)
    monkeypatch.setattr(retrieval, "get_async_search_client", lambda: client)

    async def embed(queries):
        return [[0.1, 0.2] for _ in queries]

    monkeypatch.setattr(retrieval, "_embed", embed)
    docs = await retrieval._search_one("tier 2 age", None, None, 10)
    assert docs == [{"section": "8-138", "content": "text"}]
    (query,) = client.sent[-1]["vector_queries"]
    assert type(query).__name__ == "VectorizedQuery" and query.vector == [0.1, 0.2]


async def test_with_the_vectorizer_no_embedding_call_is_made(monkeypatch, vectorizer_on):
    called = []

    async def embed(queries):
        called.append(queries)
        return [None for _ in queries]

    monkeypatch.setattr(retrieval, "_embed", embed)
    assert await retrieval._query_vectors(["a", "b"]) == [None, None]
    assert called == []


async def test_without_the_vectorizer_queries_are_embedded_here(monkeypatch):
    monkeypatch.setattr(retrieval.get_settings(), "retrieval_integrated_vectorizer", False)

    async def embed(queries):
        return [[1.0] for _ in queries]

    monkeypatch.setattr(retrieval, "_embed", embed)
    assert await retrieval._query_vectors(["a"]) == [[1.0]]


def test_the_live_index_gains_only_the_vectorizer():
    # The script reads the live index and adds the vectorizer to it, rather than
    # re-applying our whole definition over the client's index.
    from azure.search.documents.indexes.models import (
        HnswAlgorithmConfiguration,
        SearchIndex,
        VectorSearch,
        VectorSearchProfile,
    )

    from pension_chat.ingestion.indexer import VECTORIZER_NAME, with_vectorizer

    live = SearchIndex(name="pension-code-prod", fields=[], vector_search=VectorSearch(
        algorithms=[HnswAlgorithmConfiguration(name="pension-hnsw")],
        profiles=[VectorSearchProfile(name="pension-vector-profile",
                                      algorithm_configuration_name="pension-hnsw")]))
    updated = with_vectorizer(live)
    (vectorizer,) = updated.vector_search.vectorizers
    assert vectorizer.vectorizer_name == VECTORIZER_NAME
    assert vectorizer.parameters.deployment_name == "text-embedding-3-large"
    assert updated.vector_search.profiles[0].vectorizer_name == VECTORIZER_NAME
    assert updated.vector_search.algorithms[0].name == "pension-hnsw"     # untouched
    assert len(with_vectorizer(updated).vector_search.vectorizers) == 1     # idempotent


async def test_a_semantic_quota_failure_is_not_blamed_on_the_vectorizer(monkeypatch, vectorizer_on):
    # The semantic ranker fails first (its own quota); the vectorizer still works,
    # so nothing is embedded here and the search drops only the ranker.
    sent: list[dict] = []

    class Client:
        async def search(self, **kwargs):
            sent.append(kwargs)
            if kwargs.get("query_type") == "semantic":
                raise RuntimeError("semantic quota exhausted")

            async def docs():
                yield {"section": "1-160"}
            return docs()

    async def embed(queries):
        raise AssertionError("embedded here although the vectorizer worked")

    monkeypatch.setattr(retrieval, "get_async_search_client", lambda: Client())
    monkeypatch.setattr(retrieval, "_embed", embed)
    assert await retrieval._search_one("tier 2", None, None, 5) == [{"section": "1-160"}]
    assert len(sent) == 2
    assert type(sent[-1]["vector_queries"][0]).__name__ == "VectorizableTextQuery"
