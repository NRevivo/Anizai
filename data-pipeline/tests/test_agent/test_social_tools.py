"""
Gate 1 unit tests for agent/tools/social_tools.py (Sprint 19 T19.1).

Pinned drift behaviours (audit §4):
    D-1: every returned row has key `similarity` (renamed from
         persistence-side `similarity_score`).
    D-3: parameter shape translation
            min_impact_level: Optional[int] → min_impact: int (None → 1)
            min_reliability:  Optional[float] → min_reliability: float (None → 0.0)
            source_platforms: Optional[list] → platforms: Optional[list]
"""

from unittest.mock import patch

import pytest

from agent.tools import social_tools


@pytest.fixture
def fake_embedding():
    return [0.0] * 1536


# ==========================================================
# similarity_search — drift-3 parameter translation
# ==========================================================

def test_similarity_search_translates_none_filters_to_sentinels(fake_embedding):
    """
    None filters at the wrapper API → persistence sentinel values:
        min_impact_level=None → min_impact=1
        min_reliability=None  → min_reliability=0.0
    """
    with patch.object(
        social_tools.social_vectors,
        "similarity_search",
        return_value=[],
    ) as mock_search:
        social_tools.similarity_search(
            query_embedding=fake_embedding,
            limit=10,
        )

    mock_search.assert_called_once_with(
        query_vector=fake_embedding,
        limit=10,
        min_impact=1,
        platforms=None,
        min_reliability=0.0,
    )


def test_similarity_search_passes_explicit_filters(fake_embedding):
    """Explicit filter values pass through unchanged."""
    with patch.object(
        social_tools.social_vectors,
        "similarity_search",
        return_value=[],
    ) as mock_search:
        social_tools.similarity_search(
            query_embedding=fake_embedding,
            limit=10,
            min_impact_level=3,
            min_reliability=0.4,
            source_platforms=["polymarket"],
        )

    mock_search.assert_called_once_with(
        query_vector=fake_embedding,
        limit=10,
        min_impact=3,
        platforms=["polymarket"],
        min_reliability=0.4,
    )


# ==========================================================
# similarity_search — drift-1 return-key normalization
# ==========================================================

def test_similarity_search_renames_similarity_score_to_similarity(fake_embedding):
    """Every returned row has 'similarity', not 'similarity_score'."""
    persistence_rows = [
        {"signal_id": "row-a", "similarity_score": 0.92, "source_platform": "polymarket"},
        {"signal_id": "row-b", "similarity_score": 0.71, "source_platform": "hackernews"},
    ]
    with patch.object(
        social_tools.social_vectors,
        "similarity_search",
        return_value=persistence_rows,
    ):
        rows = social_tools.similarity_search(
            query_embedding=fake_embedding,
            limit=10,
        )

    assert len(rows) == 2
    for row in rows:
        assert "similarity" in row, "wrapper must expose canonical key 'similarity'"
        assert "similarity_score" not in row, "wrapper must drop persistence-side key"
    assert rows[0]["similarity"] == pytest.approx(0.92)
    assert rows[1]["similarity"] == pytest.approx(0.71)


def test_similarity_search_handles_empty_result(fake_embedding):
    """Empty persistence result → empty wrapper result, no rename to apply."""
    with patch.object(
        social_tools.social_vectors,
        "similarity_search",
        return_value=[],
    ):
        rows = social_tools.similarity_search(
            query_embedding=fake_embedding,
            limit=10,
        )
    assert rows == []


# ==========================================================
# fetch_raw_comments
# ==========================================================

def test_fetch_raw_comments_delegates_to_social_vault():
    expected = {"social_id": "soc-1", "platform_data": {"raw_comments": []}}
    with patch.object(
        social_tools.social_vault,
        "fetch_by_social_id",
        return_value=expected,
    ) as mock_fetch:
        result = social_tools.fetch_raw_comments("soc-1")

    mock_fetch.assert_called_once_with("soc-1")
    assert result is expected


def test_fetch_raw_comments_returns_none_on_miss():
    with patch.object(
        social_tools.social_vault,
        "fetch_by_social_id",
        return_value=None,
    ):
        assert social_tools.fetch_raw_comments("missing") is None
