"""
Gate 2 Gold — Processing Gate: Polymarket Gold Job Logic Validation.

Validates that processing/gold_job.py correctly:
    [1]  build_consensus_prompt() formats the GPT-4o user prompt
    [2]  parse_openai_consensus_response() handles clean JSON and markdown fences
    [3]  build_gold_social_pulse() produces a valid Section C.6 Gold Social Pulse
    [4]  All enrichment_ai fields are correctly mapped and type-cast
    [5]  social_context and platform_logic blocks are assembled correctly
    [6]  compute_momentum_block() returns all-zeros for empty price history
    [7]  compute_momentum_block() computes change_24h correctly
    [8]  compute_momentum_block() computes all three deltas from full history
    [9]  build_gold_structured_metric() sets layer=Gold, merges momentum
    [10] build_gold_structured_metric() top-level whale_alert flag is correct
    [11] build_gold_structured_metric() does not mutate the input Silver record
    [12] process_social_pulse_message() returns (GOLD_SOCIAL_PULSE, record)
    [13] process_social_pulse_message() routes invalid Silver to DLQ
    [14] process_social_pulse_message() routes empty comment batch to (None, None)
    [15] process_social_pulse_message() routes OpenAI API error to DLQ
    [16] DLQ record contains required diagnostic fields
    [17] process_structured_metrics_message() returns (GOLD_STRUCTURED_METRICS, record)
    [18] process_structured_metrics_message() is_new_market=True on empty history
    [19] process_structured_metrics_message() sets layer=Gold
    [20] validate_gold_social_pulse() passes on a well-formed record

No live database or Kafka connection required — all OpenAI calls are
replaced by unittest.mock.MagicMock using mock payloads from tests/mocks/.

References:
    - Section 4.2A: Consensus Bundling — GPT-4o + embedding
    - Section 4.2B: Momentum Block — keyed state deltas
    - Section 9.3:  Triple-Gate Test Matrix — Gate 2 (processing logic)
    - Section C.6:  Gold Social Pulse Schema
    - Section B.8:  Polymarket — whale_alert, consensus_rating
"""

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from config.kafka_topics import DEAD_LETTER_QUEUE, GOLD_SOCIAL_PULSE, GOLD_STRUCTURED_METRICS
from processing.gold_job import (
    EMBEDDING_DIM,
    _deterministic_signal_id,
    build_consensus_prompt,
    build_gold_social_pulse,
    build_gold_structured_metric,
    compute_momentum_block,
    parse_openai_consensus_response,
    process_social_pulse_message,
    process_structured_metrics_message,
)
from utils.validators import validate_gold_social_pulse

# ==========================================================
# Shared test fixtures
# ==========================================================

MOCKS_DIR = Path(__file__).parent.parent / "mocks"

# Load mock GPT-4o response — strip the _comment metadata key
_raw_ai = json.loads((MOCKS_DIR / "openai_consensus_response.json").read_text())
AI_META = {k: v for k, v in _raw_ai.items() if not k.startswith("_")}

# Synthetic 1536-dim embedding (uniform small values, not all-zeros to avoid
# division-by-zero edge cases in cosine similarity)
MOCK_EMBEDDING = [0.001] * EMBEDDING_DIM

# Minimal valid Silver social record (Polymarket volume-centric pattern, C.3)
SILVER_SOCIAL = {
    "source_name":  "polymarket",
    "ingested_at":  "2026-03-28T14:22:00+00:00",
    "market_id":    "0x87ae1d3e2aec7cbf6b7f01781f741c82ae9bd08a",
    "question":     "Will the Federal Reserve cut rates before June 2026?",
    "raw_comments": [
        {
            "comment_id": "cm_001",
            "author":     "macro_trader_99",
            "text":       "Fed pivot incoming. Inflation data coming in soft.",
            "timestamp":  "2026-03-28T13:45:00+00:00",
            "upvotes":    14,
        },
        {
            "comment_id": "cm_002",
            "author":     "rates_watcher",
            "text":       "Core PCE still sticky. Powell sounded hawkish.",
            "timestamp":  "2026-03-28T14:01:22+00:00",
            "upvotes":    8,
        },
    ],
    "content_hash": "a" * 64,          # valid 64-char SHA-256 hex digest
    "bronze_ref":   str(uuid.uuid4()),  # Bronze envelope event_id
}

# Minimal valid Silver Structured Metric (Section C.2)
SILVER_METRIC = {
    "schema_version": "2.0",
    "layer":          "Silver",
    "entity_type":    "Structured_Metric",
    "core_identity": {
        "metric_id":             str(uuid.uuid4()),
        "canonical_event_id":    str(uuid.uuid4()),
        "parent_id":             "0x87ae1d3e2aec7cbf6b7f01781f741c82ae9bd08a",
        "source_name":           "polymarket",
        "external_reference_id": "asset_001",
    },
    "data_point": {
        "current_value": 0.67,
        "unit":          "USD",
        "status":        "active",
        "timestamp_utc": "2026-03-28T14:22:00+00:00",
    },
    "momentum_block": {
        "change_24h": 0.0, "change_7d": 0.0, "change_30d": 0.0,
        "is_new_market": False,
    },
    "metadata_extension": {
        "liquidity_pool_tvl": 50000.0,
        "bid_ask_spread":     0.02,
        "24h_volume":         285400.0,
        "is_divergent":       False,
        "whale_alert":        False,
        "resolution_rules":   "",
    },
}


def _make_mock_openai_client(
    ai_meta: dict = None,
    embedding: list = None,
) -> MagicMock:
    """
    Build a minimal mock openai.OpenAI client for Gate 2 tests.

    chat.completions.create → returns mock with choices[0].message.content = JSON string
    embeddings.create       → returns mock with data[0].embedding = embedding list
    """
    ai  = ai_meta   or AI_META
    emb = embedding or MOCK_EMBEDDING

    mock = MagicMock()
    mock.chat.completions.create.return_value.choices[0].message.content = (
        json.dumps(ai)
    )
    mock.embeddings.create.return_value.data[0].embedding = emb
    return mock


# ==========================================================
# Gate 2-G.1 — build_consensus_prompt
# ==========================================================

class TestBuildConsensusPrompt:
    """Verify that the GPT-4o prompt is assembled correctly from Silver data."""

    def test_includes_market_question(self):
        prompt = build_consensus_prompt(SILVER_SOCIAL)
        assert "Will the Federal Reserve cut rates" in prompt

    def test_includes_comment_count(self):
        prompt = build_consensus_prompt(SILVER_SOCIAL)
        # "2 total" or "(2 total)" should appear
        assert "2" in prompt

    def test_includes_comment_text(self):
        prompt = build_consensus_prompt(SILVER_SOCIAL)
        assert "Fed pivot incoming" in prompt
        assert "Core PCE still sticky" in prompt

    def test_empty_comments_does_not_crash(self):
        social = {**SILVER_SOCIAL, "raw_comments": []}
        prompt = build_consensus_prompt(social)
        assert "0" in prompt

    def test_comments_are_numbered(self):
        prompt = build_consensus_prompt(SILVER_SOCIAL)
        assert "1." in prompt
        assert "2." in prompt


# ==========================================================
# Gate 2-G.2 — parse_openai_consensus_response
# ==========================================================

class TestParseOpenAIConsensusResponse:
    """Verify JSON parsing with and without markdown code fences."""

    def test_parses_clean_json(self):
        content = json.dumps({"impact_level": 3, "executive_summary": "test"})
        result  = parse_openai_consensus_response(content)
        assert result["impact_level"] == 3
        assert result["executive_summary"] == "test"

    def test_strips_json_code_fence(self):
        content = '```json\n{"impact_level": 4, "reliability_score": 0.8}\n```'
        result  = parse_openai_consensus_response(content)
        assert result["impact_level"] == 4

    def test_strips_plain_code_fence(self):
        content = '```\n{"urgency_level": 2}\n```'
        result  = parse_openai_consensus_response(content)
        assert result["urgency_level"] == 2

    def test_raises_on_invalid_json(self):
        with pytest.raises(ValueError, match="not valid JSON"):
            parse_openai_consensus_response("This is not JSON at all.")

    def test_full_mock_response_round_trips(self):
        """The mock file parses without error and has all required keys."""
        content = json.dumps(AI_META)
        result  = parse_openai_consensus_response(content)
        for key in (
            "executive_summary", "key_findings", "impact_level", "urgency_level",
            "reliability_score", "sentiment_score", "extracted_entities",
            "topic_classification", "fact_check_flag", "consensus_rating",
            "uncertainty_index",
        ):
            assert key in result, f"Missing key in parsed AI meta: {key}"


# ==========================================================
# Gate 2-G.3 — build_gold_social_pulse
# ==========================================================

class TestBuildGoldSocialPulse:
    """Verify full Gold Social Pulse (Section C.6) assembly."""

    def _record(self, **kwargs):
        silver = {**SILVER_SOCIAL, **kwargs}
        return build_gold_social_pulse(silver, AI_META, MOCK_EMBEDDING)

    def test_all_top_level_keys_present(self):
        record = self._record()
        for key in ("metadata", "content_vitals", "enrichment_ai",
                    "social_context", "platform_logic", "embedding"):
            assert key in record, f"Missing top-level key: {key}"

    def test_metadata_source_platform(self):
        record = self._record()
        assert record["metadata"]["source_platform"] == "polymarket"

    def test_metadata_canonical_event_id_matches_bronze_ref(self):
        record = self._record()
        assert record["metadata"]["canonical_event_id"] == SILVER_SOCIAL["bronze_ref"]

    def test_metadata_silver_data_ref_is_none_before_patching(self):
        """
        silver_data_ref must be None immediately after build, before the caller
        patches it with social_vault.social_id (T1, Sprint 11, Section C.6).

        None is the correct sentinel — a UUID pointing to bronze_ref would be a
        misleading reference to the wrong table.
        """
        record = self._record()
        assert record["metadata"]["silver_data_ref"] is None

    def test_metadata_signal_id_is_valid_uuid(self):
        record = self._record()
        uuid.UUID(record["metadata"]["signal_id"])  # raises if not valid UUID

    def test_content_vitals_title_is_question(self):
        record = self._record()
        assert record["content_vitals"]["title"] == SILVER_SOCIAL["question"]

    def test_content_vitals_url_contains_market_id(self):
        record = self._record()
        assert SILVER_SOCIAL["market_id"] in record["content_vitals"]["url"]

    def test_enrichment_ai_impact_level_is_int(self):
        record = self._record()
        assert isinstance(record["enrichment_ai"]["impact_level"], int)
        assert record["enrichment_ai"]["impact_level"] == AI_META["impact_level"]

    def test_enrichment_ai_reliability_score_is_float(self):
        record = self._record()
        rs = record["enrichment_ai"]["reliability_score"]
        assert isinstance(rs, float)
        assert rs == pytest.approx(AI_META["reliability_score"])

    def test_enrichment_ai_entities_present(self):
        record = self._record()
        assert "Federal Reserve" in record["enrichment_ai"]["extracted_entities"]

    def test_enrichment_ai_fact_check_flag_is_bool(self):
        record = self._record()
        assert isinstance(record["enrichment_ai"]["fact_check_flag"], bool)

    def test_social_context_community_name(self):
        record = self._record()
        assert record["social_context"]["community_name"] == "Polymarket"

    def test_social_context_engagement_score_equals_comment_count(self):
        record = self._record()
        assert record["social_context"]["primary_engagement_score"] == len(
            SILVER_SOCIAL["raw_comments"]
        )

    def test_platform_logic_entry_type(self):
        record = self._record()
        assert record["platform_logic"]["entry_type"] == "market_consensus"

    def test_platform_logic_market_id_ref(self):
        record = self._record()
        assert record["platform_logic"]["market_id_ref"] == SILVER_SOCIAL["market_id"]

    def test_platform_logic_comment_volume_analyzed(self):
        record = self._record()
        assert record["platform_logic"]["comment_volume_analyzed"] == len(
            SILVER_SOCIAL["raw_comments"]
        )

    def test_embedding_identity(self):
        """Embedding list must be passed through unchanged (no copy)."""
        record = self._record()
        assert record["embedding"] is MOCK_EMBEDDING

    def test_gold_schema_validator_passes(self):
        """validate_gold_social_pulse() must accept the assembled record."""
        record = self._record()
        result = validate_gold_social_pulse(record)
        assert result.is_valid, f"Validator errors: {result.errors}"


# ==========================================================
# Gate 2-G.4 — compute_momentum_block
# ==========================================================

class TestComputeMomentumBlock:
    """Verify momentum delta computation with various price history inputs."""

    # Fixed reference point so all assertions are wall-clock independent
    _now = datetime(2026, 3, 28, 14, 0, 0, tzinfo=timezone.utc)

    def test_empty_history_all_zeros(self):
        mb = compute_momentum_block(0.67, [], _now=self._now)
        assert mb["change_24h"]    == 0.0
        assert mb["change_7d"]     == 0.0
        assert mb["change_30d"]    == 0.0

    def test_empty_history_is_new_market_true(self):
        mb = compute_momentum_block(0.67, [], _now=self._now)
        assert mb["is_new_market"] is True

    def test_populated_history_is_new_market_false(self):
        ts = (self._now - timedelta(hours=25)).isoformat()
        mb = compute_momentum_block(0.67, [(ts, 0.60)], _now=self._now)
        assert mb["is_new_market"] is False

    def test_24h_change_computed_correctly(self):
        # Reference at 25h ago → qualifies as the 24h lookback reference
        ts_25h = (self._now - timedelta(hours=25)).isoformat()
        mb = compute_momentum_block(0.67, [(ts_25h, 0.60)], _now=self._now)
        # (0.67 - 0.60) / 0.60 = 0.116667
        assert mb["change_24h"] == pytest.approx(0.116667, abs=1e-5)

    def test_no_7d_reference_when_only_24h_history(self):
        # A 25h-old observation is too recent to serve as the 7-day reference
        ts_25h = (self._now - timedelta(hours=25)).isoformat()
        mb = compute_momentum_block(0.67, [(ts_25h, 0.60)], _now=self._now)
        assert mb["change_7d"]  == 0.0
        assert mb["change_30d"] == 0.0

    def test_all_three_deltas_with_full_history(self):
        history = [
            ((self._now - timedelta(days=35)).isoformat(), 0.50),  # used for 30d
            ((self._now - timedelta(days=8)).isoformat(),  0.55),  # used for 7d
            ((self._now - timedelta(hours=26)).isoformat(), 0.62), # used for 24h
        ]
        mb = compute_momentum_block(0.70, history, _now=self._now)
        # change_24h = (0.70 - 0.62) / 0.62 ≈ 0.12903
        assert mb["change_24h"] == pytest.approx(0.129032, abs=1e-4)
        # change_7d = (0.70 - 0.55) / 0.55 ≈ 0.27273
        assert mb["change_7d"]  == pytest.approx(0.272727, abs=1e-4)
        # change_30d = (0.70 - 0.50) / 0.50 = 0.4
        assert mb["change_30d"] == pytest.approx(0.4, abs=1e-5)

    def test_zero_reference_value_returns_zero_delta(self):
        """Division by zero must be guarded — reference value of 0.0 → 0.0 delta."""
        ts_25h = (self._now - timedelta(hours=25)).isoformat()
        mb = compute_momentum_block(0.67, [(ts_25h, 0.0)], _now=self._now)
        assert mb["change_24h"] == 0.0

    def test_most_recent_before_target_is_used(self):
        """When multiple observations precede the target window, use the most recent."""
        history = [
            ((self._now - timedelta(hours=48)).isoformat(), 0.50),  # older
            ((self._now - timedelta(hours=26)).isoformat(), 0.60),  # more recent
        ]
        mb = compute_momentum_block(0.70, history, _now=self._now)
        # change_24h should use 0.60 (closer to 24h cutoff), not 0.50
        assert mb["change_24h"] == pytest.approx((0.70 - 0.60) / 0.60, abs=1e-5)


# ==========================================================
# Gate 2-G.5 — build_gold_structured_metric
# ==========================================================

class TestBuildGoldStructuredMetric:
    """Verify Gold Structured Metric assembly from Silver + momentum."""

    _momentum = {
        "change_24h": 0.05, "change_7d": 0.10,
        "change_30d": 0.15, "is_new_market": False,
    }

    def test_layer_set_to_gold(self):
        result = build_gold_structured_metric(SILVER_METRIC, self._momentum)
        assert result["layer"] == "Gold"

    def test_momentum_block_replaced(self):
        result = build_gold_structured_metric(SILVER_METRIC, self._momentum)
        assert result["momentum_block"]["change_24h"] == pytest.approx(0.05)
        assert result["momentum_block"]["change_7d"]  == pytest.approx(0.10)
        assert result["momentum_block"]["change_30d"] == pytest.approx(0.15)

    def test_whale_alert_false_when_not_flagged(self):
        result = build_gold_structured_metric(SILVER_METRIC, self._momentum)
        assert result["whale_alert"] is False

    def test_whale_alert_true_when_flagged(self):
        metric = {
            **SILVER_METRIC,
            "metadata_extension": {
                **SILVER_METRIC["metadata_extension"],
                "whale_alert": True,
            },
        }
        result = build_gold_structured_metric(metric, self._momentum)
        assert result["whale_alert"] is True

    def test_original_silver_metric_not_mutated(self):
        """Deep copy must prevent in-place mutation of the input record."""
        original_layer = SILVER_METRIC["layer"]
        original_mb    = SILVER_METRIC["momentum_block"].copy()
        build_gold_structured_metric(SILVER_METRIC, self._momentum)
        assert SILVER_METRIC["layer"]          == original_layer
        assert SILVER_METRIC["momentum_block"] == original_mb

    def test_core_identity_preserved(self):
        result = build_gold_structured_metric(SILVER_METRIC, self._momentum)
        assert result["core_identity"] == SILVER_METRIC["core_identity"]

    def test_data_point_preserved(self):
        result = build_gold_structured_metric(SILVER_METRIC, self._momentum)
        assert result["data_point"] == SILVER_METRIC["data_point"]


# ==========================================================
# Gate 2-G.6 — process_social_pulse_message (full social path)
# ==========================================================

class TestProcessSocialPulseMessage:
    """End-to-end test of the Gold Social Pulse dispatch function."""

    def test_returns_gold_topic_and_well_formed_record(self):
        client = _make_mock_openai_client()
        topic, record = process_social_pulse_message(SILVER_SOCIAL, openai_client=client)
        assert topic == GOLD_SOCIAL_PULSE
        assert isinstance(record, dict)

    def test_returned_record_has_embedding(self):
        client = _make_mock_openai_client()
        _, record = process_social_pulse_message(SILVER_SOCIAL, openai_client=client)
        assert "embedding" in record
        assert len(record["embedding"]) == EMBEDDING_DIM

    def test_returned_record_passes_gold_validator(self):
        client = _make_mock_openai_client()
        _, record = process_social_pulse_message(SILVER_SOCIAL, openai_client=client)
        result = validate_gold_social_pulse(record)
        assert result.is_valid, f"Validator errors: {result.errors}"

    def test_invalid_silver_schema_routes_to_dlq(self):
        """Silver record missing market_id (but has raw_comments) → DLQ."""
        # Must include non-empty raw_comments so the empty-batch guard is bypassed
        # before validate_silver_social() is reached.
        invalid = {
            "source_name":  "polymarket",
            "ingested_at":  "2026-01-01T00:00:00Z",
            "raw_comments": [{"comment_id": "c1", "text": "test"}],
            # market_id intentionally omitted → validate_silver_social fails
        }
        client = _make_mock_openai_client()
        topic, record = process_social_pulse_message(invalid, openai_client=client)
        assert topic == DEAD_LETTER_QUEUE
        assert "validation_errors" in record

    def test_empty_comments_returns_none_tuple(self):
        """Empty comment batch → (None, None), not an error (Section C.3)."""
        social = {**SILVER_SOCIAL, "raw_comments": []}
        client = _make_mock_openai_client()
        topic, record = process_social_pulse_message(social, openai_client=client)
        assert topic  is None
        assert record is None

    def test_openai_consensus_error_routes_to_dlq(self):
        client = MagicMock()
        client.chat.completions.create.side_effect = Exception("API quota exceeded")
        topic, record = process_social_pulse_message(SILVER_SOCIAL, openai_client=client)
        assert topic == DEAD_LETTER_QUEUE

    def test_openai_embedding_error_routes_to_dlq(self):
        client = MagicMock()
        # Consensus call succeeds; embedding call fails
        client.chat.completions.create.return_value.choices[0].message.content = (
            json.dumps(AI_META)
        )
        client.embeddings.create.side_effect = Exception("Embedding timeout")
        topic, record = process_social_pulse_message(SILVER_SOCIAL, openai_client=client)
        assert topic == DEAD_LETTER_QUEUE

    def test_dlq_record_has_required_diagnostic_fields(self):
        """DLQ records must carry all fields needed for incident investigation."""
        invalid = {
            "source_name":  "polymarket",
            "ingested_at":  "2026-01-01T00:00:00Z",
            "raw_comments": [{"comment_id": "c1", "text": "test"}],
        }
        client = _make_mock_openai_client()
        _, dlq = process_social_pulse_message(invalid, openai_client=client)
        for field in ("dlq_id", "failed_at", "failed_layer", "failed_stage",
                      "validation_errors", "original_message"):
            assert field in dlq, f"DLQ record missing field: {field}"

    def test_dlq_failed_layer_is_gold(self):
        invalid = {
            "source_name":  "polymarket",
            "ingested_at":  "2026-01-01T00:00:00Z",
            "raw_comments": [{"comment_id": "c1", "text": "test"}],
        }
        client = _make_mock_openai_client()
        _, dlq = process_social_pulse_message(invalid, openai_client=client)
        assert dlq["failed_layer"] == "Gold"


# ==========================================================
# Gate 2-G.7 — process_structured_metrics_message (momentum path)
# ==========================================================

class TestProcessStructuredMetricsMessage:
    """End-to-end test of the Gold Structured Metrics dispatch function."""

    def test_returns_gold_metrics_topic(self):
        topic, _ = process_structured_metrics_message(SILVER_METRIC, [])
        assert topic == GOLD_STRUCTURED_METRICS

    def test_empty_history_is_new_market(self):
        _, gold = process_structured_metrics_message(SILVER_METRIC, [])
        assert gold["momentum_block"]["is_new_market"] is True

    def test_empty_history_all_deltas_zero(self):
        _, gold = process_structured_metrics_message(SILVER_METRIC, [])
        assert gold["momentum_block"]["change_24h"] == 0.0
        assert gold["momentum_block"]["change_7d"]  == 0.0
        assert gold["momentum_block"]["change_30d"] == 0.0

    def test_layer_is_gold(self):
        _, gold = process_structured_metrics_message(SILVER_METRIC, [])
        assert gold["layer"] == "Gold"

    def test_whale_alert_false_by_default(self):
        _, gold = process_structured_metrics_message(SILVER_METRIC, [])
        assert gold["whale_alert"] is False

    def test_whale_alert_true_when_flagged_in_silver(self):
        metric = {
            **SILVER_METRIC,
            "metadata_extension": {
                **SILVER_METRIC["metadata_extension"],
                "whale_alert": True,
            },
        }
        _, gold = process_structured_metrics_message(metric, [])
        assert gold["whale_alert"] is True


# ==========================================================
# Sprint 11 T1 — silver_data_ref wiring (Gap 1)
# ==========================================================

class TestSilverDataRefWiring:
    """
    Validates the silver_data_ref None-then-patch pattern (Sprint 11 T1).

    Design: the builder emits None so there is no misleading UUID pointing
    to the wrong table. The Flink caller patches the field with the actual
    social_vault.social_id returned by sv_archive() after archiving succeeds.
    raw_data_ref always holds the Bronze envelope UUID and is never changed.
    """

    def test_silver_data_ref_is_none_before_patching(self):
        """
        build_gold_social_pulse() must set silver_data_ref=None (T1, Sprint 11).

        None is the correct sentinel when the social_vault.social_id is not yet
        known. Setting it to bronze_ref would be a misleading reference — the
        Bronze UUID points to the ingest layer, not the Silver social store.
        """
        silver = {**SILVER_SOCIAL}
        record = build_gold_social_pulse(silver, AI_META, MOCK_EMBEDDING)
        assert record["metadata"]["silver_data_ref"] is None

    def test_raw_data_ref_is_set_to_bronze_ref(self):
        """raw_data_ref must be the Bronze envelope UUID, never None."""
        record = build_gold_social_pulse(SILVER_SOCIAL, AI_META, MOCK_EMBEDDING)
        assert record["metadata"]["raw_data_ref"] == SILVER_SOCIAL["bronze_ref"]

    def test_silver_data_ref_can_be_patched_to_social_id(self):
        """
        After build, the caller patches silver_data_ref with social_vault.social_id.

        This simulates what PolymarketGoldSocialFunction.process_element() does:
            social_id = sv_archive(silver_social)
            record["metadata"]["silver_data_ref"] = social_id
        After patching, silver_data_ref must hold the social_vault UUID.
        """
        social_id = str(uuid.uuid4())
        record = build_gold_social_pulse(SILVER_SOCIAL, AI_META, MOCK_EMBEDDING)
        record["metadata"]["silver_data_ref"] = social_id
        assert record["metadata"]["silver_data_ref"] == social_id

    def test_after_patch_silver_data_ref_differs_from_raw_data_ref(self):
        """
        After patching, silver_data_ref (social_vault PK) != raw_data_ref (bronze UUID).

        raw_data_ref is immutable — it always points to the Bronze envelope.
        silver_data_ref is updated to the Silver social_vault row. If they are
        equal after patching, the patch used the wrong value.
        """
        social_id = str(uuid.uuid4())
        record = build_gold_social_pulse(SILVER_SOCIAL, AI_META, MOCK_EMBEDDING)
        record["metadata"]["silver_data_ref"] = social_id
        assert record["metadata"]["silver_data_ref"] != record["metadata"]["raw_data_ref"]


# ==========================================================
# Sprint 11 T2 — deterministic signal_id (Gap 2)
# ==========================================================

class TestDeterministicSignalId:
    """
    Validates UUID5-based deterministic signal_id in both the helper and builder.

    The real dedup bug was uuid4() on every call — the ON CONFLICT (signal_id)
    DO NOTHING guard in social_vectors.insert() could never fire because every
    pipeline restart produced a new UUID. UUID5 from content_hash makes signal_id
    stable, allowing the guard to deduplicate on re-delivery (Sprint 11 T2).
    """

    HASH_A = "a" * 64
    HASH_B = "b" * 64

    # --- _deterministic_signal_id helper ---

    def test_helper_returns_uuid5_for_non_empty_hash(self):
        """Non-empty content_hash must produce a UUID version 5 string."""
        result = _deterministic_signal_id(self.HASH_A)
        assert uuid.UUID(result).version == 5

    def test_helper_same_hash_same_uuid(self):
        """Same content_hash must always produce the same UUID5."""
        assert _deterministic_signal_id(self.HASH_A) == _deterministic_signal_id(self.HASH_A)

    def test_helper_different_hash_different_uuid(self):
        """Different content_hashes must produce different UUID5 strings."""
        assert _deterministic_signal_id(self.HASH_A) != _deterministic_signal_id(self.HASH_B)

    def test_helper_empty_hash_returns_valid_uuid(self):
        """Empty content_hash falls back to uuid4() — must still be a valid UUID."""
        result = _deterministic_signal_id("")
        uuid.UUID(result)  # raises ValueError if not a valid UUID

    # --- build_gold_social_pulse() propagation ---

    def test_builder_signal_id_is_deterministic(self):
        """
        Two calls with the same content_hash must produce the same signal_id.

        This is the exact condition needed for ON CONFLICT (signal_id) DO NOTHING
        to fire on Flink re-deliveries and E2E reruns (Sprint 11 T2).
        """
        silver_a = {**SILVER_SOCIAL, "content_hash": self.HASH_A}
        silver_b = {**SILVER_SOCIAL, "content_hash": self.HASH_A}
        r1 = build_gold_social_pulse(silver_a, AI_META, MOCK_EMBEDDING)
        r2 = build_gold_social_pulse(silver_b, AI_META, MOCK_EMBEDDING)
        assert r1["metadata"]["signal_id"] == r2["metadata"]["signal_id"]

    def test_builder_signal_id_differs_for_different_content_hash(self):
        """Different batches (different content_hash) must produce different signal_ids."""
        silver_a = {**SILVER_SOCIAL, "content_hash": self.HASH_A}
        silver_b = {**SILVER_SOCIAL, "content_hash": self.HASH_B}
        r1 = build_gold_social_pulse(silver_a, AI_META, MOCK_EMBEDDING)
        r2 = build_gold_social_pulse(silver_b, AI_META, MOCK_EMBEDDING)
        assert r1["metadata"]["signal_id"] != r2["metadata"]["signal_id"]
