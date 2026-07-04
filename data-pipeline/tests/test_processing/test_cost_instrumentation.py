"""
Gate 2 — AI Call-Site Cost Instrumentation Tests (Phase 7B.5-I, T4/T5).

Verifies that every runtime OpenAI call site emits exactly one cost event
with the correct §2.5 tag, and — Ron's directive (2026-07-02) — that at the
chat sites record_usage runs immediately after the API response returns and
BEFORE JSON parsing, so calls that are paid for but fail parsing (→ DLQ)
are still counted in the day's cost.

Site registry under test (plan §2.5):
    gold_enrich     call_openai_cognitive_metadata   (newsapi/arxiv/telegram)
    gold_consensus  call_openai_consensus            (polymarket)
    gold_consensus  call_hackernews_consensus        (hackernews)
    translate       _call_translation_api            (telegram/googletrends)
    gold_embed      call_openai_embedding            (shared, 5 sources)
    rescue_embed    compute_semantic_rescue          (both branches — P4)

All DB writes are intercepted by the autouse `llm_cost_insert_calls`
conftest fixture — hermetic, no Postgres needed.

References:
    - docs/A_pipeline/plans/phase7b5i_filter_observability_and_cost.md §2.5, §4
    - processing/gold_job.py, processing/translation.py (instrumented sites)
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import numpy as np
import pytest

from config.settings import OPENAI_MODEL_NAME
from processing.gold_job import (
    call_hackernews_consensus,
    call_openai_consensus,
    call_openai_cognitive_metadata,
    call_openai_embedding,
    compute_semantic_rescue,
)
from processing.translation import (
    _call_translation_api,
    translate_keyword_to_english,
    translate_to_english,
)


# ==========================================================
# Mock builders
# ==========================================================

_PROMPT_TOKENS, _COMPLETION_TOKENS = 100, 50


def _usage_mock():
    usage = MagicMock()
    usage.prompt_tokens = _PROMPT_TOKENS
    usage.completion_tokens = _COMPLETION_TOKENS
    usage.total_tokens = _PROMPT_TOKENS + _COMPLETION_TOKENS
    return usage


def _chat_client(content: str) -> MagicMock:
    """Mock OpenAI client whose chat call returns `content` + real token counts."""
    client = MagicMock()
    response = MagicMock()
    response.usage = _usage_mock()
    response.choices[0].message.content = content
    client.chat.completions.create.return_value = response
    return client


def _embed_client(vector: list[float]) -> MagicMock:
    client = MagicMock()
    response = MagicMock()
    usage = MagicMock()
    usage.prompt_tokens = 64
    usage.completion_tokens = 0
    usage.total_tokens = 64
    response.usage = usage
    response.data[0].embedding = vector
    client.embeddings.create.return_value = response
    return client


_VALID_META = json.dumps({"executive_summary": "ok", "impact_level": 3})


def _news_doc() -> dict:
    return {
        "source_name":           "newsapi",
        "canonical_event_id":    "evt-cost-news",
        "bronze_ref":            "bref-news",
        "title":                 "Rates decision",
        "inverted_pyramid_lead": "Lead.",
        "full_text_raw":         "Body.",
    }


def _social_doc(source: str) -> dict:
    return {
        "source_name": source,
        "bronze_ref":  f"bref-{source}",
        "question":    "Will X happen?",
        "title":       "Story title",
        "top_comments": [],
        "raw_comments": [],
    }


# ==========================================================
# Chat sites — cost recorded BEFORE parsing (Ron directive)
# ==========================================================

class TestChatSitesCostBeforeParse:

    def test_gold_enrich_records_on_parse_failure(self, llm_cost_insert_calls):
        """Paid call + invalid JSON → ValueError raised, cost STILL counted."""
        client = _chat_client("NOT VALID JSON {{{")
        with pytest.raises(ValueError):
            call_openai_cognitive_metadata(_news_doc(), client)

        assert len(llm_cost_insert_calls) == 1
        row = llm_cost_insert_calls[0]
        assert row["site"] == "gold_enrich"
        assert row["model"] == OPENAI_MODEL_NAME
        assert row["source_name"] == "newsapi"
        assert row["trace_id"] == "evt-cost-news"
        assert row["prompt_tokens"] == _PROMPT_TOKENS
        assert row["completion_tokens"] == _COMPLETION_TOKENS

    def test_polymarket_consensus_records_on_parse_failure(self, llm_cost_insert_calls):
        client = _chat_client("```garbage```")
        with pytest.raises(ValueError):
            call_openai_consensus(_social_doc("polymarket"), client)

        assert len(llm_cost_insert_calls) == 1
        row = llm_cost_insert_calls[0]
        assert row["site"] == "gold_consensus"
        assert row["source_name"] == "polymarket"
        assert row["trace_id"] == "bref-polymarket"

    def test_hackernews_consensus_records_on_parse_failure(self, llm_cost_insert_calls):
        client = _chat_client("not json either")
        with pytest.raises(ValueError):
            call_hackernews_consensus(_social_doc("hackernews"), client)

        assert len(llm_cost_insert_calls) == 1
        row = llm_cost_insert_calls[0]
        assert row["site"] == "gold_consensus"
        assert row["source_name"] == "hackernews"

    def test_gold_enrich_happy_path_records_exactly_once(self, llm_cost_insert_calls):
        client = _chat_client(_VALID_META)
        meta = call_openai_cognitive_metadata(_news_doc(), client)
        assert meta["impact_level"] == 3
        assert len(llm_cost_insert_calls) == 1

    def test_source_name_travels_from_the_doc(self, llm_cost_insert_calls):
        """gold_enrich is shared by 3 sources — the doc decides the tag."""
        for source in ("newsapi", "arxiv", "telegram"):
            doc = _news_doc()
            doc["source_name"] = source
            call_openai_cognitive_metadata(doc, _chat_client(_VALID_META))
        assert [c["source_name"] for c in llm_cost_insert_calls] == [
            "newsapi", "arxiv", "telegram",
        ]


# ==========================================================
# gold_embed — the shared helper records once per call
# ==========================================================

class TestGoldEmbedSite:

    def test_embedding_records_with_threaded_context(self, llm_cost_insert_calls):
        client = _embed_client([0.1] * 1536)
        vec = call_openai_embedding(
            "summary text", client,
            source_name="arxiv", trace_id="evt-embed-1",
        )
        assert len(vec) == 1536
        assert len(llm_cost_insert_calls) == 1
        row = llm_cost_insert_calls[0]
        assert row["site"] == "gold_embed"
        assert row["model"] == "text-embedding-3-small"
        assert row["source_name"] == "arxiv"
        assert row["trace_id"] == "evt-embed-1"
        assert row["completion_tokens"] == 0

    def test_backward_compatible_without_context(self, llm_cost_insert_calls):
        """P2: keyword-only kwargs with None defaults — old call shape works."""
        call_openai_embedding("text", _embed_client([0.0] * 1536))
        assert llm_cost_insert_calls[0]["source_name"] is None
        assert llm_cost_insert_calls[0]["trace_id"] is None

    def test_api_failure_records_nothing(self, llm_cost_insert_calls):
        """A call that raised was never billed — no cost row."""
        client = MagicMock()
        client.embeddings.create.side_effect = RuntimeError("rate limit")
        with pytest.raises(RuntimeError):
            call_openai_embedding("text", client, source_name="newsapi")
        assert llm_cost_insert_calls == []


# ==========================================================
# rescue_embed — recorded on BOTH branches (approved P4)
# ==========================================================

class TestRescueEmbedSite:

    def _ref(self) -> np.ndarray:
        ref = np.ones(1536, dtype=np.float32)
        return ref / np.linalg.norm(ref)

    def test_promote_branch_records_event(self, llm_cost_insert_calls):
        ref = self._ref()
        client = _embed_client(ref.tolist())    # identical vector → similarity 1.0
        rescued, _ = compute_semantic_rescue(_news_doc(), client, ref, 0.35)
        assert rescued is True
        assert len(llm_cost_insert_calls) == 1
        assert llm_cost_insert_calls[0]["site"] == "rescue_embed"
        assert llm_cost_insert_calls[0]["trace_id"] == "evt-cost-news"

    def test_drop_branch_records_event_too(self, llm_cost_insert_calls):
        """The wasted-spend half of §0: dropped articles still cost money."""
        ref = self._ref()
        orth = np.zeros(1536, dtype=np.float32)
        orth[0] = 1.0                            # near-orthogonal to uniform ref
        client = _embed_client(orth.tolist())
        rescued, score = compute_semantic_rescue(_news_doc(), client, ref, 0.35)
        assert rescued is False
        assert score < 0.35
        assert len(llm_cost_insert_calls) == 1
        assert llm_cost_insert_calls[0]["site"] == "rescue_embed"
        assert llm_cost_insert_calls[0]["source_name"] == "newsapi"

    def test_empty_text_skips_api_and_records_nothing(self, llm_cost_insert_calls):
        """No API call → nothing paid → no event (the 0.0-cosine edge)."""
        doc = {**_news_doc(), "title": "", "inverted_pyramid_lead": "", "full_text_raw": ""}
        client = MagicMock()
        rescued, score = compute_semantic_rescue(doc, client, self._ref(), 0.35)
        assert (rescued, score) == (False, 0.0)
        client.embeddings.create.assert_not_called()
        assert llm_cost_insert_calls == []


# ==========================================================
# translate — model, tags, threading, pass-through interplay
# ==========================================================

class TestTranslateSite:

    def test_translation_records_gpt35_event(self, llm_cost_insert_calls):
        client = _chat_client("Translated text")
        out = _call_translation_api(
            "טקסט", "he", client, source_name="telegram", trace_id="evt-tg-1",
        )
        assert out == "Translated text"
        assert len(llm_cost_insert_calls) == 1
        row = llm_cost_insert_calls[0]
        assert row["site"] == "translate"
        assert row["model"] == "gpt-3.5-turbo"
        assert row["source_name"] == "telegram"
        assert row["trace_id"] == "evt-tg-1"

    def test_api_failure_records_nothing_and_passes_through(self, llm_cost_insert_calls):
        client = MagicMock()
        client.chat.completions.create.side_effect = RuntimeError("api down")
        out = _call_translation_api("טקסט", "he", client, source_name="telegram")
        assert out == "טקסט"                    # pass-through unchanged
        assert llm_cost_insert_calls == []       # nothing was billed

    def test_paid_but_unusable_response_still_counted(self, llm_cost_insert_calls):
        """
        Analog of the chat-site directive: the response arrived (billed) but
        .content is None → .strip() raises inside the try → pass-through.
        The cost event was recorded before the content was touched.
        """
        client = _chat_client(None)   # content=None breaks .strip()
        out = _call_translation_api("טקסט", "he", client, source_name="telegram")
        assert out == "טקסט"
        assert len(llm_cost_insert_calls) == 1

    def test_client_none_noop_records_nothing(self, llm_cost_insert_calls):
        assert _call_translation_api("x", "he", None) == "x"
        assert llm_cost_insert_calls == []

    def test_telegram_wrapper_threads_trace_id(self, llm_cost_insert_calls):
        client = _chat_client("Translated")
        translate_to_english("טקסט", "abualiexpress", client, trace_id="evt-tg-2")
        assert llm_cost_insert_calls[0]["source_name"] == "telegram"
        assert llm_cost_insert_calls[0]["trace_id"] == "evt-tg-2"

    def test_googletrends_wrapper_tags_source(self, llm_cost_insert_calls):
        client = _chat_client("inflation")
        translate_keyword_to_english("אינפלציה", "IL", client, trace_id="evt-gt-1")
        assert llm_cost_insert_calls[0]["source_name"] == "googletrends"
        assert llm_cost_insert_calls[0]["trace_id"] == "evt-gt-1"

    def test_english_paths_cost_nothing(self, llm_cost_insert_calls):
        """English channel / English geo → no API call → no event."""
        client = MagicMock()
        translate_to_english("hello", "clashreport", client)
        translate_keyword_to_english("inflation", "US", client)
        client.chat.completions.create.assert_not_called()
        assert llm_cost_insert_calls == []
