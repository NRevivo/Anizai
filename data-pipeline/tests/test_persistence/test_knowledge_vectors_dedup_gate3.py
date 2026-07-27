"""
Gate 3 — knowledge_vectors deterministic-id dedup (Phase 7D T5, §6; Ron T10 addition 2).

Proves by EXECUTION (not by reading DDL) that a deliberate re-delivery of the same
Gold record produces exactly ONE knowledge_vectors row: T5's deterministic signal_id
+ the table's PRIMARY KEY (signal_id) + ON CONFLICT (signal_id) DO NOTHING. Reported
as row count vs COUNT(DISTINCT signal_id) after the re-delivery — the T5 effect, the
KG-A-8 win that "reading the DDL" could not confirm.

Requires live Postgres with pgvector. Skips via db_available without it. Cleanup is
by the test_<id> canonical_event_id prefix (cleanup_knowledge_vectors).

References:
    - docs/A_pipeline/archive_plans/phase7d_enrichment_gating.md §5 T5, §6, §9(3)
    - persistence/knowledge_vectors.py::insert (ON CONFLICT guard)
    - processing/gold_job.py::build_gold_global_signal, _deterministic_signal_id
"""

from __future__ import annotations

import pytest

from processing.gold_job import build_gold_global_signal, _deterministic_signal_id
from persistence.knowledge_vectors import insert as kv_insert
from utils.db import get_cursor


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
}


def _silver(cei: str) -> dict:
    return {
        "document_hash":         "a" * 64,
        "canonical_event_id":    cei,
        "bronze_ref":            None,
        "doc_id":                None,
        "title":                 "Rates decision",
        "original_url":          "https://example.com/a",
        "inverted_pyramid_lead": "Lead.",
        "publish_date":          "2026-07-20T00:00:00Z",
        "sniper_keywords":       [],
        "impact_boost":          False,
    }


@pytest.mark.usefixtures("db_available", "cleanup_knowledge_vectors")
class TestKnowledgeVectorsDeterministicDedup:

    def test_redelivery_produces_exactly_one_vector(self, test_run_id):
        cei = f"test_{test_run_id}_kvdedup"
        rec = build_gold_global_signal(_silver(cei), _AI_META, [0.01] * 1536)

        expected_sid = _deterministic_signal_id("a" * 64)
        assert rec["metadata"]["signal_id"] == expected_sid, (
            "the builder must derive signal_id deterministically from document_hash"
        )

        # First delivery, then a DELIBERATE re-delivery. Neither may raise; the
        # ON CONFLICT (signal_id) DO NOTHING guard must fire on the second.
        sid1 = kv_insert(rec)
        sid2 = kv_insert(rec)
        assert sid1 == sid2 == expected_sid

        with get_cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS n, COUNT(DISTINCT signal_id) AS d "
                "FROM knowledge_vectors WHERE canonical_event_id = %s;",
                (cei,),
            )
            row = cur.fetchone()

        # The T5 effect, stated as Ron asked: row count vs COUNT(DISTINCT signal_id)
        # after a deliberate re-delivery. Both are 1 → the guard actually fired.
        assert row["n"] == 1, f"re-delivery must leave exactly one row, got {row['n']}"
        assert row["d"] == 1, f"exactly one distinct signal_id, got {row['d']}"
