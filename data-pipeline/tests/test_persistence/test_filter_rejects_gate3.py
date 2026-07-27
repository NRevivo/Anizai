"""
Gate 3 — filter_rejects.canonical_event_id round-trip (Phase 7D, T7, §6).

Verifies the instance key added in migration 004:
    insert_reject(..., canonical_event_id=cei) → fetch_rejects → equality,
    and a row written WITHOUT the key reads back NULL (pre-7D rows are
    indistinguishable — no backfill is possible).

Requires a live Postgres with migration 004 applied (adds the column). Without a
reachable DB the whole class SKIPS via db_available (Gate 1/2 stay independent of
infra). cleanup_filter_rejects deletes this run's rows by their test_<id> run_id
prefix on teardown.

References:
    - docs/A_pipeline/archive_plans/phase7d_enrichment_gating.md §4.1, §5 T7, §6
    - persistence/filter_rejects.py::insert_reject, fetch_rejects
    - infrastructure/sql/migrations/004_enrichment_gating.sql
"""

from __future__ import annotations

import pytest

from persistence.filter_rejects import insert_reject, fetch_rejects


def _reject_doc() -> dict:
    return {
        "source_name":           "newsapi",
        "original_url":          "https://www.reuters.com/gate3",
        "title":                 "Gate 3 reject",
        "inverted_pyramid_lead": "Lead.",
        "full_text_raw":         "Full body text for T7B.1 review.",
        "relevance_score":       0.05,
        "sniper_keywords":       ["test"],
    }


@pytest.mark.usefixtures("db_available", "cleanup_filter_rejects")
class TestFilterRejectsInstanceKeyGate3:

    def test_canonical_event_id_round_trips(self, test_run_id):
        run_id = f"test_{test_run_id}_cei"
        rid = insert_reject(
            _reject_doc(), 0.21, run_id=run_id, canonical_event_id="evt-roundtrip",
        )
        assert rid is not None, "insert must succeed (fail-open returns None on error)"

        rows = fetch_rejects(run_id=run_id)
        assert len(rows) == 1
        assert rows[0]["canonical_event_id"] == "evt-roundtrip"
        assert rows[0]["rescue_cosine"] == pytest.approx(0.21)

    def test_missing_key_reads_back_null(self, test_run_id):
        """A reject written without the key stores SQL NULL (the pre-7D-row shape)."""
        run_id = f"test_{test_run_id}_null"
        rid = insert_reject(_reject_doc(), 0.10, run_id=run_id)
        assert rid is not None

        rows = fetch_rejects(run_id=run_id)
        assert len(rows) == 1
        assert rows[0]["canonical_event_id"] is None

    def test_empty_string_key_stored_as_null(self, test_run_id):
        """insert_reject normalises "" → NULL (same convention as run_id)."""
        run_id = f"test_{test_run_id}_empty"
        insert_reject(_reject_doc(), 0.10, run_id=run_id, canonical_event_id="")
        rows = fetch_rejects(run_id=run_id)
        assert rows[0]["canonical_event_id"] is None

    def test_hackernews_social_reject_round_trips(self, test_run_id):
        """
        A social_reject_doc-shaped HackerNews reject (T6/D5-O2) round-trips end to
        end: source_name='hackernews', url mapped to original_url, instance key set.
        Proves the caller-side normalisation and the one-writer contract together.
        """
        from processing.gold_job import social_reject_doc

        run_id = f"test_{test_run_id}_hn"
        hn = {
            "source_name":   "hackernews",
            "url":           "https://news.ycombinator.com/item?id=1",
            "title":         "Show HN: a thing",
            "story_text":    "body",
            "top_comments":  [],
            "relevance_score": 0.06,
            "sniper_keywords": ["x"],
        }
        rid = insert_reject(
            social_reject_doc(hn), 0.20, run_id=run_id, canonical_event_id="bref-hn-1",
        )
        assert rid is not None

        rows = fetch_rejects(run_id=run_id)
        assert len(rows) == 1
        assert rows[0]["source_name"] == "hackernews"
        assert rows[0]["original_url"] == "https://news.ycombinator.com/item?id=1"
        assert rows[0]["canonical_event_id"] == "bref-hn-1"
