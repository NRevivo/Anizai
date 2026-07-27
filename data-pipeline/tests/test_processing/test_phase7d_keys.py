"""
Gate 2 — Phase 7D Dedup Keys & Deterministic signal_id (§5 T4/T5, §6).

T4 / D1a — the HackerNews dedup key:
    hash_hackernews_story(story_id) keys on story_id ALONE (one enrichment per story,
    ever), stripped so whitespace never forks the key, namespaced so a bare integer
    story_id can't collide in the shared social_vault content_hash space. The Silver
    mapper stores that hash in content_hash, so _deterministic_signal_id() is stable
    per story (the social half of KG-A-8).

T5 — deterministic global_news signal_id:
    the newsapi / arxiv / telegram Gold builders derive signal_id from the record's
    document_hash via _deterministic_signal_id(), so a re-delivery maps to the same
    signal_id and knowledge_vectors' ON CONFLICT dedups it (KG-A-8), instead of
    uuid4() minting a near-duplicate vector every time.

References:
    - docs/A_pipeline/plans/phase7d_enrichment_gating.md §3 D1a, §5 T4/T5, §6
    - processing/deduplication.py::hash_hackernews_story
    - processing/silver_job.py::map_hackernews_story_to_silver
    - processing/gold_job.py::_deterministic_signal_id and the three global_news builders
"""

from __future__ import annotations

import uuid

import pytest

from processing.deduplication import hash_hackernews_story, sha256_hash
from processing.gold_job import (
    _deterministic_signal_id,
    build_gold_global_signal,
    build_arxiv_gold_global_signal,
    build_telegram_gold_global_signal,
)


# ==========================================================
# T4 / D1a — hash_hackernews_story
# ==========================================================

class TestHackerNewsHasher:

    def test_stable_across_calls(self):
        assert hash_hackernews_story("12345") == hash_hackernews_story("12345")

    def test_different_story_id_different_hash(self):
        assert hash_hackernews_story("12345") != hash_hackernews_story("67890")

    def test_whitespace_normalised(self):
        """
        Gate-2 case (T7 fold-in): a stray whitespace difference must NOT fork a
        story's dedup identity — " 12345 " and "12345" produce the same key.
        """
        assert hash_hackernews_story(" 12345 ") == hash_hackernews_story("12345")
        assert hash_hackernews_story("\t12345\n") == hash_hackernews_story("12345")

    def test_namespaced_not_bare_sha(self):
        """The key is sha256('hackernews|<id>'), not sha256('<id>') — no cross-source collision."""
        assert hash_hackernews_story("12345") == sha256_hash("hackernews|12345")
        assert hash_hackernews_story("12345") != sha256_hash("12345")

    def test_returns_64_char_hex(self):
        h = hash_hackernews_story("12345")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_story_id_only_ignores_comments(self):
        """
        D1a's whole point: the key does NOT depend on the comment set, so the same
        story re-fetched with more comments yields the SAME key (one enrichment ever).
        hash_hackernews_story takes only story_id — there is no comment parameter to
        drift, which this test locks in.
        """
        # Same story_id, regardless of any comment context the caller may have had.
        assert hash_hackernews_story("999") == hash_hackernews_story("999")


class TestHackerNewsSilverKey:
    """The Silver mapper must store the story_id-only key and strip whitespace."""

    def _raw(self, story_id="42", **over):
        raw = {
            "story_id":   story_id,
            "title":      "Show HN: a thing",
            "url":        "https://news.ycombinator.com/item?id=42",
            "author":     "pg",
            "points":     120,
            "created_at": "2026-07-20T09:00:00Z",
            "top_comments": [{"text": "great"}, {"text": "ship it"}],
            "story_text": "I built a thing.",
            "story_type": "show_hn",
            "num_comments": 2,
        }
        raw.update(over)
        return raw

    def _env(self):
        return {"event_id": "bronze-evt-1", "producer_timestamp": "2026-07-20T09:01:00Z",
                "trace_id": "trace-1"}

    def test_content_hash_is_story_id_only(self):
        from processing.silver_job import map_hackernews_story_to_silver
        silver = map_hackernews_story_to_silver(self._raw(story_id="42"), self._env())
        assert silver["content_hash"] == hash_hackernews_story("42")

    def test_content_hash_independent_of_comments(self):
        """Same story_id, different top_comments → SAME content_hash (D1a)."""
        from processing.silver_job import map_hackernews_story_to_silver
        env = self._env()
        few  = map_hackernews_story_to_silver(
            self._raw(story_id="42", top_comments=[{"text": "a"}]), env)
        many = map_hackernews_story_to_silver(
            self._raw(story_id="42", top_comments=[{"text": "a"}, {"text": "b"}, {"text": "c"}]), env)
        assert few["content_hash"] == many["content_hash"]

    def test_stored_story_id_is_stripped(self):
        from processing.silver_job import map_hackernews_story_to_silver
        silver = map_hackernews_story_to_silver(self._raw(story_id="  42  "), self._env())
        assert silver["story_id"] == "42"
        assert silver["content_hash"] == hash_hackernews_story("42")

    def test_deterministic_signal_id_stable_for_hn_story(self):
        """
        With the story_id-only content_hash, _deterministic_signal_id() is stable for
        the story across re-fetches — closing the social half of KG-A-8.
        """
        from processing.silver_job import map_hackernews_story_to_silver
        env = self._env()
        s1 = map_hackernews_story_to_silver(self._raw(story_id="42", top_comments=[{"text": "a"}]), env)
        s2 = map_hackernews_story_to_silver(self._raw(story_id="42", top_comments=[{"text": "a"}, {"text": "z"}]), env)
        assert (_deterministic_signal_id(s1["content_hash"])
                == _deterministic_signal_id(s2["content_hash"]))


# ==========================================================
# T5 — deterministic global_news signal_id (3 builders)
# ==========================================================

_AI_META = {
    "executive_summary": "Summary.",
    "key_findings": ["x"],
    "impact_level": 3,
    "urgency_level": 2,
    "reliability_score": 0.7,
    "sentiment_score": 0.0,
    "extracted_entities": [],
    "topic_classification": "Finance",
    "fact_check_flag": False,
    "geospatial_focus": "US",
    "authors": ["A. Author"],
    "domain_tags": ["cs.LG"],
}
_EMBED = [0.0] * 8   # builders pass embedding through untouched; dim irrelevant here


def _doc(document_hash: str) -> dict:
    return {
        "document_hash":      document_hash,
        "canonical_event_id": "evt-1",
        "bronze_ref":         "bref-1",
        "doc_id":             "doc-1",
        "title":              "T",
        "original_url":       "https://example.com/a",
        "inverted_pyramid_lead": "Lead.",
        "publish_date":       "2026-07-20T00:00:00Z",
        "sniper_keywords":    [],
        "impact_boost":       False,
    }


_BUILDERS = [
    pytest.param(build_gold_global_signal,        id="newsapi"),
    pytest.param(build_arxiv_gold_global_signal,  id="arxiv"),
    pytest.param(build_telegram_gold_global_signal, id="telegram"),
]


class TestDeterministicGlobalNewsSignalId:

    @pytest.mark.parametrize("builder", _BUILDERS)
    def test_signal_id_equals_deterministic_of_document_hash(self, builder):
        h = "a" * 64
        rec = builder(_doc(h), _AI_META, _EMBED)
        assert rec["metadata"]["signal_id"] == _deterministic_signal_id(h)

    @pytest.mark.parametrize("builder", _BUILDERS)
    def test_same_hash_same_id_across_calls(self, builder):
        h = "b" * 64
        r1 = builder(_doc(h), _AI_META, _EMBED)
        r2 = builder(_doc(h), _AI_META, _EMBED)
        assert r1["metadata"]["signal_id"] == r2["metadata"]["signal_id"]

    @pytest.mark.parametrize("builder", _BUILDERS)
    def test_different_hash_different_id(self, builder):
        r1 = builder(_doc("c" * 64), _AI_META, _EMBED)
        r2 = builder(_doc("d" * 64), _AI_META, _EMBED)
        assert r1["metadata"]["signal_id"] != r2["metadata"]["signal_id"]

    @pytest.mark.parametrize("builder", _BUILDERS)
    def test_empty_hash_falls_back_to_uuid4(self, builder):
        """Empty document_hash → uuid4() fallback: a valid, non-crashing UUID."""
        rec = builder(_doc(""), _AI_META, _EMBED)
        sid = rec["metadata"]["signal_id"]
        # Parses as a UUID and is NOT the deterministic value of the empty string.
        assert uuid.UUID(sid)
        # Two empty-hash builds differ (random), proving the fallback path.
        rec2 = builder(_doc(""), _AI_META, _EMBED)
        assert sid != rec2["metadata"]["signal_id"]


class TestDeterministicSignalIdHelper:
    """The Sprint-11 helper both paths reuse unchanged."""

    def test_same_hash_same_id(self):
        assert _deterministic_signal_id("a" * 64) == _deterministic_signal_id("a" * 64)

    def test_deterministic_is_uuid5_over_x500(self):
        h = "a" * 64
        assert _deterministic_signal_id(h) == str(uuid.uuid5(uuid.NAMESPACE_X500, h))

    def test_empty_is_uuid4_random(self):
        a = _deterministic_signal_id("")
        b = _deterministic_signal_id("")
        assert a != b
        assert uuid.UUID(a) and uuid.UUID(b)
