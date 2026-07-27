"""
Gate 2 — Phase 7D Social-Path Reject Units (§5 T6, decisions O1(b) / D5-O2, §6).

compute_social_rescue_cosine — assembles the HackerNews reject text (title +
story_text + top-comment texts, comments bounded) and scores it against the sniper
reference vector via the shared _embed_and_score core; returns 0.0 on empty text.

social_reject_doc — normalises the HN social record into the news-document shape that
insert_reject() reads (url -> original_url, body -> full_text_raw), so insert_reject's
field contract stays explicit (one writer, one shape).

NOTE (D4 / KG-A-14 companion): the sniper reference vector is news-built, so these
cosines are NOT directly comparable to news-path cosines — Phase 7B.5 sweeps a
separate social threshold. These tests assert the mechanics, not a threshold.

References:
    - docs/A_pipeline/plans/phase7d_enrichment_gating.md §3 D2/D4, §5 T6, §6
    - processing/gold_job.py::compute_social_rescue_cosine, social_reject_doc, _embed_and_score
"""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest

from processing.gold_job import compute_social_rescue_cosine, social_reject_doc


_DIM = 1536


def _ref() -> np.ndarray:
    v = np.ones(_DIM, dtype=np.float32)
    return v / np.linalg.norm(v)


def _embed_client(vector) -> MagicMock:
    client = MagicMock()
    resp = MagicMock()
    usage = MagicMock()
    usage.prompt_tokens, usage.completion_tokens, usage.total_tokens = 40, 0, 40
    resp.usage = usage
    resp.data[0].embedding = list(vector)
    client.embeddings.create.return_value = resp
    return client


def _hn(**over) -> dict:
    doc = {
        "source_name":     "hackernews",
        "story_id":        "42",
        "title":           "Show HN: rate limiter",
        "url":             "https://news.ycombinator.com/item?id=42",
        "story_text":      "",
        "top_comments":    [{"text": "clever"}, {"text": "needs backpressure"}],
        "relevance_score": 0.06,
        "sniper_keywords": ["rate"],
        "bronze_ref":      "bref-hn-42",
        "content_hash":    "h" * 64,
        "is_high_signal":  False,
    }
    doc.update(over)
    return doc


# ==========================================================
# compute_social_rescue_cosine
# ==========================================================

class TestSocialRescueCosine:

    def test_identical_vector_scores_one(self, llm_cost_insert_calls):
        ref = _ref()
        client = _embed_client(ref.tolist())     # article == ref → cosine 1.0
        score = compute_social_rescue_cosine(_hn(), client, ref)
        assert score == pytest.approx(1.0, abs=1e-5)

    def test_records_one_rescue_embed_cost_row(self, llm_cost_insert_calls):
        ref = _ref()
        compute_social_rescue_cosine(_hn(), _embed_client(ref.tolist()), ref)
        assert len(llm_cost_insert_calls) == 1
        row = llm_cost_insert_calls[0]
        assert row["site"] == "rescue_embed"
        assert row["source_name"] == "hackernews"
        # trace_id uses _cost_trace_id → HN has no canonical_event_id, so bronze_ref.
        assert row["trace_id"] == "bref-hn-42"

    def test_text_uses_title_story_text_and_comments(self, llm_cost_insert_calls):
        ref = _ref()
        client = _embed_client(ref.tolist())
        compute_social_rescue_cosine(
            _hn(story_text="ask hn body", top_comments=[{"text": "c1"}, {"text": "c2"}]),
            client, ref,
        )
        sent = client.embeddings.create.call_args.kwargs["input"][0]
        assert "Show HN: rate limiter" in sent   # title
        assert "ask hn body" in sent             # story_text
        assert "c1" in sent and "c2" in sent     # comment texts

    def test_text_falls_back_to_title_only(self, llm_cost_insert_calls):
        ref = _ref()
        client = _embed_client(ref.tolist())
        compute_social_rescue_cosine(_hn(story_text="", top_comments=[]), client, ref)
        sent = client.embeddings.create.call_args.kwargs["input"][0]
        assert sent.strip() == "Show HN: rate limiter"

    def test_empty_text_returns_zero_without_api_call(self, llm_cost_insert_calls):
        client = MagicMock()
        score = compute_social_rescue_cosine(
            _hn(title="", story_text="", top_comments=[]), client, _ref(),
        )
        assert score == 0.0
        client.embeddings.create.assert_not_called()
        assert llm_cost_insert_calls == []       # nothing paid → nothing recorded

    def test_api_error_propagates(self, llm_cost_insert_calls):
        """
        compute_social_rescue_cosine does not swallow API errors — the CALLER
        (process_element T6 branch) catches and captures cosine 0.0 (fail-open).
        The unit contract is propagation, mirroring compute_semantic_rescue.
        """
        client = MagicMock()
        client.embeddings.create.side_effect = RuntimeError("rate limit")
        with pytest.raises(RuntimeError):
            compute_social_rescue_cosine(_hn(), client, _ref())


# ==========================================================
# social_reject_doc — D5/O2 caller-side normalisation
# ==========================================================

class TestSocialRejectDoc:

    def test_url_maps_to_original_url(self):
        d = social_reject_doc(_hn(url="https://x/y"))
        assert d["original_url"] == "https://x/y"

    def test_source_name_is_hackernews(self):
        assert social_reject_doc(_hn())["source_name"] == "hackernews"

    def test_body_prefers_story_text(self):
        d = social_reject_doc(_hn(story_text="ask hn body", top_comments=[{"text": "c1"}]))
        assert d["full_text_raw"] == "ask hn body"

    def test_body_falls_back_to_joined_comments(self):
        d = social_reject_doc(_hn(story_text="", top_comments=[{"text": "c1"}, {"text": "c2"}]))
        assert d["full_text_raw"] == "c1 c2"

    def test_lead_is_empty(self):
        assert social_reject_doc(_hn())["inverted_pyramid_lead"] == ""

    def test_carries_title_score_and_keywords(self):
        d = social_reject_doc(_hn(title="T", relevance_score=0.06, sniper_keywords=["rate"]))
        assert d["title"] == "T"
        assert d["relevance_score"] == 0.06
        assert d["sniper_keywords"] == ["rate"]

    def test_shape_matches_insert_reject_reads(self):
        """
        social_reject_doc must expose exactly the keys insert_reject reads, so the
        one-writer contract holds without insert_reject learning a social shape.
        """
        d = social_reject_doc(_hn())
        for key in (
            "source_name", "original_url", "title",
            "inverted_pyramid_lead", "full_text_raw",
            "relevance_score", "sniper_keywords",
        ):
            assert key in d, f"insert_reject reads {key!r} — social_reject_doc must supply it"
