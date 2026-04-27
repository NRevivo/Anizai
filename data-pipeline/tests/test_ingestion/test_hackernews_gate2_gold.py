"""
Gate 2 Gold — Processing Gate: HackerNews Gold Job Logic Validation.

Validates that processing/gold_job.py HackerNews branch correctly:
    [1]  build_hackernews_consensus_prompt() formats title + top_comments
    [2]  build_hackernews_consensus_prompt() handles empty top_comments
    [3]  build_hackernews_gold_social_pulse() produces a valid Section C.6 record
    [4]  metadata.source_platform == "hackernews"
    [5]  metadata fields are present and typed correctly
    [6]  All enrichment_ai fields are correctly mapped and type-cast
    [7]  social_context.community_name == "HackerNews"
    [8]  social_context.author_handle == story author
    [9]  social_context.primary_engagement_score == len(top_comments)
    [10] platform_logic.entry_type == "hackernews_story_summary"
    [11] platform_logic.story_type preserved from Silver
    [12] platform_logic.points preserved from Silver as int
    [13] platform_logic.top_technical_insights == ai_meta.key_findings
    [14] platform_logic.external_link_ref == Silver url
    [15] platform_logic.community_sentiment == ai_meta.sentiment_score (float)
    [16] platform_logic.has_raw_source == True
    [17] embedding is a list of EMBEDDING_DIM floats
    [18] process_hackernews_gold_message() returns (GOLD_SOCIAL_PULSE, record) on success
    [19] process_hackernews_gold_message() returns (None, None) for is_high_signal=False
    [20] process_hackernews_gold_message() routes invalid Silver to DLQ
    [21] process_hackernews_gold_message() routes OpenAI consensus error to DLQ
    [22] process_hackernews_gold_message() routes OpenAI embedding error to DLQ
    [23] DLQ record has required diagnostic fields (dlq_id, source_topic, etc.)
    [24] DLQ source_topic == SILVER_SOCIAL_PULSE
    [25] DLQ failed_layer == "Gold"
    [26] validate_gold_social_pulse() passes on a well-formed HackerNews record
    [27] content_vitals.description_snippet truncated to 300 chars
    [28] content_vitals.url == Silver url
    [29] impact_level type-cast to int
    [30] reliability_score type-cast to float

No live database or Kafka connection required — all OpenAI calls are
replaced by unittest.mock.MagicMock using the mock payload from
tests/mocks/openai_hackernews_enrichment.json.

References:
    - Section 4.2A: Consensus Bundling — GPT-4o + embedding
    - Section 9.3:  Triple-Gate Test Matrix — Gate 2 (processing logic)
    - Section B.2:  HackerNews ingestion parameters
    - Section C.6:  Gold Social Pulse Schema — HackerNews Story-Centric extension
    - Section 3.5:  Dead-Letter Queue — never silently drop
"""

import json
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from config.kafka_topics import DEAD_LETTER_QUEUE, GOLD_SOCIAL_PULSE, SILVER_SOCIAL_PULSE
from processing.gold_job import (
    EMBEDDING_DIM,
    _deterministic_signal_id,
    build_hackernews_consensus_prompt,
    build_hackernews_gold_social_pulse,
    process_hackernews_gold_message,
)
from utils.validators import validate_gold_social_pulse

# ==========================================================
# Shared fixtures
# ==========================================================

MOCKS_DIR = Path(__file__).parent.parent / "mocks"

# Load mock GPT-4o enrichment response — strip the _comment metadata key
_raw_ai = json.loads((MOCKS_DIR / "openai_hackernews_enrichment.json").read_text())
AI_META = {k: v for k, v in _raw_ai.items() if not k.startswith("_")}

# Synthetic 1536-dim embedding (small uniform values — avoids all-zeros edge case)
MOCK_EMBEDDING = [0.001] * EMBEDDING_DIM

# Minimal valid Silver social record — HackerNews Story-Centric (Section C.3)
SILVER_SOCIAL = {
    "source_name":     "hackernews",
    "ingested_at":     "2024-04-01T14:30:00+00:00",
    "story_id":        "40185065",
    "title":           "Polymarket hits $1B in trading volume as prediction markets gain mainstream attention",
    "url":             "https://www.reuters.com/markets/prediction-markets-polymarket-1b-volume-2024-04-01/",
    "author":          "technologist42",
    "points":          187,
    "published_at":    "2024-04-01T14:30:00.000Z",
    "top_comments": [
        {
            "comment_id": 40185200,
            "author":     "marketwatcher",
            "text":       "The growth is driven largely by election markets.",
            "created_at": "2024-04-01T14:45:00.000Z",
        },
        {
            "comment_id": 40185201,
            "author":     "quantresearcher",
            "text":       "Prediction markets are the most honest signal we have for geopolitical risk.",
            "created_at": "2024-04-01T14:52:00.000Z",
        },
    ],
    "num_comments":    73,
    "story_text":      "",
    "story_type":      "story",
    "content_hash":    "a" * 64,
    "relevance_score": 0.85,
    "sniper_keywords": ["polymarket", "prediction markets"],
    "is_high_signal":  True,
    "fetch_mode":      "pulse",
    "bronze_ref":      str(uuid.uuid4()),
}


def _make_openai_mock(ai_meta: dict, embedding: list[float]) -> MagicMock:
    """
    Build a minimal openai.OpenAI mock that satisfies call_hackernews_consensus()
    and call_openai_embedding() without hitting the real API.
    """
    mock_client = MagicMock()

    # chat.completions.create — returns AI metadata JSON
    mock_choice = MagicMock()
    mock_choice.message.content = json.dumps(ai_meta)
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_client.chat.completions.create.return_value = mock_response

    # embeddings.create — returns 1536-dim float list
    mock_emb_data = MagicMock()
    mock_emb_data.embedding = embedding
    mock_emb_response = MagicMock()
    mock_emb_response.data = [mock_emb_data]
    mock_client.embeddings.create.return_value = mock_emb_response

    return mock_client


# ==========================================================
# [1]–[2] Prompt builder tests
# ==========================================================

class TestBuildHackerNewsConsensusPrompt:
    """Validates build_hackernews_consensus_prompt() output structure."""

    def test_prompt_contains_title(self):
        """Prompt must contain the story title (check [1])."""
        prompt = build_hackernews_consensus_prompt(SILVER_SOCIAL)
        assert SILVER_SOCIAL["title"] in prompt

    def test_prompt_contains_hackernews_label(self):
        """Prompt must start with 'HackerNews story:' label (check [1])."""
        prompt = build_hackernews_consensus_prompt(SILVER_SOCIAL)
        assert prompt.startswith("HackerNews story:")

    def test_prompt_contains_comment_text(self):
        """Prompt must include comment text from top_comments (check [1])."""
        prompt = build_hackernews_consensus_prompt(SILVER_SOCIAL)
        assert "The growth is driven largely by election markets." in prompt
        assert "Prediction markets are the most honest signal" in prompt

    def test_prompt_uses_numbered_list(self):
        """Comments must be formatted as a numbered list (check [1])."""
        prompt = build_hackernews_consensus_prompt(SILVER_SOCIAL)
        assert "1." in prompt
        assert "2." in prompt

    def test_prompt_reports_comment_count(self):
        """Prompt must report total comment count (check [1])."""
        prompt = build_hackernews_consensus_prompt(SILVER_SOCIAL)
        assert f"({len(SILVER_SOCIAL['top_comments'])} total)" in prompt

    def test_prompt_empty_comments_still_valid(self):
        """Empty top_comments produces a valid (non-crashing) prompt (check [2])."""
        silver_no_comments = {**SILVER_SOCIAL, "top_comments": []}
        prompt = build_hackernews_consensus_prompt(silver_no_comments)
        assert "HackerNews story:" in prompt
        assert "(0 total)" in prompt


# ==========================================================
# [3]–[17] Gold record builder tests
# ==========================================================

class TestBuildHackerNewsGoldSocialPulse:
    """Validates build_hackernews_gold_social_pulse() output structure."""

    @pytest.fixture(scope="class")
    def gold_record(self):
        return build_hackernews_gold_social_pulse(SILVER_SOCIAL, AI_META, MOCK_EMBEDDING)

    def test_gold_passes_validate_gold_social_pulse(self, gold_record):
        """Gold record passes validate_gold_social_pulse() (check [3])."""
        result = validate_gold_social_pulse(gold_record)
        assert result.is_valid, f"validate_gold_social_pulse errors: {result.errors}"

    def test_source_platform_is_hackernews(self, gold_record):
        """metadata.source_platform == 'hackernews' (check [4])."""
        assert gold_record["metadata"]["source_platform"] == "hackernews"

    def test_metadata_fields_present(self, gold_record):
        """All required metadata fields are present and typed (check [5])."""
        meta = gold_record["metadata"]
        assert isinstance(meta["signal_id"], str) and len(meta["signal_id"]) == 36
        assert isinstance(meta["canonical_event_id"], str)
        assert isinstance(meta["published_at"], str)
        # silver_data_ref is None before the caller patches with social_vault.social_id
        # (T1, Sprint 11). None is correct — not a misleading Bronze UUID reference.
        assert meta["silver_data_ref"] is None
        assert isinstance(meta["raw_data_ref"], str)

    def test_enrichment_ai_impact_level_is_int(self, gold_record):
        """enrichment_ai.impact_level is int (check [6, 29])."""
        val = gold_record["enrichment_ai"]["impact_level"]
        assert isinstance(val, int)
        assert 1 <= val <= 5

    def test_enrichment_ai_reliability_score_is_float(self, gold_record):
        """enrichment_ai.reliability_score is float in [0, 1] (check [6, 30])."""
        val = gold_record["enrichment_ai"]["reliability_score"]
        assert isinstance(val, float)
        assert 0.0 <= val <= 1.0

    def test_enrichment_ai_fact_check_flag_is_bool(self, gold_record):
        """enrichment_ai.fact_check_flag is bool (check [6])."""
        assert isinstance(gold_record["enrichment_ai"]["fact_check_flag"], bool)

    def test_enrichment_ai_key_findings_is_list(self, gold_record):
        """enrichment_ai.key_findings is a list (check [6])."""
        assert isinstance(gold_record["enrichment_ai"]["key_findings"], list)

    def test_social_context_community_name(self, gold_record):
        """social_context.community_name == 'HackerNews' (check [7])."""
        assert gold_record["social_context"]["community_name"] == "HackerNews"

    def test_social_context_author_handle(self, gold_record):
        """social_context.author_handle == story author (check [8])."""
        assert gold_record["social_context"]["author_handle"] == SILVER_SOCIAL["author"]

    def test_social_context_engagement_score(self, gold_record):
        """social_context.primary_engagement_score == len(top_comments) (check [9])."""
        expected = len(SILVER_SOCIAL["top_comments"])
        assert gold_record["social_context"]["primary_engagement_score"] == expected

    def test_platform_logic_entry_type(self, gold_record):
        """platform_logic.entry_type == 'hackernews_story_summary' (check [10])."""
        assert gold_record["platform_logic"]["entry_type"] == "hackernews_story_summary"

    def test_platform_logic_story_type_preserved(self, gold_record):
        """platform_logic.story_type preserved from Silver (check [11])."""
        assert gold_record["platform_logic"]["story_type"] == SILVER_SOCIAL["story_type"]

    def test_platform_logic_points_preserved(self, gold_record):
        """platform_logic.points preserved as int from Silver (check [12])."""
        assert isinstance(gold_record["platform_logic"]["points"], int)
        assert gold_record["platform_logic"]["points"] == SILVER_SOCIAL["points"]

    def test_platform_logic_top_technical_insights(self, gold_record):
        """platform_logic.top_technical_insights == ai_meta.key_findings (check [13])."""
        assert gold_record["platform_logic"]["top_technical_insights"] == AI_META["key_findings"]

    def test_platform_logic_external_link_ref(self, gold_record):
        """platform_logic.external_link_ref == Silver url (check [14])."""
        assert gold_record["platform_logic"]["external_link_ref"] == SILVER_SOCIAL["url"]

    def test_platform_logic_community_sentiment_is_float(self, gold_record):
        """platform_logic.community_sentiment == ai_meta.sentiment_score as float (check [15])."""
        val = gold_record["platform_logic"]["community_sentiment"]
        assert isinstance(val, float)
        assert val == float(AI_META["sentiment_score"])

    def test_platform_logic_has_raw_source_true(self, gold_record):
        """platform_logic.has_raw_source == True (check [16])."""
        assert gold_record["platform_logic"]["has_raw_source"] is True

    def test_embedding_is_correct_dimension(self, gold_record):
        """embedding is a list of EMBEDDING_DIM floats (check [17])."""
        emb = gold_record["embedding"]
        assert isinstance(emb, list)
        assert len(emb) == EMBEDDING_DIM

    def test_content_vitals_description_snippet_truncated(self, gold_record):
        """content_vitals.description_snippet is at most 300 chars (check [27])."""
        assert len(gold_record["content_vitals"]["description_snippet"]) <= 300

    def test_content_vitals_url(self, gold_record):
        """content_vitals.url == Silver url (check [28])."""
        assert gold_record["content_vitals"]["url"] == SILVER_SOCIAL["url"]


# ==========================================================
# [18]–[25] Process function dispatch tests
# ==========================================================

class TestProcessHackerNewsGoldMessage:
    """
    Validates process_hackernews_gold_message() routing and DLQ behaviour.
    Uses unittest.mock to replace OpenAI calls (Section 4.4 Mock-Driven Development).
    """

    def test_returns_gold_social_pulse_on_success(self):
        """
        Full success path returns (GOLD_SOCIAL_PULSE, gold_record) (check [18]).

        Injects a mock OpenAI client so no real API call is made.
        """
        mock_client = _make_openai_mock(AI_META, MOCK_EMBEDDING)
        topic, record = process_hackernews_gold_message(SILVER_SOCIAL, openai_client=mock_client)
        assert topic == GOLD_SOCIAL_PULSE
        assert record is not None
        assert record["metadata"]["source_platform"] == "hackernews"

    def test_low_signal_returns_none_none(self):
        """
        is_high_signal=False → (None, None) — intentional skip (check [19]).

        Low-signal stories are stored in social_vault but skip OpenAI token spend
        (Section 4.1A). No DLQ record is emitted.
        """
        low_signal = {**SILVER_SOCIAL, "is_high_signal": False, "relevance_score": 0.0}
        topic, record = process_hackernews_gold_message(low_signal)
        assert topic is None
        assert record is None

    def test_invalid_silver_routes_to_dlq(self):
        """
        Silver record missing required fields routes to DEAD_LETTER_QUEUE (check [20]).
        """
        bad_silver = {
            "source_name":    "hackernews",
            "is_high_signal": True,
            # story_id, title, url, points, author, published_at, top_comments all missing
        }
        mock_client = _make_openai_mock(AI_META, MOCK_EMBEDDING)
        topic, record = process_hackernews_gold_message(bad_silver, openai_client=mock_client)
        assert topic == DEAD_LETTER_QUEUE
        assert record is not None

    def test_openai_consensus_error_routes_to_dlq(self):
        """
        OpenAI consensus API error routes to DEAD_LETTER_QUEUE (check [21]).

        Simulates a transient API failure (e.g. rate limit, network timeout).
        """
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = RuntimeError("rate limit exceeded")
        # Also set up embeddings.create so it doesn't interfere (should not be reached)
        mock_client.embeddings.create.return_value = MagicMock()

        topic, record = process_hackernews_gold_message(SILVER_SOCIAL, openai_client=mock_client)
        assert topic == DEAD_LETTER_QUEUE
        assert "openai_consensus" == record["failed_stage"]

    def test_openai_embedding_error_routes_to_dlq(self):
        """
        OpenAI embedding API error routes to DEAD_LETTER_QUEUE (check [22]).
        """
        mock_client = MagicMock()

        # Consensus call succeeds
        mock_choice = MagicMock()
        mock_choice.message.content = json.dumps(AI_META)
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_client.chat.completions.create.return_value = mock_response

        # Embedding call fails
        mock_client.embeddings.create.side_effect = RuntimeError("embedding service down")

        topic, record = process_hackernews_gold_message(SILVER_SOCIAL, openai_client=mock_client)
        assert topic == DEAD_LETTER_QUEUE
        assert "openai_embedding" == record["failed_stage"]

    def test_dlq_record_has_required_diagnostic_fields(self):
        """
        DLQ record contains all required diagnostic fields (check [23]).
        """
        bad_silver = {"source_name": "hackernews", "is_high_signal": True}
        mock_client = _make_openai_mock(AI_META, MOCK_EMBEDDING)
        _, record = process_hackernews_gold_message(bad_silver, openai_client=mock_client)

        assert "dlq_id" in record
        assert "failed_at" in record
        assert "failed_layer" in record
        assert "failed_stage" in record
        assert "source_topic" in record
        assert "validation_errors" in record
        assert "original_message" in record

    def test_dlq_source_topic_is_silver_social_pulse(self):
        """
        DLQ source_topic == SILVER_SOCIAL_PULSE (check [24]).

        HackerNews Gold records arrive from process.silver.social_pulse —
        DLQ operators replay from SILVER_SOCIAL_PULSE (not SILVER_GLOBAL_NEWS).
        """
        bad_silver = {"source_name": "hackernews", "is_high_signal": True}
        mock_client = _make_openai_mock(AI_META, MOCK_EMBEDDING)
        _, record = process_hackernews_gold_message(bad_silver, openai_client=mock_client)
        assert record["source_topic"] == SILVER_SOCIAL_PULSE

    def test_dlq_failed_layer_is_gold(self):
        """DLQ failed_layer == 'Gold' (check [25])."""
        bad_silver = {"source_name": "hackernews", "is_high_signal": True}
        mock_client = _make_openai_mock(AI_META, MOCK_EMBEDDING)
        _, record = process_hackernews_gold_message(bad_silver, openai_client=mock_client)
        assert record["failed_layer"] == "Gold"


# ==========================================================
# [26] validate_gold_social_pulse integration
# ==========================================================

class TestValidateGoldSocialPulseIntegration:
    """
    Confirms validate_gold_social_pulse() accepts the HackerNews record shape.
    """

    def test_well_formed_hackernews_record_passes_validator(self):
        """validate_gold_social_pulse() passes on a well-formed HackerNews record (check [26])."""
        gold = build_hackernews_gold_social_pulse(SILVER_SOCIAL, AI_META, MOCK_EMBEDDING)
        result = validate_gold_social_pulse(gold)
        assert result.is_valid, (
            f"Unexpected validation errors for HackerNews Gold record: {result.errors}"
        )

    def test_missing_social_context_fails_validator(self):
        """Gold record without social_context fails validate_gold_social_pulse()."""
        gold = build_hackernews_gold_social_pulse(SILVER_SOCIAL, AI_META, MOCK_EMBEDDING)
        del gold["social_context"]
        result = validate_gold_social_pulse(gold)
        assert not result.is_valid

    def test_missing_community_name_fails_validator(self):
        """Gold record with missing community_name fails validate_gold_social_pulse()."""
        gold = build_hackernews_gold_social_pulse(SILVER_SOCIAL, AI_META, MOCK_EMBEDDING)
        del gold["social_context"]["community_name"]
        result = validate_gold_social_pulse(gold)
        assert not result.is_valid


# ==========================================================
# Sprint 11 T2 — deterministic signal_id (Gap 2, HackerNews branch)
# ==========================================================

class TestHackerNewsDeterministicSignalId:
    """
    Validates UUID5-based deterministic signal_id in the HackerNews Gold builder.

    Shares the same _deterministic_signal_id helper as the Polymarket branch.
    Tests here confirm the helper is correctly wired into build_hackernews_gold_social_pulse()
    — the same dedup guarantee applies to HackerNews re-deliveries (Sprint 11 T2).
    """

    HASH_A = "c" * 64   # distinct from Polymarket test hashes to avoid UUID5 collisions
    HASH_B = "d" * 64

    def test_builder_signal_id_is_deterministic(self):
        """
        Two calls with the same content_hash must produce the same signal_id.

        ON CONFLICT (signal_id) DO NOTHING fires on re-delivery only when signal_id
        is stable. uuid4() would generate a new UUID every time, bypassing the guard.
        """
        silver_a = {**SILVER_SOCIAL, "content_hash": self.HASH_A}
        silver_b = {**SILVER_SOCIAL, "content_hash": self.HASH_A}
        r1 = build_hackernews_gold_social_pulse(silver_a, AI_META, MOCK_EMBEDDING)
        r2 = build_hackernews_gold_social_pulse(silver_b, AI_META, MOCK_EMBEDDING)
        assert r1["metadata"]["signal_id"] == r2["metadata"]["signal_id"]

    def test_builder_signal_id_differs_for_different_content_hash(self):
        """Different stories (different content_hash) must produce different signal_ids."""
        silver_a = {**SILVER_SOCIAL, "content_hash": self.HASH_A}
        silver_b = {**SILVER_SOCIAL, "content_hash": self.HASH_B}
        r1 = build_hackernews_gold_social_pulse(silver_a, AI_META, MOCK_EMBEDDING)
        r2 = build_hackernews_gold_social_pulse(silver_b, AI_META, MOCK_EMBEDDING)
        assert r1["metadata"]["signal_id"] != r2["metadata"]["signal_id"]
