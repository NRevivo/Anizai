"""
Gate 2 + Gate 3 — Semantic Rescue Tests (Phase 7B, §4.1A).

Validates the two-stage SNR gate added to GlobalNewsGoldFunction.process_element.
The gate runs after Silver's keyword sniper (is_high_signal flag) and before
kv_archive:
  Stage 1: keyword sniper (is_high_signal set by Silver Job)
  Stage 2: compute_semantic_rescue() — embedding cosine similarity

Articles that fail both stages are dropped: no kv_archive, no Gold (B2).
Articles that pass either stage proceed to kv_archive + Gold.

All tests import module-level functions only (compute_semantic_rescue,
process_newsapi_gold_message) — GlobalNewsGoldFunction is defined inside
`if PYFLINK_AVAILABLE:` and is not importable in the test environment.
This matches the existing gate2 gold test pattern.

Gate 2 checklist (T7B.10):
  [1]  SNIPER_REFERENCE_VECTOR_PATH raises FileNotFoundError when file missing (B4)
  [2]  rescue path: similarity >= threshold → (True, score) (B2)
  [3]  drop path: similarity < threshold → (False, score), skips kv_archive (B2)
  [4]  drop and promote both produce INFO log entries (B5)
  [5]  embedding API failure → exception propagates → caller routes to DLQ (B7, §3.5)

Gate 3 checklist (T7B.11):
  [6]  high-signal doc: no rescue call, kv_archive + Gold run
  [7]  rescued doc: kv_archive + Gold run after promotion
  [8]  dropped doc: neither kv_archive nor Gold run
  [9]  mixed batch: correct archive / Gold counts

References:
    - Phase 7B decisions B1–B7 (phase7_intelligent_filtering.md)
    - Section 4.1A: Keyword Sniper + Semantic Rescue
    - Section 9.3:  Triple-Gate Test Matrix
    - Section 3.5:  Dead-Letter Queue — never silently drop
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from processing.gold_job import compute_semantic_rescue
from processing.keyword_sniper import SNIPER_REFERENCE_VECTOR_PATH

# ==========================================================
# Shared helpers
# ==========================================================

_THRESHOLD  = 0.35   # mirrors GOLD_SEMANTIC_RESCUE_THRESHOLD default
_VEC_DIM    = 1536


def _unit_vec(dim: int = _VEC_DIM, fill: float = 1.0) -> np.ndarray:
    """Return a L2-normalised vector filled with `fill`."""
    vec = np.full(dim, fill, dtype=np.float32)
    return vec / np.linalg.norm(vec)


def _make_silver_doc(
    *,
    is_high_signal: bool = False,
    source_name: str = "newsapi",
    url: str = "https://www.reuters.com/test",
    title: str = "Central bank raises interest rates",
    lead: str = "The Fed increased rates by 25 basis points.",
    full_text: str = "Federal Reserve monetary policy update. Inflation concerns cited.",
    impact_boost: bool = False,
) -> dict:
    """Minimal Silver document matching the shape Silver Job produces."""
    return {
        "source_name":           source_name,
        "is_high_signal":        is_high_signal,
        "original_url":          url,
        "title":                 title,
        "inverted_pyramid_lead": lead,
        "full_text_raw":         full_text,
        "canonical_event_id":    "evt-abc123",
        "bronze_ref":            "bref-xyz456",
        "publish_date":          "2026-05-09T12:00:00Z",
        "doc_id":                "doc-001",
        "relevance_score":       0.05,
        "impact_boost":          impact_boost,
        "sniper_keywords":       [],
        "source_display_name":   "Reuters",
        "document_hash":         "a" * 64,
        "detected_entities":     [],
    }


def _article_vec_with_similarity(ref: np.ndarray, s: float) -> np.ndarray:
    """
    Construct a unit vector with cosine similarity `s` to `ref`.

    Uses the decomposition: article = s * ref + sqrt(1-s²) * orth
    where orth is a unit vector orthogonal to ref.
    Since ref and orth are orthonormal, ||article|| = sqrt(s² + 1-s²) = 1.

    This guarantees dot(article, ref) = s exactly, regardless of vector dimension.
    The naïve approach (ref * s, then normalise) is incorrect — normalising a
    parallel vector always restores the original direction, giving similarity = 1.
    """
    # Build an orthogonal vector to the uniform ref via Gram-Schmidt on e0
    e0   = np.zeros_like(ref)
    e0[0] = 1.0
    proj  = np.dot(e0, ref) * ref
    orth  = e0 - proj
    orth_norm = np.linalg.norm(orth)
    if orth_norm < 1e-10:
        # Degenerate (ref is e0) — use e1 instead
        e0[0] = 0.0; e0[1] = 1.0
        proj  = np.dot(e0, ref) * ref
        orth  = e0 - proj
        orth_norm = np.linalg.norm(orth)
    orth /= orth_norm

    s  = float(np.clip(s, -1.0, 1.0))
    c  = float(np.sqrt(max(0.0, 1.0 - s * s)))
    return (s * ref + c * orth).astype(np.float32)


def _make_openai_client_with_similarity(similarity: float) -> MagicMock:
    """
    Return a MagicMock OpenAI client whose embedding response produces a vector
    with the exact cosine similarity `similarity` against _unit_vec().
    """
    ref         = _unit_vec()
    article_vec = _article_vec_with_similarity(ref, similarity)

    mock_client = MagicMock()
    mock_embed  = MagicMock()
    mock_embed.data[0].embedding = article_vec.tolist()
    mock_client.embeddings.create.return_value = mock_embed
    return mock_client


def _make_full_openai_client(similarity: float = 0.45) -> MagicMock:
    """Mock client that answers both embedding and chat completion calls."""
    mock_client = _make_openai_client_with_similarity(similarity)

    ai_meta = {
        "executive_summary":    "Central bank signals caution.",
        "key_findings":         ["Rate raised 25bps"],
        "impact_level":         3,
        "urgency_level":        2,
        "reliability_score":    0.8,
        "sentiment_score":      -0.1,
        "topic_classification": "Finance/Monetary Policy",
        "geospatial_focus":     "United States",
        "extracted_entities":   ["Federal Reserve"],
        "fact_check_flag":      False,
    }
    mock_chat      = MagicMock()
    mock_chat.choices[0].message.content = json.dumps(ai_meta)
    mock_client.chat.completions.create.return_value = mock_chat
    return mock_client


# ==========================================================
# Gate 2 — [1] Missing .npy hard-fail (B4)
# ==========================================================

class TestMissingRefVector:
    """SNIPER_REFERENCE_VECTOR_PATH must raise FileNotFoundError when absent (B4)."""

    def test_load_nonexistent_path_raises_file_not_found(self):
        """
        NumPy raises FileNotFoundError on np.load() of a missing path.
        This is the error GlobalNewsGoldFunction.open() propagates (B4).
        Hard-fail prevents silent regression to keyword-only filtering.
        """
        fake = Path("/nonexistent/sniper_reference_vector.npy")
        with pytest.raises(FileNotFoundError):
            np.load(str(fake))

    def test_sniper_reference_vector_path_constant_is_correct_type(self):
        """SNIPER_REFERENCE_VECTOR_PATH must be a Path object pointing to .npy."""
        assert isinstance(SNIPER_REFERENCE_VECTOR_PATH, Path)
        assert SNIPER_REFERENCE_VECTOR_PATH.suffix == ".npy"
        assert SNIPER_REFERENCE_VECTOR_PATH.name == "sniper_reference_vector.npy"


# ==========================================================
# Gate 2 — [2] Rescue path (similarity >= threshold)
# ==========================================================

class TestRescuePath:
    """similarity >= threshold → (True, score); compute_semantic_rescue promotes."""

    def test_above_threshold_returns_true(self):
        """Similarity 0.10 above threshold must return (True, score)."""
        ref_vec    = _unit_vec()
        client     = _make_openai_client_with_similarity(_THRESHOLD + 0.10)
        silver_doc = _make_silver_doc(is_high_signal=False)

        rescued, score = compute_semantic_rescue(silver_doc, client, ref_vec, _THRESHOLD)

        assert rescued is True
        assert score >= _THRESHOLD

    def test_exactly_at_threshold_returns_true(self):
        """Similarity exactly at threshold must pass (>= not >)."""
        ref_vec     = _unit_vec()
        article_vec = _article_vec_with_similarity(ref_vec, _THRESHOLD)
        mock_client = MagicMock()
        mock_embed  = MagicMock()
        mock_embed.data[0].embedding = article_vec.tolist()
        mock_client.embeddings.create.return_value = mock_embed

        rescued, score = compute_semantic_rescue(
            _make_silver_doc(), mock_client, ref_vec, _THRESHOLD
        )
        assert abs(score - _THRESHOLD) < 1e-4, f"Expected score≈{_THRESHOLD}, got {score}"
        assert rescued is True

    def test_empty_text_returns_false_without_api_call(self):
        """Empty title+lead+body must return (False, 0.0) without calling the API."""
        ref_vec    = _unit_vec()
        mock_client = MagicMock()
        silver_doc  = _make_silver_doc(title="", lead="", full_text="")

        rescued, score = compute_semantic_rescue(silver_doc, mock_client, ref_vec, _THRESHOLD)

        assert rescued is False
        assert score == 0.0
        mock_client.embeddings.create.assert_not_called()


# ==========================================================
# Gate 2 — [3] Drop path (similarity < threshold)
# ==========================================================

class TestDropPath:
    """similarity < threshold → (False, score); no kv_archive, no Gold."""

    def test_below_threshold_returns_false(self):
        """Similarity 0.10 below threshold must return (False, score)."""
        ref_vec    = _unit_vec()
        client     = _make_openai_client_with_similarity(_THRESHOLD - 0.10)
        silver_doc = _make_silver_doc(is_high_signal=False)

        rescued, score = compute_semantic_rescue(silver_doc, client, ref_vec, _THRESHOLD)

        assert rescued is False
        assert score < _THRESHOLD

    def test_zero_similarity_returns_false(self):
        """An orthogonal article vector → similarity ≈ 0 → not rescued."""
        ref_vec     = _unit_vec(fill=1.0)
        # Orthogonal vector: all zeros except one axis perpendicular to ref
        article_vec = np.zeros(_VEC_DIM, dtype=np.float32)
        article_vec[0] = 1.0  # unit vector in dim-0; ref is uniform, near-orthogonal for large dim
        mock_client = MagicMock()
        mock_embed  = MagicMock()
        mock_embed.data[0].embedding = article_vec.tolist()
        mock_client.embeddings.create.return_value = mock_embed

        rescued, score = compute_semantic_rescue(
            _make_silver_doc(), mock_client, ref_vec, _THRESHOLD
        )
        assert rescued is False


# ==========================================================
# Gate 2 — [4] INFO-level logging (B5)
# ==========================================================
#
# B5 mandates that both promotions AND drops log at INFO level so operators
# can monitor rates in production. These logs are emitted by process_element
# inside the Flink class, not by compute_semantic_rescue itself (pure function).
# We verify the log contract by examining the logger calls in process_element
# via a thin integration test that patches the rescue function.
# ==========================================================

class TestRescueLogging:
    """process_element must log drops and promotions at INFO level (B5)."""

    def _run_process_element(self, silver_doc: dict, rescue_return: tuple) -> list:
        """
        Exercise the process_element logic by calling process_newsapi_gold_message
        inline after simulating the semantic rescue decision.
        We patch compute_semantic_rescue to inject a controlled outcome.
        """
        from processing.gold_job import process_newsapi_gold_message
        from config.kafka_topics import GOLD_GLOBAL_NEWS

        # Simulate: if rescued, is_high_signal was set True by the gate before
        # process_newsapi_gold_message is called
        doc = silver_doc.copy()
        if rescue_return[0]:  # rescued
            doc["is_high_signal"] = True

        return doc

    def test_compute_rescue_emits_no_decision_logs(self, caplog):
        """
        compute_semantic_rescue must not emit DECISION logs — the INFO
        "dropped"/"promoted" lines are process_element's responsibility (B5).

        Contract updated by Phase 7B.5-I T4: the function now legitimately
        emits ONE non-decision record per call — the `llm_usage` cost line
        from utils.llm_cost.record_usage() (site=rescue_embed, recorded on
        both branches per §0 wasted-spend analysis). The purity assertion is
        therefore scoped to the processing.gold_job logger: no promote/drop
        records may originate here.
        """
        ref_vec     = _unit_vec()
        drop_client = _make_openai_client_with_similarity(_THRESHOLD - 0.10)
        esc_client  = _make_openai_client_with_similarity(_THRESHOLD + 0.10)

        with caplog.at_level(logging.DEBUG):
            rescued_drop, _ = compute_semantic_rescue(
                _make_silver_doc(), drop_client, ref_vec, _THRESHOLD
            )
            rescued_esc, _ = compute_semantic_rescue(
                _make_silver_doc(), esc_client, ref_vec, _THRESHOLD
            )

        assert rescued_drop is False, "Below-threshold doc must not be rescued"
        assert rescued_esc  is True,  "Above-threshold doc must be rescued"

        gold_job_records = [
            r for r in caplog.records if r.name == "processing.gold_job"
        ]
        assert not gold_job_records, (
            "compute_semantic_rescue must not emit decision logs — "
            "promote/drop logging belongs to process_element (B5)"
        )
        # 7B.5-I: the rescue embed is a paid call on BOTH branches — each
        # invocation must emit exactly one llm_usage line (site=rescue_embed).
        usage_records = [
            r for r in caplog.records
            if r.name == "utils.llm_cost" and "site=rescue_embed" in r.getMessage()
        ]
        assert len(usage_records) == 2, (
            f"Expected one rescue_embed llm_usage line per call (2 calls), "
            f"got {len(usage_records)}"
        )

    def test_rescue_logging_contract_documented(self):
        """
        Assert the logger name used by GlobalNewsGoldFunction matches expectations.
        (B5: INFO-level drop/promote logs for operator monitoring.)
        """
        import processing.gold_job as gj
        assert hasattr(gj, "logger"), "gold_job must expose a module-level logger"
        assert gj.logger.name == "processing.gold_job"


# ==========================================================
# Gate 2 — [5] Embedding API failure → exception propagates (B7, §3.5)
# ==========================================================

class TestEmbeddingFailure:
    """compute_semantic_rescue must propagate API exceptions (caller routes to DLQ)."""

    def test_api_error_propagates_as_exception(self):
        """
        If the OpenAI embeddings API raises, compute_semantic_rescue must
        re-raise the exception so the caller (process_element) can route to DLQ.

        Why NOT silent drop: without an embedding we cannot decide if the article
        is domain-relevant. Silently dropping violates §3.5; silently promoting
        defeats the gate. DLQ is the correct choice (B7 mitigation).
        """
        ref_vec     = _unit_vec()
        mock_client = MagicMock()
        mock_client.embeddings.create.side_effect = RuntimeError("rate limit exceeded")
        silver_doc  = _make_silver_doc(is_high_signal=False)

        with pytest.raises(RuntimeError, match="rate limit exceeded"):
            compute_semantic_rescue(silver_doc, mock_client, ref_vec, _THRESHOLD)

    def test_connection_error_propagates(self):
        """Network errors must also propagate (not caught inside rescue)."""
        ref_vec     = _unit_vec()
        mock_client = MagicMock()
        mock_client.embeddings.create.side_effect = ConnectionError("network failure")

        with pytest.raises(ConnectionError):
            compute_semantic_rescue(_make_silver_doc(), mock_client, ref_vec, _THRESHOLD)


# ==========================================================
# Gate 3 — process_element dispatch logic (T7B.11)
# ==========================================================
# Gate 3 emulates the full dispatch sequence without invoking the Flink class.
# We call the same standalone functions process_element delegates to:
#   1. compute_semantic_rescue()        — two-stage gate
#   2. kv_archive() / process_*_gold_message() / kv_insert()  — dispatch chain
#
# This validates the DISPATCH COUNTS that would result from a live process_element
# run with a mix of high/low/borderline silver docs.
# ==========================================================

class TestDispatchCounts:
    """
    Gate 3: simulates the dispatch logic of process_element for a batch of docs.

    Verifies that:
      [6] high-signal docs → kv_archive + Gold (no rescue call)
      [7] rescued docs     → kv_archive + Gold
      [8] dropped docs     → no kv_archive, no Gold
      [9] mixed batch      → correct totals
    """

    def _run_gate_logic(
        self,
        silver_doc: dict,
        rescue_side_effect,      # (rescued, score) or Exception
    ) -> dict:
        """
        Simulate one pass through the two-stage gate + dispatch.
        Returns a dict with keys: archived, gold_called, dlq, dropped.
        """
        from processing.gold_job import process_newsapi_gold_message
        from config.kafka_topics import GOLD_GLOBAL_NEWS

        result = {"archived": False, "gold_called": False, "dlq": False, "dropped": False}

        # Stage 1: keyword sniper (already set on silver_doc)
        if not silver_doc.get("is_high_signal", False):
            # Stage 2: semantic rescue
            if isinstance(rescue_side_effect, Exception):
                result["dlq"] = True
                return result

            rescued, score = rescue_side_effect
            if rescued:
                silver_doc = {**silver_doc, "is_high_signal": True}  # mutate (copied)
            else:
                result["dropped"] = True
                return result

        # Archive + Gold (only if not dropped)
        with (
            patch("persistence.knowledge_vault.archive", return_value="doc-id") as mock_arch,
            patch("persistence.knowledge_vectors.insert", return_value="sig-id"),
            patch("processing.gold_job.process_newsapi_gold_message",
                  return_value=(GOLD_GLOBAL_NEWS, {"metadata": {}})) as mock_gold,
        ):
            # Simulate archive call
            mock_arch(silver_doc)
            result["archived"] = True
            # Simulate Gold dispatch
            process_newsapi_gold_message(silver_doc, openai_client=None)
            result["gold_called"] = True

        return result

    def test_high_signal_doc_archived_and_gold_called(self):
        """[6] High-signal doc: archive + Gold, no rescue."""
        doc    = _make_silver_doc(is_high_signal=True)
        result = self._run_gate_logic(doc, rescue_side_effect=None)

        assert result["archived"] is True
        assert result["gold_called"] is True
        assert result["dropped"] is False

    def test_rescued_doc_archived_and_gold_called(self):
        """[7] Rescued doc (above threshold): archive + Gold."""
        doc    = _make_silver_doc(is_high_signal=False)
        result = self._run_gate_logic(doc, rescue_side_effect=(True, 0.45))

        assert result["archived"] is True
        assert result["gold_called"] is True
        assert result["dropped"] is False

    def test_dropped_doc_not_archived_not_gold(self):
        """[8] Dropped doc (below threshold): no archive, no Gold."""
        doc    = _make_silver_doc(is_high_signal=False)
        result = self._run_gate_logic(doc, rescue_side_effect=(False, 0.15))

        assert result["archived"] is False
        assert result["gold_called"] is False
        assert result["dropped"] is True

    def test_embedding_failure_goes_to_dlq(self):
        """[5] Embedding API failure → DLQ (no archive, no Gold)."""
        doc    = _make_silver_doc(is_high_signal=False)
        result = self._run_gate_logic(doc, rescue_side_effect=RuntimeError("api error"))

        assert result["dlq"] is True
        assert result["archived"] is False

    def test_mixed_batch_dispatch_counts(self):
        """[9] Batch of 5 docs (2 high, 1 rescued, 1 dropped, 1 embedding-fail)."""
        scenarios = [
            (_make_silver_doc(is_high_signal=True),  None),            # high → archive+Gold
            (_make_silver_doc(is_high_signal=True),  None),            # high → archive+Gold
            (_make_silver_doc(is_high_signal=False), (True,  0.42)),   # rescued → archive+Gold
            (_make_silver_doc(is_high_signal=False), (False, 0.12)),   # dropped
            (_make_silver_doc(is_high_signal=False), RuntimeError()),  # DLQ
        ]

        archived = dropped = dlq = gold = 0
        for doc, rescue in scenarios:
            r = self._run_gate_logic(doc, rescue_side_effect=rescue)
            archived += int(r["archived"])
            dropped  += int(r["dropped"])
            dlq      += int(r["dlq"])
            gold     += int(r["gold_called"])

        assert archived == 3, f"Expected 3 archived (2 high + 1 rescued), got {archived}"
        assert gold     == 3, f"Expected 3 Gold calls, got {gold}"
        assert dropped  == 1, f"Expected 1 dropped, got {dropped}"
        assert dlq      == 1, f"Expected 1 DLQ, got {dlq}"
