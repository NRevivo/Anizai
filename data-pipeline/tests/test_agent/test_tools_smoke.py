"""
Sprint 19 T19.1 — Gate B smoke-check for agent/tools/* wrappers.

Runs the 8 wrapper public functions against a real Postgres seeded with
deterministic rows (tests/fixtures/sprint19_vault_seed.sql). Verifies that:
    - Each wrapper returns the documented Python type (list[dict] or
      Optional[dict]).
    - Drift normalizations land in the wrapper's output, not the
      persistence-side shape (audit §3, §4):
        D-1 social_tools.similarity_search rows expose 'similarity', not
            'similarity_score'.
        D-2 explicit values forwarded (e.g., hours, days, limit).
        D-4 source_platforms list normalized to single-platform.

Run only when Postgres is up and the seed has been applied:
    docker compose -f data-pipeline/infrastructure/docker-compose.yml up -d postgres
    docker exec -i anizai-postgres psql -U anizai -d anizai \
        < data-pipeline/tests/fixtures/sprint19_vault_seed.sql
    pytest data-pipeline/tests/test_agent/test_tools_smoke.py -m smoke -v

The seed rows persist across runs by design (no cleanup fixture is
requested; conftest fixtures scope to `test_<run_id>%`, not
`sprint19_smoke_%`). See the seed file's header for full lifetime notes.

Spec references:
    - data-pipeline/docs/sprint19_persistence_audit.md §6 (wrapper API)
    - data-pipeline/docs/sprint19_persistence_audit.md §3, §4 (drift)
    - data-pipeline/docs/agentic_hub_implementation.md (Gate B artifact for T19.1)
"""

from __future__ import annotations

import pytest

from agent.tools import (
    knowledge_tools,
    mapping_tools,
    market_tools,
    social_tools,
)


pytestmark = [pytest.mark.smoke, pytest.mark.usefixtures("db_available")]


# ==========================================================
# Deterministic 1536-dim unit vector e_0 — matches the
# embedding stored in every seed row, so cosine similarity
# is 1.0 and the seed row sorts to the top of HNSW results.
# ==========================================================

@pytest.fixture(scope="module")
def unit_vector_e0() -> list[float]:
    vec = [0.0] * 1536
    vec[0] = 1.0
    return vec


# ==========================================================
# Seed identifiers — copied from sprint19_vault_seed.sql
# ==========================================================

SEED_KV_DOC_ID = "00000000-0000-0000-0000-000000000a01"
SEED_KV_CANONICAL = "sprint19_smoke_kv"

SEED_SV_SOCIAL_ID = "00000000-0000-0000-0000-000000000c01"
SEED_SV_CANONICAL = "sprint19_smoke_sv"

SEED_MV_PM_SOURCE = "polymarket"
SEED_MV_PM_REF = "sprint19_smoke_pm_slug"

SEED_MAP_CANONICAL = "sprint19_smoke_map"
SEED_MAP_PLATFORM_ID = "sprint19_smoke_map_slug"


# ==========================================================
# knowledge_tools
# ==========================================================

def test_knowledge_similarity_search_returns_list_with_similarity_key(unit_vector_e0):
    rows = knowledge_tools.similarity_search(
        query_embedding=unit_vector_e0,
        limit=5,
        source_platforms=["newsapi"],
    )
    assert isinstance(rows, list), "wrapper must return list"
    assert len(rows) >= 1, "seed row should appear in newsapi-filtered HNSW results"
    for row in rows:
        assert isinstance(row, dict)
        assert "similarity" in row, "knowledge_vectors persistence already returns 'similarity'"
        assert "source_platform" in row
        assert "canonical_event_id" in row
        # silver_data_ref required for Researcher drill-down (spec §8.4.1 step 3).
        # Restored to the SELECT in T19.2 — guard against regression.
        assert "silver_data_ref" in row, (
            "knowledge_vectors.similarity_search must expose silver_data_ref "
            "for the Researcher drill-down to knowledge_vault"
        )


def test_knowledge_fetch_full_text_returns_seed_row():
    row = knowledge_tools.fetch_full_text(SEED_KV_DOC_ID)
    assert row is not None, "seed knowledge_vault row should be retrievable by doc_id"
    assert isinstance(row, dict)
    assert row["canonical_event_id"] == SEED_KV_CANONICAL
    assert "full_text_raw" in row


def test_knowledge_fetch_full_text_returns_none_on_miss():
    row = knowledge_tools.fetch_full_text("00000000-0000-0000-0000-deadbeefdead")
    assert row is None


# ==========================================================
# social_tools
# ==========================================================

def test_social_similarity_search_renames_similarity_score_to_similarity(unit_vector_e0):
    """
    Drift D-1: persistence returns 'similarity_score'; wrapper must rename
    to 'similarity' on every row.
    """
    rows = social_tools.similarity_search(
        query_embedding=unit_vector_e0,
        limit=5,
        source_platforms=["polymarket"],
    )
    assert isinstance(rows, list)
    assert len(rows) >= 1, "seed row should appear in polymarket-filtered HNSW results"
    for row in rows:
        assert "similarity" in row, "wrapper must expose canonical 'similarity' key (D-1)"
        assert "similarity_score" not in row, "wrapper must drop persistence-side key (D-1)"


def test_social_fetch_raw_comments_returns_seed_row():
    row = social_tools.fetch_raw_comments(SEED_SV_SOCIAL_ID)
    assert row is not None, "seed social_vault row should be retrievable by social_id"
    assert isinstance(row, dict)
    assert row["canonical_event_id"] == SEED_SV_CANONICAL
    assert "platform_data" in row


def test_social_fetch_raw_comments_returns_none_on_miss():
    row = social_tools.fetch_raw_comments("00000000-0000-0000-0000-deadbeefdead")
    assert row is None


# ==========================================================
# market_tools
# ==========================================================

def test_market_fetch_latest_returns_seed_row():
    row = market_tools.fetch_latest(SEED_MV_PM_SOURCE, SEED_MV_PM_REF)
    assert row is not None, "seed momentum_vault polymarket row should be retrievable"
    assert isinstance(row, dict)
    assert row["source_name"] == SEED_MV_PM_SOURCE
    assert row["external_reference_id"] == SEED_MV_PM_REF
    assert "current_value" in row


def test_market_fetch_latest_returns_none_on_miss():
    row = market_tools.fetch_latest("polymarket", "sprint19_smoke_does_not_exist")
    assert row is None


def test_market_fetch_time_series_returns_list_with_explicit_hours(unit_vector_e0):
    """
    Drift D-2: hours forwarded explicitly (no persistence-side default
    of 24 silently leaking in). Seed row is < 1 day old, so any window
    >= a few hours covers it; we use hours=720 (Sprint 19 spec value).
    """
    rows = market_tools.fetch_time_series(
        source_name=SEED_MV_PM_SOURCE,
        external_reference_id=SEED_MV_PM_REF,
        hours=720,
    )
    assert isinstance(rows, list)
    assert len(rows) >= 1, "seed row should appear in 720h time series"
    assert rows[0]["external_reference_id"] == SEED_MV_PM_REF


def test_market_fetch_fred_anomalies_returns_list_with_explicit_days():
    """Drift D-2: days forwarded explicitly (no default of 7 leaking in)."""
    rows = market_tools.fetch_fred_anomalies(days=14)
    assert isinstance(rows, list)
    assert len(rows) >= 1, "seed FRED row carries anomaly_flags within the 14-day window"
    for row in rows:
        assert row["source_name"] == "fred"
        assert "metadata_extension" in row


# ==========================================================
# mapping_tools
# ==========================================================

def test_mapping_lookup_by_canonical_returns_seed_row():
    rows = mapping_tools.lookup_by_canonical(
        canonical_event_id=SEED_MAP_CANONICAL,
        platform="polymarket",
    )
    assert isinstance(rows, list)
    assert len(rows) == 1, "exactly one mapping row seeded for this canonical_event_id"
    row = rows[0]
    assert row["platform"] == "polymarket"
    assert row["platform_specific_id"] == SEED_MAP_PLATFORM_ID


def test_mapping_lookup_by_canonical_returns_empty_when_unlinked():
    rows = mapping_tools.lookup_by_canonical(
        canonical_event_id="sprint19_smoke_does_not_exist",
    )
    assert rows == []
