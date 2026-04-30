"""
Gate 1 unit tests for agent/tools/knowledge_tools.py (Sprint 19 T19.1).

Pure-mock tests: patch persistence.knowledge_vectors and
persistence.knowledge_vault at the agent.tools.knowledge_tools binding site.
No DB needed.

Pinned drift behaviours (audit §4):
    D-2: spec values pass through to persistence as kwargs (limit, filters)
    D-4: source_platforms (list) → source_platform (str) translation
"""

from unittest.mock import patch

import pytest

from agent.tools import knowledge_tools


@pytest.fixture
def fake_embedding():
    """1536-dim zero vector — only used to satisfy mocked function arg shape."""
    return [0.0] * 1536


# ==========================================================
# similarity_search
# ==========================================================

def test_similarity_search_passes_spec_args(fake_embedding):
    """
    Spec §8.4.1 step 1 calls similarity_search(limit=15, min_impact_level=2,
    min_reliability=0.3). Wrapper must forward these as keyword args, not
    rely on the persistence default of limit=10 / no filters.
    """
    expected = [{"signal_id": "abc", "similarity": 0.93}]
    with patch.object(
        knowledge_tools.knowledge_vectors,
        "similarity_search",
        return_value=expected,
    ) as mock_search:
        result = knowledge_tools.similarity_search(
            query_embedding=fake_embedding,
            limit=15,
            min_impact_level=2,
            min_reliability=0.3,
        )

    mock_search.assert_called_once_with(
        query_vector=fake_embedding,
        limit=15,
        source_platform=None,
        min_impact_level=2,
        min_reliability=0.3,
    )
    assert result is expected


def test_similarity_search_single_source_platform_unpacked(fake_embedding):
    """source_platforms=['newsapi'] → underlying source_platform='newsapi'."""
    with patch.object(
        knowledge_tools.knowledge_vectors,
        "similarity_search",
        return_value=[],
    ) as mock_search:
        knowledge_tools.similarity_search(
            query_embedding=fake_embedding,
            limit=10,
            source_platforms=["newsapi"],
        )

    _, kwargs = mock_search.call_args
    assert kwargs["source_platform"] == "newsapi"


def test_similarity_search_no_source_platforms_means_none(fake_embedding):
    """Default source_platforms=None → underlying source_platform=None."""
    with patch.object(
        knowledge_tools.knowledge_vectors,
        "similarity_search",
        return_value=[],
    ) as mock_search:
        knowledge_tools.similarity_search(
            query_embedding=fake_embedding,
            limit=10,
        )

    _, kwargs = mock_search.call_args
    assert kwargs["source_platform"] is None


def test_similarity_search_multi_platform_raises(fake_embedding):
    """knowledge_vectors HNSW pre-filter is single-platform — wrapper rejects."""
    with pytest.raises(NotImplementedError):
        knowledge_tools.similarity_search(
            query_embedding=fake_embedding,
            limit=10,
            source_platforms=["newsapi", "arxiv"],
        )


def test_similarity_search_empty_list_treated_as_no_filter(fake_embedding):
    """Empty list is len 0 — neither len>1 nor len==1, so falls through to None."""
    with patch.object(
        knowledge_tools.knowledge_vectors,
        "similarity_search",
        return_value=[],
    ) as mock_search:
        knowledge_tools.similarity_search(
            query_embedding=fake_embedding,
            limit=10,
            source_platforms=[],
        )

    _, kwargs = mock_search.call_args
    assert kwargs["source_platform"] is None


# ==========================================================
# fetch_full_text
# ==========================================================

def test_fetch_full_text_delegates_to_knowledge_vault():
    """fetch_full_text is a 1:1 wrapper around knowledge_vault.fetch_by_doc_id."""
    expected = {"doc_id": "abc-123", "full_text_raw": "the quick brown fox"}
    with patch.object(
        knowledge_tools.knowledge_vault,
        "fetch_by_doc_id",
        return_value=expected,
    ) as mock_fetch:
        result = knowledge_tools.fetch_full_text("abc-123")

    mock_fetch.assert_called_once_with("abc-123")
    assert result is expected


def test_fetch_full_text_returns_none_on_miss():
    with patch.object(
        knowledge_tools.knowledge_vault,
        "fetch_by_doc_id",
        return_value=None,
    ):
        assert knowledge_tools.fetch_full_text("missing-uuid") is None
