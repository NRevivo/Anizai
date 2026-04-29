"""
Gate 3 — Persistence Gate: HackerNews social_vault + social_vectors Verification.

Validates that:
    A. Silver HackerNews records written by persistence/social_vault.py are
       stored and retrievable from the live PostgreSQL `social_vault` table.
    B. Gold HackerNews Social Pulse records written by persistence/social_vectors.py
       are stored and retrievable from the `social_vectors` table (pgvector + HNSW),
       with entry_type="hackernews_story_summary" and HackerNews platform_logic
       extension fields preserved through the JSONB round-trip.

Requires: PostgreSQL container running (docker compose up postgres).
          All Gate 1 and Gate 2 tests must pass before running Gate 3.

What Gate 3 covers (Section 9.3 Gate 3):

social_vault:
    [1]  insert() stores a HackerNews Silver record and returns social_id
    [2]  fetch_by_social_id() retrieves the exact record
    [3]  source_name == "hackernews" stored correctly
    [4]  platform_data JSONB preserves story_id, title, points, top_comments
    [5]  exists_by_content_hash() returns True after insert
    [6]  exists_by_content_hash() returns False for unknown hash
    [7]  insert() raises ValueError for unknown source_name
    [8]  ingested_at is a timezone-aware datetime

social_vectors:
    [9]  insert() stores a HackerNews Gold Social Pulse and returns signal_id
    [10] fetch_by_signal_id() retrieves the exact record
    [11] source_platform == "hackernews" and entry_type == "hackernews_story_summary"
    [12] platform_logic JSONB round-trip: story_type, points, top_technical_insights,
         external_link_ref, community_sentiment, has_raw_source
    [13] social_context.community_name == "HackerNews" survives round-trip
    [14] enrichment_ai JSONB round-trip (impact_level, reliability_score, entities)
    [15] exists_by_canonical_event() returns True after insert
    [16] exists_by_canonical_event() returns False for unknown event
    [17] exists_by_canonical_event() platform filter works correctly
    [18] similarity_search() returns the inserted record with similarity_score ≈ 1.0
    [19] similarity_search() platform filter restricts to hackernews records
    [20] insert() is idempotent — ON CONFLICT (signal_id) DO NOTHING
    [21] insert() raises ValueError for invalid source_platform
    [22] _default_entry_type("hackernews") == "hackernews_story_summary"
    [23] published_at is stored as timezone-aware datetime
    [24] has_raw_source == True survives JSONB round-trip

Isolation: every test record uses a canonical_event_id prefixed with
"test_<test_run_id>_" (conftest.py). cleanup_social_vectors and
cleanup_social_vault fixtures delete matching rows after each test.

References:
    - Section 5.1:  Silver stores — Social Vault architecture
    - Section 5.2:  Vector Intelligence — pgvector with HNSW
    - Section 6.2:  The Pulse Analyst agent (primary query consumer)
    - Section 9.3:  Triple-Gate Test Matrix — Gate 3
    - Section B.2:  HackerNews ingestion parameters
    - Section C.3:  Silver Social Discourse Store — HackerNews Story-Centric
    - Section C.6:  Gold Social Pulse — HackerNews Story-Centric extension
    - Section 3.3:  Service Isolation — only persistence/ modules write to DB
"""

import math
import uuid
from datetime import datetime, timezone

import pytest

from persistence.social_vault import (
    VALID_SOURCES,
    exists_by_content_hash,
    fetch_by_social_id,
    insert as sv_insert,
)
from persistence.social_vectors import (
    EMBEDDING_DIM,
    VALID_PLATFORMS,
    _default_entry_type,
    exists_by_canonical_event,
    fetch_by_signal_id,
    insert as svec_insert,
    similarity_search,
)
from utils.db import get_cursor

pytestmark = pytest.mark.usefixtures("db_available")


# ==========================================================
# Embedding helpers (same pattern as Polymarket Gate 3)
# ==========================================================

def spike_vec(pos: int, dim: int = EMBEDDING_DIM) -> list[float]:
    """Unit vector with 1.0 at position `pos`. Cosine sim = 1.0 vs itself."""
    v = [0.0] * dim
    v[pos] = 1.0
    return v


def uniform_vec(dim: int = EMBEDDING_DIM) -> list[float]:
    """L2-normalised uniform vector — valid embedding for tests where sim score doesn't matter."""
    val = 1.0 / math.sqrt(dim)
    return [val] * dim


# ==========================================================
# Record builders
# ==========================================================

def make_hn_silver(
    content_hash:       str | None = None,
    bronze_ref:         str | None = None,
    story_id:           str        = "40185065",
    canonical_event_id: str | None = None,
) -> dict:
    """
    Build a minimal valid HackerNews Silver social record (Section C.3).
    Matches the output of map_hackernews_story_to_silver().

    canonical_event_id: when provided, written into the record so that
        social_vault.insert() uses it as the isolation key (cleanup fixture
        deletes rows matching ``canonical_event_id LIKE 'test_{run_id}%'``).
        bronze_ref is always kept as a valid UUID (raw_data_ref NOT NULL).
    """
    record = {
        "source_name":     "hackernews",
        "ingested_at":     "2024-04-01T14:30:00+00:00",
        "story_id":        story_id,
        "title":           "Polymarket hits $1B in trading volume",
        "url":             "https://www.reuters.com/markets/prediction-markets-1b",
        "author":          "technologist42",
        "points":          187,
        "published_at":    "2024-04-01T14:30:00.000Z",
        "top_comments": [
            {
                "comment_id": 40185200,
                "author":     "marketwatcher",
                "text":       "Prediction markets are the most honest signal.",
                "created_at": "2024-04-01T14:45:00.000Z",
            }
        ],
        "num_comments":    73,
        "story_text":      "",
        "story_type":      "story",
        "content_hash":    content_hash or ("a" * 64),
        "relevance_score": 0.85,
        "sniper_keywords": ["polymarket", "prediction markets"],
        "is_high_signal":  True,
        "fetch_mode":      "pulse",
        "bronze_ref":      bronze_ref or str(uuid.uuid4()),
    }
    if canonical_event_id is not None:
        record["canonical_event_id"] = canonical_event_id
    return record


def make_hn_gold(
    canonical_event_id: str,
    embedding:          list[float],
    impact_level:       int   = 4,
    urgency_level:      int   = 3,
    reliability_score:  float = 0.82,
    signal_id:          str | None = None,
) -> dict:
    """
    Build a minimal valid HackerNews Gold Social Pulse record (Section C.6).
    Matches the output of build_hackernews_gold_social_pulse().
    """
    return {
        "metadata": {
            "signal_id":          signal_id or str(uuid.uuid4()),
            "canonical_event_id": canonical_event_id,
            "source_platform":    "hackernews",
            "published_at":       "2024-04-01T14:30:00+00:00",
            "silver_data_ref":    str(uuid.uuid4()),
            "raw_data_ref":       str(uuid.uuid4()),
        },
        "content_vitals": {
            "title":               "Polymarket hits $1B in trading volume",
            "url":                 "https://www.reuters.com/markets/prediction-markets-1b",
            "description_snippet": "Community broadly views Polymarket's $1B milestone as validation of prediction markets.",
        },
        "enrichment_ai": {
            "executive_summary":    "HackerNews community views $1B as validation of prediction markets.",
            "key_findings":         [
                "AI regulation contracts showing unexpected liquidity.",
                "CFTC regulatory risk is primary ceiling for US institutional adoption.",
            ],
            "impact_level":         impact_level,
            "urgency_level":        urgency_level,
            "reliability_score":    reliability_score,
            "sentiment_score":      0.55,
            "extracted_entities":   ["Polymarket", "CFTC", "Federal Reserve"],
            "topic_classification": "Geopolitics",
            "fact_check_flag":      False,
        },
        "social_context": {
            "community_name":           "HackerNews",
            "author_handle":            "technologist42",
            "author_reputation":        0.8,
            "primary_engagement_score": 1,
        },
        "platform_logic": {
            "entry_type":             "hackernews_story_summary",
            "story_type":             "story",
            "points":                 187,
            "top_technical_insights": [
                "AI regulation contracts showing unexpected liquidity.",
                "CFTC regulatory risk is primary ceiling for US institutional adoption.",
            ],
            "external_link_ref":      "https://www.reuters.com/markets/prediction-markets-1b",
            "community_sentiment":    0.55,
            "has_raw_source":         True,
        },
        "embedding": embedding,
    }


# ==========================================================
# Gate 3A — social_vault (Silver persistence, Section 5.1)
# ==========================================================

@pytest.mark.usefixtures("cleanup_social_vault")
class TestHackerNewsSocialVaultInsert:
    """
    Verifies social_vault.insert() and fetch_by_social_id() for HackerNews
    Silver records (Gate 3 checks [1]–[8]).
    """

    def test_insert_returns_social_id(self, test_run_id):
        """insert() must return a valid UUID social_id (check [1])."""
        record = make_hn_silver(canonical_event_id=f"test_{test_run_id}_sv_basic")
        social_id = sv_insert(record)
        assert isinstance(social_id, str)
        assert uuid.UUID(social_id)

    def test_fetch_returns_inserted_record(self, test_run_id):
        """fetch_by_social_id() must retrieve the exact record that was inserted (check [2])."""
        record    = make_hn_silver(canonical_event_id=f"test_{test_run_id}_sv_fetch")
        social_id = sv_insert(record)

        row = fetch_by_social_id(social_id)
        assert row is not None, f"No row found for social_id={social_id}"
        assert str(row["social_id"]) == social_id

    def test_source_name_stored_correctly(self, test_run_id):
        """source_name == 'hackernews' persisted to social_vault (check [3])."""
        record    = make_hn_silver(canonical_event_id=f"test_{test_run_id}_sv_source")
        social_id = sv_insert(record)
        row       = fetch_by_social_id(social_id)
        assert row["source_name"] == "hackernews"

    def test_platform_data_jsonb_round_trip(self, test_run_id):
        """
        platform_data JSONB must preserve all HackerNews story fields (check [4]).

        The full Silver record is stored as platform_data, so story_id, title,
        points, and top_comments must survive the JSONB round-trip intact.
        """
        record    = make_hn_silver(
            canonical_event_id=f"test_{test_run_id}_sv_jsonb",
            story_id="40185065",
        )
        social_id  = sv_insert(record)
        row        = fetch_by_social_id(social_id)

        pd = row["platform_data"]
        assert pd["story_id"]  == "40185065"
        assert pd["title"]     == "Polymarket hits $1B in trading volume"
        assert pd["points"]    == 187
        assert isinstance(pd["top_comments"], list)
        assert len(pd["top_comments"]) == 1
        assert pd["top_comments"][0]["author"] == "marketwatcher"

    def test_exists_by_content_hash_true_after_insert(self, test_run_id):
        """exists_by_content_hash() returns True after inserting a record (check [5])."""
        content_hash = "b" * 64
        record = make_hn_silver(
            content_hash=content_hash,
            canonical_event_id=f"test_{test_run_id}_sv_hash_true",
        )
        sv_insert(record)
        assert exists_by_content_hash(content_hash) is True

    def test_exists_by_content_hash_false_for_unknown(self):
        """exists_by_content_hash() returns False for a hash never inserted (check [6])."""
        unknown_hash = "f" * 64
        assert exists_by_content_hash(unknown_hash) is False

    def test_insert_raises_for_unknown_source_name(self):
        """insert() must raise ValueError when source_name is not in VALID_SOURCES (check [7])."""
        bad = make_hn_silver()
        bad["source_name"] = "twitter"
        with pytest.raises(ValueError, match="source_name"):
            sv_insert(bad)

    def test_ingested_at_is_timezone_aware(self, test_run_id):
        """ingested_at stored as a timezone-aware datetime (check [8])."""
        record    = make_hn_silver(canonical_event_id=f"test_{test_run_id}_sv_ts")
        social_id = sv_insert(record)
        row       = fetch_by_social_id(social_id)
        assert row["ingested_at"].tzinfo is not None

    def test_hackernews_in_valid_sources(self):
        """'hackernews' must be in VALID_SOURCES (regression guard, Section C.3)."""
        assert "hackernews" in VALID_SOURCES


# ==========================================================
# Gate 3B — social_vectors (Gold persistence, Section 5.2)
# ==========================================================

@pytest.mark.usefixtures("cleanup_social_vectors")
class TestHackerNewsSocialVectorsInsert:
    """
    Verifies social_vectors.insert() and fetch_by_signal_id() for HackerNews
    Gold Social Pulse records (Gate 3 checks [9]–[14], [23]).
    """

    def test_insert_returns_signal_id(self, test_run_id):
        """insert() must return a valid UUID signal_id (check [9])."""
        record = make_hn_gold(
            canonical_event_id=f"test_{test_run_id}_svec_basic",
            embedding=uniform_vec(),
        )
        sid = svec_insert(record)
        assert isinstance(sid, str)
        assert uuid.UUID(sid)

    def test_fetch_returns_inserted_record(self, test_run_id):
        """fetch_by_signal_id() must retrieve the exact record that was inserted (check [10])."""
        ceid   = f"test_{test_run_id}_svec_fetch"
        record = make_hn_gold(ceid, uniform_vec())
        sid    = svec_insert(record)

        row = fetch_by_signal_id(sid)
        assert row is not None
        assert str(row["signal_id"]) == sid
        assert row["canonical_event_id"] == ceid

    def test_source_platform_and_entry_type(self, test_run_id):
        """
        source_platform == 'hackernews' and entry_type == 'hackernews_story_summary'
        must be persisted correctly (check [11]).
        """
        ceid   = f"test_{test_run_id}_svec_entry_type"
        sid    = svec_insert(make_hn_gold(ceid, uniform_vec()))
        row    = fetch_by_signal_id(sid)

        assert row["source_platform"] == "hackernews"
        assert row["entry_type"]      == "hackernews_story_summary"

    def test_platform_logic_jsonb_round_trip(self, test_run_id):
        """
        platform_logic JSONB must preserve all HackerNews C.6 extension fields
        through the PostgreSQL round-trip (check [12]).
        """
        ceid   = f"test_{test_run_id}_svec_platform_logic"
        sid    = svec_insert(make_hn_gold(ceid, uniform_vec()))
        row    = fetch_by_signal_id(sid)
        pl     = row["platform_logic"]

        assert pl["entry_type"]        == "hackernews_story_summary"
        assert pl["story_type"]        == "story"
        assert int(pl["points"])       == 187
        assert isinstance(pl["top_technical_insights"], list)
        assert len(pl["top_technical_insights"]) == 2
        assert pl["external_link_ref"] == "https://www.reuters.com/markets/prediction-markets-1b"
        assert float(pl["community_sentiment"]) == pytest.approx(0.55, abs=1e-6)
        assert pl["has_raw_source"] is True

    def test_social_context_community_name_round_trip(self, test_run_id):
        """social_context.community_name == 'HackerNews' survives JSONB round-trip (check [13])."""
        ceid   = f"test_{test_run_id}_svec_sc"
        sid    = svec_insert(make_hn_gold(ceid, uniform_vec()))
        row    = fetch_by_signal_id(sid)

        sc = row["social_context"]
        assert sc["community_name"] == "HackerNews"
        assert sc["author_handle"]  == "technologist42"

    def test_enrichment_ai_round_trip(self, test_run_id):
        """enrichment_ai JSONB (impact_level, reliability_score, entities) survives (check [14])."""
        ceid   = f"test_{test_run_id}_svec_ai"
        record = make_hn_gold(ceid, uniform_vec(), impact_level=5, reliability_score=0.9)
        sid    = svec_insert(record)
        row    = fetch_by_signal_id(sid)

        ai = row["enrichment_ai"]
        assert int(ai["impact_level"])         == 5
        assert float(ai["reliability_score"])  == pytest.approx(0.9, abs=1e-6)
        assert "Polymarket" in ai["extracted_entities"]
        assert ai["fact_check_flag"] is False

    def test_published_at_is_timezone_aware(self, test_run_id):
        """published_at stored as timezone-aware datetime (check [23])."""
        ceid   = f"test_{test_run_id}_svec_ts"
        sid    = svec_insert(make_hn_gold(ceid, uniform_vec()))
        row    = fetch_by_signal_id(sid)
        assert row["published_at"].tzinfo is not None

    def test_has_raw_source_survives_jsonb(self, test_run_id):
        """has_raw_source=True in platform_logic must survive JSONB round-trip (check [24])."""
        ceid = f"test_{test_run_id}_svec_has_raw"
        sid  = svec_insert(make_hn_gold(ceid, uniform_vec()))
        row  = fetch_by_signal_id(sid)
        assert row["platform_logic"]["has_raw_source"] is True


# ==========================================================
# Gate 3C — Existence Check
# ==========================================================

@pytest.mark.usefixtures("cleanup_social_vectors")
class TestExistenceCheck:
    """
    Verifies exists_by_canonical_event() before and after insert,
    and with / without platform filter (Gate 3 checks [15]–[17]).
    """

    def test_exists_returns_true_after_insert(self, test_run_id):
        """exists_by_canonical_event() returns True after inserting (check [15])."""
        ceid = f"test_{test_run_id}_exists_true"
        svec_insert(make_hn_gold(ceid, uniform_vec()))
        assert exists_by_canonical_event(ceid) is True

    def test_exists_returns_false_for_unknown(self):
        """exists_by_canonical_event() returns False for an ID never inserted (check [16])."""
        assert exists_by_canonical_event(f"test_nonexistent_{uuid.uuid4()}") is False

    def test_exists_with_matching_platform_filter(self, test_run_id):
        """Platform filter matches when correct platform provided (check [17])."""
        ceid = f"test_{test_run_id}_exists_platform"
        svec_insert(make_hn_gold(ceid, uniform_vec()))
        assert exists_by_canonical_event(ceid, source_platform="hackernews") is True

    def test_exists_with_wrong_platform_returns_false(self, test_run_id):
        """Platform filter returns False when record is on a different platform (check [17])."""
        ceid = f"test_{test_run_id}_exists_platform_mismatch"
        svec_insert(make_hn_gold(ceid, uniform_vec()))
        assert exists_by_canonical_event(ceid, source_platform="polymarket") is False


# ==========================================================
# Gate 3D — Similarity Search
# ==========================================================

@pytest.mark.usefixtures("cleanup_social_vectors")
class TestSimilaritySearch:
    """
    Verifies HNSW cosine similarity search against live pgvector index
    for HackerNews records (Gate 3 checks [18]–[19]).
    """

    def test_search_returns_inserted_record(self, test_run_id):
        """
        Querying with the same embedding used at insert must return the
        record with similarity_score ≈ 1.0 (check [18]).
        """
        ceid = f"test_{test_run_id}_hn_search_exact"
        vec  = spike_vec(10)
        sid  = svec_insert(make_hn_gold(ceid, vec))

        results = similarity_search(vec, limit=5, platforms=["hackernews"])
        sig_ids = [str(r["signal_id"]) for r in results]
        assert sid in sig_ids, "Inserted HackerNews record not found in similarity search"

        match = next(r for r in results if str(r["signal_id"]) == sid)
        assert float(match["similarity_score"]) == pytest.approx(1.0, abs=1e-4)

    def test_search_platform_filter_restricts_to_hackernews(self, test_run_id):
        """
        platforms=['hackernews'] must not return polymarket records (check [19]).
        """
        ceid_hn   = f"test_{test_run_id}_hn_search_hn"
        ceid_poly = f"test_{test_run_id}_hn_search_poly"
        vec       = spike_vec(11)

        from tests.test_persistence.test_polymarket_gate3 import make_gold_record
        sid_hn   = svec_insert(make_hn_gold(ceid_hn,   vec))
        sid_poly = svec_insert(make_gold_record(ceid_poly, vec, source_platform="polymarket"))

        results = similarity_search(vec, limit=10, platforms=["hackernews"])
        sig_ids = [str(r["signal_id"]) for r in results]

        assert sid_hn   in sig_ids,     "HackerNews record must appear in hackernews-filtered search"
        assert sid_poly not in sig_ids, "Polymarket record must not appear in hackernews-filtered search"

    def test_search_returns_similarity_score(self, test_run_id):
        """similarity_score must be present and a float in [0.0, 1.0]."""
        ceid = f"test_{test_run_id}_hn_search_score"
        vec  = spike_vec(12)
        svec_insert(make_hn_gold(ceid, vec))

        results = similarity_search(vec, limit=1, platforms=["hackernews"])
        assert len(results) >= 1
        score = float(results[0]["similarity_score"])
        assert 0.0 <= score <= 1.0


# ==========================================================
# Gate 3E — Idempotency
# ==========================================================

@pytest.mark.usefixtures("cleanup_social_vectors")
class TestIdempotency:
    """
    Verifies ON CONFLICT (signal_id) DO NOTHING for HackerNews records
    (Gate 3 check [20]).
    """

    def test_duplicate_insert_does_not_create_extra_row(self, test_run_id):
        """Re-inserting the same signal_id must result in exactly one row."""
        ceid      = f"test_{test_run_id}_hn_idempotent"
        signal_id = str(uuid.uuid4())
        record    = make_hn_gold(ceid, uniform_vec(), signal_id=signal_id)

        svec_insert(record)
        svec_insert(record)  # same signal_id — must be ignored

        with get_cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS cnt FROM social_vectors WHERE signal_id = %s;",
                (signal_id,),
            )
            count = cur.fetchone()["cnt"]

        assert count == 1, f"Expected 1 row after duplicate insert, found {count}"


# ==========================================================
# Gate 3F — Validation Guards (no DB required)
# ==========================================================

class TestValidationGuards:
    """
    Verifies that insert() raises ValueError before hitting PostgreSQL
    when the record is structurally invalid (Gate 3 check [21]).
    """

    def test_invalid_source_platform_raises(self):
        """insert() raises ValueError for source_platform='twitter' (check [21])."""
        record = make_hn_gold("guard_platform", uniform_vec())
        record["metadata"]["source_platform"] = "twitter"
        with pytest.raises(ValueError, match="source_platform"):
            svec_insert(record)

    def test_wrong_embedding_dimension_raises(self):
        """insert() raises ValueError when embedding dimension != 1536."""
        record = make_hn_gold("guard_dim", [0.1] * 512)
        with pytest.raises(ValueError, match="embedding dimension mismatch"):
            svec_insert(record)

    def test_missing_metadata_raises(self):
        """insert() raises ValueError when metadata block is absent."""
        record = {
            "content_vitals": {},
            "enrichment_ai":  {},
            "social_context": {},
            "embedding":      uniform_vec(),
        }
        with pytest.raises(ValueError, match="Missing required key: 'metadata'"):
            svec_insert(record)


# ==========================================================
# Gate 3G — Static contract checks (no DB required)
# ==========================================================

class TestStaticContracts:
    """
    Confirms static constants and helpers match the Section C.6 spec
    without requiring a database connection (Gate 3 check [22]).
    """

    def test_default_entry_type_hackernews(self):
        """_default_entry_type('hackernews') == 'hackernews_story_summary' (check [22])."""
        assert _default_entry_type("hackernews") == "hackernews_story_summary"

    def test_hackernews_in_valid_platforms(self):
        """'hackernews' must be in VALID_PLATFORMS (regression guard, Section C.6)."""
        assert "hackernews" in VALID_PLATFORMS

    def test_valid_platforms_contains_expected_set(self):
        """VALID_PLATFORMS must contain exactly the two platforms in init.sql CHECK constraint.
        Reddit removed (Sprint 11 T4) — API pre-approval required (Nov 2025 policy)."""
        assert VALID_PLATFORMS == frozenset({"polymarket", "hackernews"})
