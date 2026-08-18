"""
Gate 1 unit tests for agent/agents/researcher.py (Sprint 19 T19.2).

Pure-mock tests: patch agent.tools.knowledge_tools at the researcher's
import binding site. No DB, no real embeddings, no fixture data.

Coverage map (spec §8.4.1):
    - Algorithm step 1: spec args forwarded explicitly
      (test_run_passes_spec_filters)
    - Algorithm step 2: composite ranking outranks raw similarity
      (test_run_composite_ranking_outranks_raw_similarity)
    - Algorithm step 3: top-5 drill-down via silver_data_ref
      (test_run_drilldown_uses_silver_data_ref)
    - Algorithm step 4: evidence_weight in [0, 1] (recency clamps to 1.0,
      similarity in [0, 1], impact_norm in [0, 1] → weighted sum stays bounded)
      (test_run_evidence_weight_in_unit_interval)
    - Empty-result short-circuit (test_run_empty_when_zero_rows)
    - source_diversity counts (test_run_source_diversity_counts)
    - Publisher derivation per platform
      (test_run_publisher_telegram_uses_channel_username,
       test_run_publisher_arxiv_returns_constant_arxiv,
       test_run_publisher_newsapi_uses_url_host_stripping_www,
       test_run_publisher_unknown_when_url_missing)
    - Drill-down miss tolerance (test_run_handles_missing_drilldown_gracefully)
    - Recency range as ISO8601 (test_run_recency_range_iso8601)
    - URL derivation, evidence-URL patch 2026-08-18
      (test_run_url_prefers_vault_original_url,
       test_run_url_falls_back_to_content_vitals_when_drilldown_misses,
       test_run_url_falls_back_when_vault_original_url_is_blank,
       test_run_url_is_none_when_no_source_has_one,
       test_run_url_packed_for_every_platform)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from agent.agents import researcher


# ==========================================================
# Fixtures
# ==========================================================

NOW = datetime(2026, 4, 30, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def fake_embedding() -> list[float]:
    """Mocked persistence ignores it; only the shape matters at the wrapper layer."""
    return [0.0] * 1536


def _make_row(
    *,
    signal_id: str = "sig-1",
    canonical_event_id: str = "ev-1",
    silver_data_ref: str = "doc-1",
    source_platform: str = "newsapi",
    similarity: float = 0.9,
    impact_level: int = 4,
    reliability_score: float = 0.8,
    sentiment_score: float = 0.1,
    published_at: datetime = NOW,
    title: str = "Title",
    url: str = "https://www.reuters.com/article/123",
    channel_username: str | None = None,
    executive_summary: str = "Summary text.",
    key_findings: list[str] | None = None,
) -> dict:
    """
    Build one knowledge_vectors-like row matching the wrapper return contract
    (similarity present, JSONB columns as plain dicts).
    """
    enrichment_ai = {
        "impact_level": impact_level,
        "reliability_score": reliability_score,
        "sentiment_score": sentiment_score,
        "executive_summary": executive_summary,
        "key_findings": key_findings if key_findings is not None else ["finding-A"],
    }
    content_vitals = {"title": title, "url": url}
    domain_context = {}
    if channel_username is not None:
        domain_context["channel_username"] = channel_username

    return {
        "signal_id": signal_id,
        "canonical_event_id": canonical_event_id,
        "silver_data_ref": silver_data_ref,
        "source_platform": source_platform,
        "entry_type": "global_signal",
        "published_at": published_at,
        "content_vitals": content_vitals,
        "enrichment_ai": enrichment_ai,
        "domain_context": domain_context,
        "similarity": similarity,
    }


def _make_full_doc(doc_id: str = "doc-1", text: str = "Full body text.") -> dict:
    """Minimal knowledge_vault row shape used by drill-down."""
    return {
        "doc_id": doc_id,
        "canonical_event_id": "ev-1",
        "full_text_raw": text,
        "original_url": "https://reuters.com/article/123",
    }


# ==========================================================
# Algorithm step 1: spec args forwarded
# ==========================================================

def test_run_passes_spec_filters(fake_embedding):
    """
    Spec §8.4.1 step 1: limit=15, min_impact_level=2, min_reliability=0.3.
    The researcher must forward these explicitly (no reliance on persistence
    defaults — drift D-2 from audit §4).
    """
    rows = [_make_row()]
    with (
        patch.object(
            researcher.knowledge_tools, "similarity_search", return_value=rows
        ) as mock_search,
        patch.object(
            researcher.knowledge_tools, "fetch_full_text", return_value=_make_full_doc()
        ),
    ):
        researcher.run(fake_embedding, now=NOW)

    mock_search.assert_called_once_with(
        query_embedding=fake_embedding,
        limit=15,
        min_impact_level=2,
        min_reliability=0.3,
    )


# ==========================================================
# Empty short-circuit
# ==========================================================

def test_run_empty_when_zero_rows(fake_embedding):
    """Zero similarity_search rows → empty=True, articles=[], no drill-down."""
    with (
        patch.object(
            researcher.knowledge_tools, "similarity_search", return_value=[]
        ),
        patch.object(
            researcher.knowledge_tools, "fetch_full_text"
        ) as mock_drill,
    ):
        result = researcher.run(fake_embedding, now=NOW)

    assert result["empty"] is True
    assert result["articles"] == []
    assert result["recency_range"] is None
    assert result["source_diversity"] == {
        "newsapi_count": 0,
        "arxiv_count": 0,
        "telegram_count": 0,
    }
    mock_drill.assert_not_called()


# ==========================================================
# Algorithm step 2: composite ranking
# ==========================================================

def test_run_composite_ranking_outranks_raw_similarity(fake_embedding):
    """
    Composite formula = 0.6*sim + 0.25*(impact/5) + 0.15*recency.

    Two rows:
      A: similarity 0.95, impact 1, published 30 days ago (recency ~ 0.05)
         composite = 0.6*0.95 + 0.25*0.2 + 0.15*0.05 = 0.57 + 0.05 + 0.0075 ≈ 0.6275
      B: similarity 0.80, impact 5, published right now (recency = 1.0)
         composite = 0.6*0.80 + 0.25*1.0 + 0.15*1.0 = 0.48 + 0.25 + 0.15 = 0.88

    Raw similarity says A wins; composite says B wins. The first article in
    the result must be B's signal_id, proving the composite drove the order.
    """
    row_a = _make_row(
        signal_id="A",
        similarity=0.95,
        impact_level=1,
        published_at=NOW - timedelta(days=30),
    )
    row_b = _make_row(
        signal_id="B",
        similarity=0.80,
        impact_level=5,
        published_at=NOW,
    )
    with (
        patch.object(
            researcher.knowledge_tools, "similarity_search", return_value=[row_a, row_b]
        ),
        patch.object(
            researcher.knowledge_tools, "fetch_full_text", return_value=_make_full_doc()
        ),
    ):
        result = researcher.run(fake_embedding, now=NOW)

    assert result["articles"][0]["signal_id"] == "B"
    assert result["articles"][1]["signal_id"] == "A"


# ==========================================================
# evidence_weight bounds
# ==========================================================

def test_run_evidence_weight_in_unit_interval(fake_embedding):
    """
    With similarity≤1, impact_norm≤1, recency≤1 and weights summing to 1.0,
    every evidence_weight must land in [0, 1].
    """
    rows = [
        _make_row(
            signal_id=f"sig-{i}",
            similarity=0.5 + 0.05 * i,
            impact_level=(i % 5) + 1,
            published_at=NOW - timedelta(days=i),
        )
        for i in range(5)
    ]
    with (
        patch.object(
            researcher.knowledge_tools, "similarity_search", return_value=rows
        ),
        patch.object(
            researcher.knowledge_tools, "fetch_full_text", return_value=_make_full_doc()
        ),
    ):
        result = researcher.run(fake_embedding, now=NOW)

    for article in result["articles"]:
        assert 0.0 <= article["evidence_weight"] <= 1.0


# ==========================================================
# Algorithm step 3: drill-down via silver_data_ref
# ==========================================================

def test_run_drilldown_uses_silver_data_ref(fake_embedding):
    """
    Spec §8.4.1 step 3: top-5 drill-down keys off `silver_data_ref` from the
    knowledge_vectors row, NOT signal_id, canonical_event_id, or any other id.
    """
    row = _make_row(
        signal_id="vec-id-not-this-one",
        canonical_event_id="ev-not-this-one",
        silver_data_ref="THE-DOC-ID",
    )
    with (
        patch.object(
            researcher.knowledge_tools, "similarity_search", return_value=[row]
        ),
        patch.object(
            researcher.knowledge_tools, "fetch_full_text", return_value=_make_full_doc()
        ) as mock_drill,
    ):
        researcher.run(fake_embedding, now=NOW)

    mock_drill.assert_called_once_with("THE-DOC-ID")


def test_run_top_5_drilldown_only(fake_embedding):
    """
    similarity_search returns 15 rows; drill-down must fire exactly 5 times,
    matching TOP_K_DRILLDOWN. Articles list length is 5.
    """
    rows = [_make_row(signal_id=f"sig-{i}", silver_data_ref=f"doc-{i}") for i in range(15)]
    with (
        patch.object(
            researcher.knowledge_tools, "similarity_search", return_value=rows
        ),
        patch.object(
            researcher.knowledge_tools, "fetch_full_text", return_value=_make_full_doc()
        ) as mock_drill,
    ):
        result = researcher.run(fake_embedding, now=NOW)

    assert len(result["articles"]) == 5
    assert mock_drill.call_count == 5


# ==========================================================
# source_diversity
# ==========================================================

def test_run_source_diversity_counts(fake_embedding):
    """source_diversity counts top-K rows by source_platform."""
    rows = [
        _make_row(signal_id="n1", source_platform="newsapi"),
        _make_row(signal_id="n2", source_platform="newsapi"),
        _make_row(signal_id="a1", source_platform="arxiv"),
        _make_row(signal_id="t1", source_platform="telegram", channel_username="@alpha"),
        _make_row(signal_id="t2", source_platform="telegram", channel_username="@beta"),
    ]
    with (
        patch.object(
            researcher.knowledge_tools, "similarity_search", return_value=rows
        ),
        patch.object(
            researcher.knowledge_tools, "fetch_full_text", return_value=_make_full_doc()
        ),
    ):
        result = researcher.run(fake_embedding, now=NOW)

    assert result["source_diversity"] == {
        "newsapi_count": 2,
        "arxiv_count": 1,
        "telegram_count": 2,
    }


# ==========================================================
# Publisher derivation
# ==========================================================

def test_run_publisher_telegram_uses_channel_username(fake_embedding):
    """telegram → domain_context.channel_username."""
    row = _make_row(
        source_platform="telegram",
        channel_username="@CoinDesk",
        url="",
    )
    with (
        patch.object(
            researcher.knowledge_tools, "similarity_search", return_value=[row]
        ),
        patch.object(
            researcher.knowledge_tools, "fetch_full_text", return_value=_make_full_doc()
        ),
    ):
        result = researcher.run(fake_embedding, now=NOW)

    assert result["articles"][0]["publisher"] == "@CoinDesk"


def test_run_publisher_arxiv_returns_constant_arxiv(fake_embedding):
    """arxiv → constant 'ArXiv' regardless of URL contents."""
    row = _make_row(source_platform="arxiv", url="https://arxiv.org/abs/2401.99999")
    with (
        patch.object(
            researcher.knowledge_tools, "similarity_search", return_value=[row]
        ),
        patch.object(
            researcher.knowledge_tools, "fetch_full_text", return_value=_make_full_doc()
        ),
    ):
        result = researcher.run(fake_embedding, now=NOW)

    assert result["articles"][0]["publisher"] == "ArXiv"


def test_run_publisher_newsapi_uses_url_host_stripping_www(fake_embedding):
    """newsapi → URL host with leading 'www.' stripped."""
    row = _make_row(
        source_platform="newsapi",
        url="https://www.reuters.com/world/some-article",
    )
    with (
        patch.object(
            researcher.knowledge_tools, "similarity_search", return_value=[row]
        ),
        patch.object(
            researcher.knowledge_tools, "fetch_full_text", return_value=_make_full_doc()
        ),
    ):
        result = researcher.run(fake_embedding, now=NOW)

    assert result["articles"][0]["publisher"] == "reuters.com"


def test_run_publisher_unknown_when_url_missing(fake_embedding):
    """No URL on a non-telegram/non-arxiv row → 'Unknown'."""
    row = _make_row(source_platform="newsapi", url="")
    with (
        patch.object(
            researcher.knowledge_tools, "similarity_search", return_value=[row]
        ),
        patch.object(
            researcher.knowledge_tools, "fetch_full_text", return_value=_make_full_doc()
        ),
    ):
        result = researcher.run(fake_embedding, now=NOW)

    assert result["articles"][0]["publisher"] == "Unknown"


# ==========================================================
# URL derivation (evidence-URL patch, 2026-08-18)
# ==========================================================
# The fixtures make the preference observable on purpose: _make_full_doc's
# original_url has no "www." while _make_row's content_vitals.url does, so a
# test can tell which field was actually read. On live data the two are
# byte-identical (verified 2026-08-18, 2,031/2,031 newsapi+arxiv cloud rows),
# which is exactly why the distinction has to be pinned here instead.

def test_run_url_prefers_vault_original_url(fake_embedding):
    """
    original_url wins over content_vitals.url — it is the schema-enforced
    field (validate_silver_document) and is additionally guarded non-empty at
    the Silver url_guard for newsapi and arxiv.
    """
    row = _make_row(url="https://www.reuters.com/article/123")
    with (
        patch.object(
            researcher.knowledge_tools, "similarity_search", return_value=[row]
        ),
        patch.object(
            researcher.knowledge_tools, "fetch_full_text", return_value=_make_full_doc()
        ),
    ):
        result = researcher.run(fake_embedding, now=NOW)

    assert result["articles"][0]["url"] == "https://reuters.com/article/123"


def test_run_url_falls_back_to_content_vitals_when_drilldown_misses(fake_embedding):
    """
    Drill-down is best-effort. When full_doc is None the Gold-side copy is the
    only URL in scope — dropping the link there would be strictly worse than
    using the unenforced field.
    """
    row = _make_row(url="https://www.reuters.com/article/123")
    with (
        patch.object(
            researcher.knowledge_tools, "similarity_search", return_value=[row]
        ),
        patch.object(
            researcher.knowledge_tools, "fetch_full_text", return_value=None
        ),
    ):
        result = researcher.run(fake_embedding, now=NOW)

    assert result["articles"][0]["url"] == "https://www.reuters.com/article/123"


def test_run_url_falls_back_when_vault_original_url_is_blank(fake_embedding):
    """A present-but-empty original_url must not shadow a usable Gold copy."""
    row = _make_row(url="https://www.reuters.com/article/123")
    full_doc = _make_full_doc()
    full_doc["original_url"] = "   "
    with (
        patch.object(
            researcher.knowledge_tools, "similarity_search", return_value=[row]
        ),
        patch.object(
            researcher.knowledge_tools, "fetch_full_text", return_value=full_doc
        ),
    ):
        result = researcher.run(fake_embedding, now=NOW)

    assert result["articles"][0]["url"] == "https://www.reuters.com/article/123"


def test_run_url_is_none_when_no_source_has_one(fake_embedding):
    """
    None, never "" — EvidenceItem.url is Optional[str] and the frontend
    branches on presence.
    """
    row = _make_row(url="")
    full_doc = _make_full_doc()
    full_doc["original_url"] = ""
    with (
        patch.object(
            researcher.knowledge_tools, "similarity_search", return_value=[row]
        ),
        patch.object(
            researcher.knowledge_tools, "fetch_full_text", return_value=full_doc
        ),
    ):
        result = researcher.run(fake_embedding, now=NOW)

    assert result["articles"][0]["url"] is None


@pytest.mark.parametrize("source_platform", ["newsapi", "arxiv", "telegram"])
def test_run_url_packed_for_every_platform(fake_embedding, source_platform: str):
    """
    The researcher reports what the vault holds for ALL platforms; the
    newsapi/arxiv gate is rate_evidence's job (rate_evidence.
    PLATFORMS_WITH_PUBLIC_URL). Packing telegram here is deliberate — if this
    ever starts returning None for telegram, the gate has leaked upstream.
    """
    row = _make_row(source_platform=source_platform, channel_username="@CoinDesk")
    with (
        patch.object(
            researcher.knowledge_tools, "similarity_search", return_value=[row]
        ),
        patch.object(
            researcher.knowledge_tools, "fetch_full_text", return_value=_make_full_doc()
        ),
    ):
        result = researcher.run(fake_embedding, now=NOW)

    assert result["articles"][0]["url"] == "https://reuters.com/article/123"


# ==========================================================
# Drill-down miss tolerance
# ==========================================================

def test_run_handles_missing_drilldown_gracefully(fake_embedding):
    """
    If knowledge_vault.fetch_by_doc_id returns None (e.g., row archived after
    the vector was indexed but before this query), the article still packs
    with full_text_snippet="" rather than raising.
    """
    row = _make_row()
    with (
        patch.object(
            researcher.knowledge_tools, "similarity_search", return_value=[row]
        ),
        patch.object(
            researcher.knowledge_tools, "fetch_full_text", return_value=None
        ),
    ):
        result = researcher.run(fake_embedding, now=NOW)

    article = result["articles"][0]
    assert article["full_text_snippet"] == ""
    assert article["signal_id"] == "sig-1"


# ==========================================================
# recency_range
# ==========================================================

def test_run_recency_range_iso8601(fake_embedding):
    """
    recency_range.oldest / newest must be ISO8601 strings derived from
    the top-K rows' published_at timestamps.
    """
    oldest = NOW - timedelta(days=10)
    newest = NOW
    rows = [
        _make_row(signal_id="old", published_at=oldest),
        _make_row(signal_id="new", published_at=newest),
    ]
    with (
        patch.object(
            researcher.knowledge_tools, "similarity_search", return_value=rows
        ),
        patch.object(
            researcher.knowledge_tools, "fetch_full_text", return_value=_make_full_doc()
        ),
    ):
        result = researcher.run(fake_embedding, now=NOW)

    rng = result["recency_range"]
    assert rng is not None
    assert rng["oldest"] == oldest.isoformat()
    assert rng["newest"] == newest.isoformat()
