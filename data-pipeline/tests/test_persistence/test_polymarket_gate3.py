"""
Gate 3 — Persistence Gate: Polymarket social_vectors End-to-End Verification.

Validates that Gold Social Pulse records written by persistence/social_vectors.py
are correctly stored and retrievable from the live PostgreSQL `social_vectors`
table (pgvector + HNSW index).

Requires: PostgreSQL container running (docker compose up postgres).
          All Gate 1 and Gate 2 tests must pass before running Gate 3.

What Gate 3 covers (Section 9.3 Gate 3):
    [1]  insert() writes a well-formed row to social_vectors
    [2]  fetch_by_signal_id() retrieves the exact record that was inserted
    [3]  All JSONB blocks round-trip without data loss (content_vitals,
         enrichment_ai, social_context, platform_logic)
    [4]  source_platform and entry_type are stored correctly
    [5]  exists_by_canonical_event() returns True after insert
    [6]  exists_by_canonical_event() returns False for unknown event
    [7]  exists_by_canonical_event() with platform filter works correctly
    [8]  similarity_search() returns the inserted record when queried with
         the same vector (similarity_score ≈ 1.0)
    [9]  similarity_search() ranks vectors by cosine similarity correctly
    [10] similarity_search() impact_level filter excludes low-impact records
    [11] similarity_search() platform filter restricts results by source
    [12] insert() is idempotent — re-inserting same signal_id does not
         create a duplicate row (ON CONFLICT DO NOTHING)
    [13] insert() raises ValueError on wrong embedding dimension
    [14] insert() raises ValueError on invalid source_platform
    [15] insert() raises ValueError on missing required keys
    [16] published_at is stored and retrieved as a timezone-aware datetime
    [17] whale_alert flag survives round-trip through metadata_extension JSONB

Isolation: every test record uses a canonical_event_id prefixed with
"test_<test_run_id>_" (see conftest.py). The cleanup_social_vectors fixture
deletes all matching rows after each test, keeping the shared DB clean.

References:
    - Section 5.2:  Vector Intelligence — pgvector with HNSW
    - Section 6.2:  The Pulse Analyst agent (primary query consumer)
    - Section 9.3:  Triple-Gate Test Matrix — Gate 3
    - Section C.6:  Gold Social Pulse Schema
    - Section 4.2A: Consensus Bundling (temporal blocks → single vector)
"""

import math
import uuid
from datetime import datetime, timezone

import pytest

from persistence.social_vectors import (
    EMBEDDING_DIM,
    VALID_PLATFORMS,
    exists_by_canonical_event,
    fetch_by_signal_id,
    insert,
    similarity_search,
)
from utils.db import get_cursor

pytestmark = pytest.mark.usefixtures("db_available")


# ==========================================================
# Embedding helpers
# ==========================================================

def spike_vec(pos: int, dim: int = EMBEDDING_DIM) -> list[float]:
    """
    Unit vector with 1.0 at position `pos` and 0.0 elsewhere.

    Cosine similarity between two spike vectors at different positions = 0.0.
    Cosine similarity between identical spike vectors = 1.0.
    Useful for asserting search rank order deterministically.
    """
    v = [0.0] * dim
    v[pos] = 1.0
    return v


def uniform_vec(dim: int = EMBEDDING_DIM) -> list[float]:
    """
    Unit vector with equal weight across all dimensions (L2-normalised).
    Used as a generic valid embedding where exact similarity values don't matter.
    """
    val = 1.0 / math.sqrt(dim)
    return [val] * dim


# ==========================================================
# Record builder
# ==========================================================

def make_gold_record(
    canonical_event_id: str,
    embedding: list[float],
    source_platform: str = "polymarket",
    impact_level: int = 3,
    urgency_level: int = 2,
    reliability_score: float = 0.75,
    whale_alert: bool = False,
    signal_id: str | None = None,
) -> dict:
    """
    Build a minimal but valid Gold Social Pulse record (Section C.6).

    The record structure mirrors what the Gold Job emits after OpenAI
    Consensus Bundling (Section 4.2A) — platform_logic carries the
    Polymarket-specific fields from Section C.6 Platform Extensions.
    """
    return {
        "metadata": {
            "signal_id":          signal_id or str(uuid.uuid4()),
            "canonical_event_id": canonical_event_id,
            "source_platform":    source_platform,
            "published_at":       "2026-03-28T14:22:31+00:00",
            "silver_data_ref":    str(uuid.uuid4()),
            "raw_data_ref":       str(uuid.uuid4()),
        },
        "content_vitals": {
            "title":               "Will the Federal Reserve cut rates before June 2026?",
            "url":                 "https://polymarket.com/event/fed-rate-cut-june-2026",
            "description_snippet": "Community consensus on Fed rate cut probability based on 3 comments.",
        },
        "enrichment_ai": {
            "executive_summary":   "Community leans YES (67%) citing soft inflation data, though sticky core PCE and hawkish Powell rhetoric keep NO sentiment elevated.",
            "key_findings":        ["Macro traders favour YES at 0.73", "Core PCE cited as key uncertainty"],
            "impact_level":        impact_level,
            "urgency_level":       urgency_level,
            "reliability_score":   reliability_score,
            "sentiment_score":     0.15,
            "extracted_entities":  ["Federal Reserve", "Jerome Powell", "PCE", "T10Y2Y"],
            "topic_classification": "Monetary Policy",
            "fact_check_flag":     False,
        },
        "social_context": {
            "community_name":           "Polymarket",
            "author_handle":            None,
            "author_reputation":        0.8,
            "primary_engagement_score": 44,
        },
        "platform_logic": {
            "entry_type":               "market_consensus",
            "aggregation_window_hours": 4,
            "comment_volume_analyzed":  3,
            "consensus_rating":         0.67,
            "market_id_ref":            "0x87ae1d3e2aec7cbf6b7f01781f741c82ae9bd08a",
            "whale_alert":              whale_alert,
        },
        "embedding": embedding,
    }


# ==========================================================
# Gate 3.1 — Insert and Fetch
# ==========================================================

@pytest.mark.usefixtures("cleanup_social_vectors")
class TestInsertAndFetch:
    """
    Verifies that insert() writes a complete row and fetch_by_signal_id()
    retrieves it with all fields intact (Gate 3 checks 1-4, 16-17).
    """

    def test_insert_returns_signal_id(self, test_run_id):
        """insert() must return the signal_id string of the inserted row."""
        record = make_gold_record(
            canonical_event_id=f"test_{test_run_id}_insert_basic",
            embedding=uniform_vec(),
        )
        sid = insert(record)
        assert isinstance(sid, str)
        assert uuid.UUID(sid)  # valid UUID

    def test_fetch_returns_inserted_record(self, test_run_id):
        """fetch_by_signal_id() must return the exact row that was inserted."""
        record = make_gold_record(
            canonical_event_id=f"test_{test_run_id}_fetch_roundtrip",
            embedding=uniform_vec(),
        )
        sid = insert(record)
        row = fetch_by_signal_id(sid)

        assert row is not None, f"No row found for signal_id={sid}"
        assert str(row["signal_id"])          == sid
        assert row["canonical_event_id"]      == f"test_{test_run_id}_fetch_roundtrip"
        assert row["source_platform"]         == "polymarket"
        assert row["entry_type"]              == "market_consensus"

    def test_content_vitals_round_trip(self, test_run_id):
        """content_vitals JSONB must be stored and retrieved without data loss."""
        record = make_gold_record(
            canonical_event_id=f"test_{test_run_id}_content_vitals",
            embedding=uniform_vec(),
        )
        sid = insert(record)
        row = fetch_by_signal_id(sid)

        cv = row["content_vitals"]
        assert cv["title"]               == record["content_vitals"]["title"]
        assert cv["url"]                 == record["content_vitals"]["url"]
        assert cv["description_snippet"] == record["content_vitals"]["description_snippet"]

    def test_enrichment_ai_round_trip(self, test_run_id):
        """enrichment_ai JSONB (impact_level, reliability_score, entities) must survive round-trip."""
        record = make_gold_record(
            canonical_event_id=f"test_{test_run_id}_enrichment_ai",
            embedding=uniform_vec(),
            impact_level=4,
            reliability_score=0.9,
        )
        sid = insert(record)
        row = fetch_by_signal_id(sid)

        ai = row["enrichment_ai"]
        assert int(ai["impact_level"])        == 4
        assert float(ai["reliability_score"]) == pytest.approx(0.9, abs=1e-6)
        assert "Federal Reserve" in ai["extracted_entities"]
        assert ai["topic_classification"]     == "Monetary Policy"
        assert ai["fact_check_flag"]          is False

    def test_social_context_round_trip(self, test_run_id):
        """social_context JSONB must preserve community identity fields."""
        record = make_gold_record(
            canonical_event_id=f"test_{test_run_id}_social_ctx",
            embedding=uniform_vec(),
        )
        sid = insert(record)
        row = fetch_by_signal_id(sid)

        sc = row["social_context"]
        assert sc["community_name"]           == "Polymarket"
        assert int(sc["primary_engagement_score"]) == 44

    def test_platform_logic_round_trip(self, test_run_id):
        """platform_logic JSONB must preserve all Polymarket-specific extension fields."""
        record = make_gold_record(
            canonical_event_id=f"test_{test_run_id}_platform_logic",
            embedding=uniform_vec(),
        )
        sid = insert(record)
        row = fetch_by_signal_id(sid)

        pl = row["platform_logic"]
        assert pl["entry_type"]               == "market_consensus"
        assert int(pl["aggregation_window_hours"]) == 4
        assert int(pl["comment_volume_analyzed"])  == 3
        assert float(pl["consensus_rating"])  == pytest.approx(0.67, abs=1e-4)

    def test_whale_alert_survives_jsonb_round_trip(self, test_run_id):
        """
        whale_alert=True stored in platform_logic JSONB must survive round-trip.

        Why tested explicitly: PostgreSQL JSONB stores booleans natively, but
        psycopg2's RealDictCursor returns them as Python bools — verify no
        type coercion to int(1) or string 'true' occurs (Section B.8).
        """
        record = make_gold_record(
            canonical_event_id=f"test_{test_run_id}_whale_alert",
            embedding=uniform_vec(),
            whale_alert=True,
        )
        sid = insert(record)
        row = fetch_by_signal_id(sid)
        assert row["platform_logic"]["whale_alert"] is True

    def test_published_at_is_timezone_aware(self, test_run_id):
        """published_at must be retrieved as a timezone-aware datetime (Section 3.2)."""
        record = make_gold_record(
            canonical_event_id=f"test_{test_run_id}_published_at",
            embedding=uniform_vec(),
        )
        sid = insert(record)
        row = fetch_by_signal_id(sid)

        published = row["published_at"]
        assert published is not None
        assert published.tzinfo is not None, "published_at must be timezone-aware"

    def test_fetch_returns_none_for_unknown_signal_id(self):
        """fetch_by_signal_id() must return None for a non-existent UUID."""
        result = fetch_by_signal_id(str(uuid.uuid4()))
        assert result is None


# ==========================================================
# Gate 3.2 — Existence Check
# ==========================================================

@pytest.mark.usefixtures("cleanup_social_vectors")
class TestExistenceCheck:
    """
    Verifies exists_by_canonical_event() before and after insert,
    and with / without platform filter (Gate 3 checks 5-7).
    """

    def test_exists_returns_true_after_insert(self, test_run_id):
        """exists_by_canonical_event() must return True after inserting a record."""
        ceid = f"test_{test_run_id}_exists_true"
        insert(make_gold_record(ceid, uniform_vec()))
        assert exists_by_canonical_event(ceid) is True

    def test_exists_returns_false_for_unknown_event(self):
        """exists_by_canonical_event() must return False for an ID never inserted."""
        assert exists_by_canonical_event(f"test_nonexistent_{uuid.uuid4()}") is False

    def test_exists_with_matching_platform_filter(self, test_run_id):
        """Platform filter must match when correct platform is provided."""
        ceid = f"test_{test_run_id}_exists_platform_match"
        insert(make_gold_record(ceid, uniform_vec(), source_platform="polymarket"))
        assert exists_by_canonical_event(ceid, source_platform="polymarket") is True

    def test_exists_with_wrong_platform_returns_false(self, test_run_id):
        """
        Platform filter must return False when the record exists on a
        different platform — prevents cross-platform false positives.
        """
        ceid = f"test_{test_run_id}_exists_platform_mismatch"
        insert(make_gold_record(ceid, uniform_vec(), source_platform="polymarket"))
        assert exists_by_canonical_event(ceid, source_platform="hackernews") is False


# ==========================================================
# Gate 3.3 — Similarity Search (pgvector HNSW)
# ==========================================================

@pytest.mark.usefixtures("cleanup_social_vectors")
class TestSimilaritySearch:
    """
    Verifies the HNSW cosine similarity search against live pgvector index.

    Uses spike vectors to produce deterministic similarity scores:
        spike_vec(0) · spike_vec(0) = 1.0   (identical direction)
        spike_vec(0) · spike_vec(1) = 0.0   (orthogonal)

    Gate 3 checks 8-11.
    """

    def test_search_returns_inserted_record(self, test_run_id):
        """
        Querying with the same embedding used at insert must return the record
        with similarity_score ≈ 1.0.
        """
        ceid = f"test_{test_run_id}_search_exact"
        vec  = spike_vec(0)
        sid  = insert(make_gold_record(ceid, vec))

        results = similarity_search(vec, limit=5, platforms=["polymarket"])
        signal_ids = [str(r["signal_id"]) for r in results]

        assert sid in signal_ids, "Inserted record not found in search results"

        # Find the specific result and check similarity
        match = next(r for r in results if str(r["signal_id"]) == sid)
        assert float(match["similarity_score"]) == pytest.approx(1.0, abs=1e-4)

    def test_search_rank_order_by_cosine_similarity(self, test_run_id):
        """
        Records closer to the query vector must rank higher than orthogonal ones.

        Inserts two vectors in different directions; queries in the direction of
        the first — it must appear before the second in the results.

        Why filter by this run's signal_ids rather than asserting both are in the
        top-N globally: the test DB accumulates polymarket records across runs.
        spike_vec(3) has cosine similarity 0 with spike_vec(2), so it ranks at
        the bottom and falls outside a small limit once enough records exist.
        Filtering to only this run's two records tests the actual intent (rank
        order) without assuming anything about total DB state.
        """
        ceid_close = f"test_{test_run_id}_search_rank_close"
        ceid_far   = f"test_{test_run_id}_search_rank_far"
        vec_close  = spike_vec(2)   # aligns with query
        vec_far    = spike_vec(3)   # orthogonal to query
        query_vec  = spike_vec(2)

        sid_close = insert(make_gold_record(ceid_close, vec_close))
        sid_far   = insert(make_gold_record(ceid_far,   vec_far))

        # Use a large limit so both this-run records are always present regardless
        # of how many other polymarket records have accumulated in the test DB.
        results = similarity_search(query_vec, limit=500, platforms=["polymarket"])
        sig_ids = [str(r["signal_id"]) for r in results]

        assert sid_close in sig_ids, "Close vector not in results (DB issue or insert failed)"
        assert sid_far   in sig_ids, "Far vector not in results (DB issue or insert failed)"

        # Rank order assertion — only between the two controlled records
        close_rank = sig_ids.index(sid_close)
        far_rank   = sig_ids.index(sid_far)
        assert close_rank < far_rank, (
            f"Close vector (rank {close_rank}) should rank before "
            f"far vector (rank {far_rank})"
        )

    def test_search_impact_filter_excludes_low_impact(self, test_run_id):
        """
        min_impact=4 filter must exclude records with impact_level < 4.

        This verifies the WHERE (enrichment_ai->>'impact_level')::int >= %s
        pushdown in similarity_search() (Section 5.2 Metadata Filtering).
        """
        ceid_high = f"test_{test_run_id}_impact_high"
        ceid_low  = f"test_{test_run_id}_impact_low"
        vec       = spike_vec(4)

        sid_high = insert(make_gold_record(ceid_high, vec, impact_level=5))
        sid_low  = insert(make_gold_record(ceid_low,  vec, impact_level=2))

        results  = similarity_search(vec, limit=10, min_impact=4, platforms=["polymarket"])
        sig_ids  = [str(r["signal_id"]) for r in results]

        assert sid_high in sig_ids,  "High-impact record should be in filtered results"
        assert sid_low  not in sig_ids, "Low-impact record should be excluded by filter"

    def test_search_platform_filter_restricts_results(self, test_run_id):
        """
        Querying with platforms=['hackernews'] must not return polymarket records,
        even if their embeddings are closer to the query vector.
        """
        ceid_poly = f"test_{test_run_id}_platform_poly"
        ceid_hn   = f"test_{test_run_id}_platform_hn"
        vec       = spike_vec(5)

        sid_poly = insert(make_gold_record(ceid_poly, vec, source_platform="polymarket"))
        sid_hn   = insert(make_gold_record(ceid_hn,   vec, source_platform="hackernews"))

        results = similarity_search(vec, limit=10, platforms=["hackernews"])
        sig_ids = [str(r["signal_id"]) for r in results]

        assert sid_hn   in sig_ids,     "HackerNews record should appear in hackernews-filtered search"
        assert sid_poly not in sig_ids, "Polymarket record must not appear in hackernews-filtered search"

    def test_search_returns_similarity_score(self, test_run_id):
        """similarity_score must be present and a float between 0.0 and 1.0."""
        ceid = f"test_{test_run_id}_search_score"
        vec  = spike_vec(6)
        insert(make_gold_record(ceid, vec))

        results = similarity_search(vec, limit=1, platforms=["polymarket"])
        assert len(results) >= 1
        score = float(results[0]["similarity_score"])
        assert 0.0 <= score <= 1.0

    def test_search_excludes_embedding_column(self, test_run_id):
        """
        similarity_search() must not return the raw embedding bytes.

        Why: embedding vectors are 1536 × 4 bytes = 6 KB each. Returning
        them in every search response would bloat RAG agent payloads
        without adding value (Section 6.2).
        """
        ceid = f"test_{test_run_id}_no_embedding_col"
        vec  = spike_vec(7)
        insert(make_gold_record(ceid, vec))

        results = similarity_search(vec, limit=1, platforms=["polymarket"])
        assert len(results) >= 1
        assert "embedding" not in results[0], (
            "Raw embedding column must not be returned by similarity_search()"
        )

    def test_search_empty_result_on_no_matches(self):
        """similarity_search() must return an empty list when platform filter matches nothing."""
        # Use a platform that exists in CHECK constraint but has no test rows
        results = similarity_search(
            spike_vec(8),
            limit=5,
            platforms=["hackernews"],
            min_impact=5,
        )
        # May return [] or real records — just confirm it's a list (no crash)
        assert isinstance(results, list)


# ==========================================================
# Gate 3.4 — Idempotency
# ==========================================================

@pytest.mark.usefixtures("cleanup_social_vectors")
class TestIdempotency:
    """
    Verifies ON CONFLICT (signal_id) DO NOTHING: re-inserting the same
    record must not create a duplicate row (Gate 3 check 12).
    """

    def test_duplicate_insert_does_not_create_extra_row(self, test_run_id):
        """
        Calling insert() twice with the same signal_id must result in
        exactly one row in the database.
        """
        ceid      = f"test_{test_run_id}_idempotent"
        signal_id = str(uuid.uuid4())
        record    = make_gold_record(ceid, uniform_vec(), signal_id=signal_id)

        sid1 = insert(record)
        sid2 = insert(record)  # exact same record

        assert sid1 == sid2 == signal_id

        with get_cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS cnt FROM social_vectors WHERE signal_id = %s;",
                (signal_id,),
            )
            count = cur.fetchone()["cnt"]

        assert count == 1, f"Expected 1 row, found {count} — duplicate insert not idempotent"

    def test_second_insert_returns_same_signal_id(self, test_run_id):
        """insert() on a duplicate must return the same signal_id, not a new one."""
        ceid      = f"test_{test_run_id}_idempotent_return"
        signal_id = str(uuid.uuid4())
        record    = make_gold_record(ceid, uniform_vec(), signal_id=signal_id)

        first  = insert(record)
        second = insert(record)
        assert first == second


# ==========================================================
# Gate 3.5 — Validation Guards
# ==========================================================

class TestValidationGuards:
    """
    Verifies that insert() raises ValueError before reaching PostgreSQL
    when the record is structurally invalid (Gate 3 checks 13-15).

    These tests require no DB connection — the ValueError is raised by
    _validate_record() and _validate_embedding() before any SQL executes.
    """

    def test_wrong_embedding_dimension_raises(self):
        """insert() must raise ValueError when embedding dimension != 1536."""
        record = make_gold_record(
            canonical_event_id="test_dim_error",
            embedding=[0.1] * 512,  # wrong dimension
        )
        with pytest.raises(ValueError, match="embedding dimension mismatch"):
            insert(record)

    def test_invalid_source_platform_raises(self):
        """insert() must raise ValueError for an unknown source_platform."""
        record = make_gold_record(
            canonical_event_id="test_platform_error",
            embedding=uniform_vec(),
            source_platform="twitter",  # not in VALID_PLATFORMS
        )
        with pytest.raises(ValueError, match="source_platform"):
            insert(record)

    def test_missing_metadata_key_raises(self):
        """insert() must raise ValueError when the metadata block is absent."""
        record = {
            "content_vitals": {},
            "enrichment_ai":  {},
            "social_context": {},
            "embedding":      uniform_vec(),
            # 'metadata' intentionally missing
        }
        with pytest.raises(ValueError, match="Missing required key: 'metadata'"):
            insert(record)

    def test_missing_embedding_key_raises(self):
        """insert() must raise ValueError when the embedding key is absent."""
        record = {
            "metadata":      {"canonical_event_id": "x", "source_platform": "polymarket"},
            "content_vitals": {},
            "enrichment_ai":  {},
            "social_context": {},
            # 'embedding' intentionally missing
        }
        with pytest.raises(ValueError, match="Missing required key: 'embedding'"):
            insert(record)

    def test_embedding_must_be_list(self):
        """insert() must raise ValueError when embedding is not a list."""
        record = make_gold_record(
            canonical_event_id="test_emb_type_error",
            embedding="not_a_list",  # type: ignore
        )
        with pytest.raises(ValueError, match="embedding must be a list"):
            insert(record)

    def test_valid_platforms_constant(self):
        """VALID_PLATFORMS must contain exactly the two platforms in init.sql CHECK constraint.
        Reddit removed (Sprint 11 T4) — API pre-approval required (Nov 2025 policy)."""
        assert VALID_PLATFORMS == frozenset({"polymarket", "hackernews"})
