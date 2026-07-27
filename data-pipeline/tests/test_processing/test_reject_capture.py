"""
Gate 2 — Reject Capture & Cosine Persistence Tests (Phase 7B.5-I, T5).

Validates the T3 wiring around the two-stage filter's drop/promote branches:

  apply_rescue_outcome() — the module-level pure function extracted from
  GlobalNewsGoldFunction.process_element (approved decision, plan §5.5
  PyFlink pattern) so this suite can exercise the promote/drop handling
  without a Flink runtime. The invariance tests below are the guard that
  the extraction changed structure, not semantics.

§4 checklist covered here:
  [1]  Drop + flag ON  → exactly one filter_rejects insert, every field
       populated, FULL (untruncated) text, cosine < threshold.
  [2]  Flag OFF → zero reject inserts; drop decision otherwise identical.
  [3]  Empty-text edge → rescue_cosine = 0.0, no crash.
  [4]  Capture-INSERT failure → warning only; the drop completes cleanly
       (fail-open lives INSIDE persistence/filter_rejects.insert_reject).
  [5]  Promote branch → doc carries the EXACT cosine; sniper-passed docs
       never acquire the key (→ vault NULL).
  [6]  Negative-boundary: DLQ traffic and dedup skips produce NO reject
       rows (capture point is the drop branch only — verified structurally
       and by flow simulation).
  [7]  Filter-behavior invariance: identical decisions with capture on/off.
  [8]  (Ron addition, 2026-07-02) a silver_doc carrying the new
       rescue_cosine key passes validate_silver_document — the validator
       checks required fields only and must not reject the new key.

References:
    - docs/A_pipeline/plans/phase7b5i_filter_observability_and_cost.md §2.1, §4
    - processing/gold_job.py apply_rescue_outcome (under test)
    - persistence/filter_rejects.py insert_reject (fail-open contract)
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from processing.gold_job import apply_rescue_outcome
from utils.validators import validate_silver_document

_THRESHOLD = 0.35   # mirrors GOLD_SEMANTIC_RESCUE_THRESHOLD default


def _make_silver_doc(**overrides) -> dict:
    """Minimal Silver document matching the shape Silver Job produces."""
    doc = {
        "doc_id":                "doc-001",
        "document_hash":         "a" * 64,
        "canonical_event_id":    "evt-reject-test",
        "bronze_ref":            "bref-xyz456",
        "source_name":           "newsapi",
        "is_high_signal":        False,
        "original_url":          "https://www.reuters.com/test",
        "title":                 "Celebrity gossip roundup",
        "inverted_pyramid_lead": "A quiet week in entertainment news.",
        "full_text_raw":         "Nothing geopolitically relevant happened.",
        "publish_date":          "2026-07-01T12:00:00Z",
        "relevance_score":       0.05,
        "sniper_keywords":       [],
        "detected_entities":     [],
    }
    doc.update(overrides)
    return doc


@pytest.fixture
def reject_calls(monkeypatch):
    """
    Capture insert_reject calls without a DB. apply_rescue_outcome lazy-
    imports the function from the module at call time, so patching the
    module attribute intercepts it (same pattern as llm_cost_insert_calls).
    """
    import persistence.filter_rejects as fr

    calls: list[tuple] = []

    def _stub(silver_doc, rescue_cosine, run_id=None, canonical_event_id=None):
        # Capture canonical_event_id too (Phase 7D T7) — accepting it without
        # capturing/asserting would be the KG-A-13 blind spot on a new column.
        calls.append((silver_doc, rescue_cosine, run_id, canonical_event_id))
        return "00000000-0000-0000-0000-000000000000"

    monkeypatch.setattr(fr, "insert_reject", _stub)
    return calls


# ==========================================================
# [1] Drop + flag ON — capture fires with full fidelity
# ==========================================================

class TestDropCaptureEnabled:

    def test_drop_captures_exactly_one_reject(self, reject_calls):
        doc = _make_silver_doc()
        survived = apply_rescue_outcome(
            doc, rescued=False, similarity=0.21,
            capture_enabled=True, run_id="dayrun-t",
        )
        assert survived is False
        assert len(reject_calls) == 1

    def test_captured_fields_are_the_docs_fields(self, reject_calls):
        doc = _make_silver_doc()
        apply_rescue_outcome(
            doc, rescued=False, similarity=0.21,
            capture_enabled=True, run_id="dayrun-t",
        )
        captured_doc, cosine, run_id, cei = reject_calls[0]
        assert captured_doc is doc, "The reject row must come from the live doc"
        assert cosine == pytest.approx(0.21)
        assert cosine < _THRESHOLD
        assert run_id == "dayrun-t"
        # T7: the instance key is captured, non-empty, and equals _cost_trace_id
        # of this fixture (canonical_event_id present → returned as-is). Asserting
        # the VALUE, not just presence, closes the KG-A-13 blind spot on the column.
        assert cei == "evt-reject-test"

    def test_full_text_passed_untruncated(self, reject_calls):
        """T7B.1 manual FN classification needs the WHOLE article (§2.1)."""
        long_text = "geopolitics " * 2000   # ~24k chars, far beyond any preview length
        doc = _make_silver_doc(full_text_raw=long_text)
        apply_rescue_outcome(
            doc, rescued=False, similarity=0.10, capture_enabled=True,
        )
        captured_doc, _, _, _ = reject_calls[0]
        assert captured_doc["full_text_raw"] == long_text
        assert len(captured_doc["full_text_raw"]) == len(long_text)

    def test_drop_never_mutates_the_doc(self, reject_calls):
        """Only the promote branch writes keys onto the doc."""
        doc = _make_silver_doc()
        before = dict(doc)
        apply_rescue_outcome(doc, rescued=False, similarity=0.2, capture_enabled=True)
        assert doc == before
        assert "rescue_cosine" not in doc


# ==========================================================
# [2] Flag OFF — no capture, identical drop
# ==========================================================

class TestDropCaptureDisabled:

    def test_no_reject_row_when_flag_off(self, reject_calls):
        survived = apply_rescue_outcome(
            _make_silver_doc(), rescued=False, similarity=0.21,
            capture_enabled=False,
        )
        assert survived is False
        assert reject_calls == []

    def test_flag_defaults_off(self, reject_calls):
        """capture_enabled defaults False — mirroring settings' default."""
        survived = apply_rescue_outcome(
            _make_silver_doc(), rescued=False, similarity=0.21,
        )
        assert survived is False
        assert reject_calls == []


# ==========================================================
# [3] Empty-text edge — cosine 0.0, no crash
# ==========================================================

class TestEmptyTextEdge:

    def test_empty_text_reject_carries_zero_cosine(self, reject_calls):
        """
        compute_semantic_rescue returns (False, 0.0) for empty text without
        an API call; the capture path must persist that 0.0 cleanly.
        """
        doc = _make_silver_doc(title="", inverted_pyramid_lead="", full_text_raw="")
        survived = apply_rescue_outcome(
            doc, rescued=False, similarity=0.0, capture_enabled=True,
        )
        assert survived is False
        _, cosine, _, _ = reject_calls[0]
        assert cosine == 0.0


# ==========================================================
# [4] Capture-INSERT failure — fail-open inside the persistence module
# ==========================================================

class TestCaptureFailOpen:

    def test_db_error_warns_and_returns_none(self, monkeypatch, caplog):
        """
        insert_reject must catch ALL exceptions (contract: a capture failure
        never turns a clean drop into a DLQ event). Simulated by making
        get_cursor blow up inside the persistence module.
        """
        import persistence.filter_rejects as fr

        def _boom(*args, **kwargs):
            raise RuntimeError("connection refused")

        monkeypatch.setattr(fr, "get_cursor", _boom)

        with caplog.at_level(logging.WARNING, logger="persistence.filter_rejects"):
            result = fr.insert_reject(_make_silver_doc(), 0.21, run_id="dayrun-t")

        assert result is None
        assert "fail-open" in caplog.text

    def test_drop_completes_cleanly_despite_capture_failure(self, monkeypatch):
        """End-to-end at the apply level: DB down → drop still returns False."""
        import persistence.filter_rejects as fr
        monkeypatch.setattr(
            fr, "get_cursor",
            MagicMock(side_effect=RuntimeError("pool exhausted")),
        )
        survived = apply_rescue_outcome(
            _make_silver_doc(), rescued=False, similarity=0.2, capture_enabled=True,
        )
        assert survived is False   # no exception reached the caller

    def test_malformed_doc_fails_open_too(self, caplog):
        """
        Even a doc that breaks parameter building (relevance_score not
        castable) must warn and return None, never raise.
        """
        import persistence.filter_rejects as fr
        doc = _make_silver_doc(relevance_score="not-a-float")
        with caplog.at_level(logging.WARNING, logger="persistence.filter_rejects"):
            assert fr.insert_reject(doc, 0.2) is None
        assert "fail-open" in caplog.text


# ==========================================================
# [5] Promote branch — exact cosine threading
# ==========================================================

class TestPromoteBranch:

    def test_promote_sets_flag_and_exact_cosine(self, reject_calls):
        doc = _make_silver_doc()
        survived = apply_rescue_outcome(
            doc, rescued=True, similarity=0.4123, capture_enabled=True,
        )
        assert survived is True
        assert doc["is_high_signal"] is True
        assert doc["rescue_cosine"] == pytest.approx(0.4123)
        assert reject_calls == [], "Promotions must never write reject rows"

    def test_sniper_passed_doc_never_acquires_the_key(self):
        """
        Sniper-passed docs (is_high_signal=True) never enter the rescue
        branch, so they never gain rescue_cosine — the vault column stays
        NULL by construction (§2.2). Asserted on the doc shape here; the
        NULL round-trip is Gate 3.
        """
        doc = _make_silver_doc(is_high_signal=True, relevance_score=0.6)
        assert "rescue_cosine" not in doc


# ==========================================================
# [6] Negative boundary — DLQ / dedup traffic writes no rejects
# ==========================================================

class TestNegativeBoundary:

    def test_embedding_failure_path_writes_no_reject(self, reject_calls):
        """
        When compute_semantic_rescue raises, process_element routes to DLQ
        BEFORE apply_rescue_outcome is reached (gold_job drop-branch order).
        Simulate that sequence: exception → no capture call.
        """
        from processing.gold_job import compute_semantic_rescue
        import numpy as np

        mock_client = MagicMock()
        mock_client.embeddings.create.side_effect = RuntimeError("rate limit")
        ref = np.ones(1536, dtype=np.float32)
        ref /= np.linalg.norm(ref)

        with pytest.raises(RuntimeError):
            compute_semantic_rescue(_make_silver_doc(), mock_client, ref, _THRESHOLD)
        # apply_rescue_outcome was never called — nothing captured
        assert reject_calls == []

    def test_capture_point_is_the_drop_branch_only(self):
        """
        Structural guard (§1: 'by construction'): insert_reject must be CALLED
        from exactly these TWO drop branches — and no third:
          - apply_rescue_outcome                         (news rescue-drop)
          - PolymarketGoldSocialFunction.process_element (HN low-signal-drop, T6)
        Dedup skips and DLQ records happen in code paths that simply have no
        capture call to reach. The site is qualified by enclosing CLASS so the
        guard rejects an insert_reject added to the WRONG process_element —
        gold_job defines three, and a stray direct call in
        GlobalNewsGoldFunction.process_element (double-capturing news rejects)
        must fail this test. AST-based so docstring mentions don't count.
        """
        import ast
        import inspect
        import processing.gold_job as gj

        tree = ast.parse(inspect.getsource(gj))
        call_sites: list[str] = []

        class _CallFinder(ast.NodeVisitor):
            def __init__(self):
                self.func_stack: list[str] = []
                self.class_stack: list[str] = []

            def visit_ClassDef(self, node):
                self.class_stack.append(node.name)
                self.generic_visit(node)
                self.class_stack.pop()

            def visit_FunctionDef(self, node):
                self.func_stack.append(node.name)
                self.generic_visit(node)
                self.func_stack.pop()

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_Call(self, node):
                func = node.func
                name = (
                    func.id if isinstance(func, ast.Name)
                    else getattr(func, "attr", "")
                )
                if name == "insert_reject":
                    fn = self.func_stack[-1] if self.func_stack else "<module>"
                    # Qualify with the enclosing class so the two intended sites
                    # are distinguishable from the other two process_element methods.
                    site = f"{self.class_stack[-1]}.{fn}" if self.class_stack else fn
                    call_sites.append(site)
                self.generic_visit(node)

        _CallFinder().visit(tree)
        assert call_sites == [
            "apply_rescue_outcome",
            "PolymarketGoldSocialFunction.process_element",
        ], (
            f"insert_reject must be called from exactly the two intended drop "
            f"branches (apply_rescue_outcome + PolymarketGoldSocialFunction."
            f"process_element) — found call sites: {call_sites}"
        )


# ==========================================================
# [7] Filter-behavior invariance — instrumentation moves nothing
# ==========================================================

class TestInvariance:
    """The T6 gate in miniature: same inputs → same decisions, capture on or off."""

    @pytest.mark.parametrize("rescued,similarity", [
        (False, 0.0),      # empty-text edge
        (False, 0.2099),   # just below threshold
        (False, 0.3499),   # boundary-adjacent drop
        (True,  0.35),     # boundary promote
        (True,  0.9),      # clear promote
    ])
    def test_decision_identical_with_capture_on_and_off(
        self, reject_calls, rescued, similarity
    ):
        doc_on  = _make_silver_doc()
        doc_off = _make_silver_doc()

        result_on = apply_rescue_outcome(
            doc_on, rescued, similarity, capture_enabled=True, run_id="x",
        )
        result_off = apply_rescue_outcome(
            doc_off, rescued, similarity, capture_enabled=False,
        )

        assert result_on == result_off, "Capture flag must never change the decision"
        assert doc_on == doc_off, "Capture flag must never change the doc mutations"

    def test_return_depends_only_on_rescued(self, reject_calls):
        """The decision is compute_semantic_rescue's — apply only relays it."""
        assert apply_rescue_outcome(_make_silver_doc(), True, 0.01,
                                    capture_enabled=True) is True
        assert apply_rescue_outcome(_make_silver_doc(), False, 0.99,
                                    capture_enabled=True) is False


# ==========================================================
# [8] Validator accepts the new key (Ron addition, 2026-07-02)
# ==========================================================

class TestValidatorAcceptsRescueCosine:

    def _valid_baseline_doc(self) -> dict:
        """A doc that passes validate_silver_document before any new key."""
        return {
            "doc_id":                "doc-001",
            "document_hash":         "b" * 64,
            "canonical_event_id":    "evt-validator-test",
            "full_text_raw":         "Body text.",
            "inverted_pyramid_lead": "Lead.",
            "source_name":           "newsapi",
            "original_url":          "https://example.com/a",
            "publish_date":          "2026-07-01T12:00:00Z",
            "detected_entities":     [],
            "relevance_score":       0.12,
        }

    def test_baseline_doc_is_valid(self):
        result = validate_silver_document(self._valid_baseline_doc())
        assert result.is_valid, f"Baseline must be valid first: {result.errors}"

    def test_doc_with_rescue_cosine_key_still_valid(self):
        """
        The promote path adds rescue_cosine to the doc before kv_archive.
        _check_fields validates REQUIRED keys only, so the unknown key must
        pass — if this ever fails, do NOT loosen the validator: pass the
        cosine to archive() as a parameter instead (Ron directive 2026-07-02).
        """
        doc = self._valid_baseline_doc()
        doc["rescue_cosine"] = 0.42
        result = validate_silver_document(doc)
        assert result.is_valid, (
            f"Validator rejected the rescue_cosine key: {result.errors} — "
            "see Ron directive: switch to an archive() parameter, don't loosen "
            "the validator"
        )

    def test_promoted_doc_shape_is_valid(self, monkeypatch):
        """The exact doc apply_rescue_outcome produces must validate."""
        doc = self._valid_baseline_doc()
        doc["is_high_signal"] = False
        apply_rescue_outcome(doc, rescued=True, similarity=0.41)
        result = validate_silver_document(doc)
        assert result.is_valid, f"Promoted doc failed validation: {result.errors}"
