"""
Gate 3 — Filter Observability & Cost Persistence Round-Trips (Phase 7B.5-I, T6).

Insert → read back → field equality against the LIVE local Postgres
(migration 003 applied), covering the §4 Gate 3 bar:

  [1]  filter_rejects round-trip — every field survives, JSONB intact,
       FULL untruncated text, run_id tagging.
  [2]  llm_cost_events round-trip — direct insert_event + the real
       record_usage() write path (marked llm_cost_db so the conftest
       autouse stub steps aside).
  [3]  knowledge_vault.rescue_cosine — rescued row carries the exact
       cosine; sniper-passed row reads back NULL.
  [4]  View correctness — seeded events → llm_cost_run_summary returns the
       correct per-(source, usage_type) rows, per-source ALL rollups, and
       the grand-total row; llm_cost_daily_summary carries today's
       contribution.

Isolation: every row is tagged with run_id = f"test_{test_run_id}..." or a
test-prefixed canonical_event_id; the cleanup_* fixtures delete them after
each test (conftest convention).

References:
    - docs/A_pipeline/plans/phase7b5i_filter_observability_and_cost.md §2, §4
    - infrastructure/sql/migrations/003_filter_observability_and_cost.sql
"""

from __future__ import annotations

import uuid

import pytest

from persistence.filter_rejects import fetch_rejects, insert_reject
from persistence.knowledge_vault import archive, fetch_by_doc_id
from utils.db import get_cursor


pytestmark = pytest.mark.usefixtures("db_available")


def _reject_doc(**overrides) -> dict:
    doc = {
        "source_name":           "newsapi",
        "original_url":          "https://example.com/reject-me",
        "title":                 "Celebrity gossip roundup",
        "inverted_pyramid_lead": "A quiet week in entertainment.",
        "full_text_raw":         "Nothing geopolitically relevant happened. " * 500,
        "relevance_score":       0.05,
        "sniper_keywords":       ["celebrity", "gossip"],
    }
    doc.update(overrides)
    return doc


def _vault_doc(canonical_event_id: str, **overrides) -> dict:
    doc = {
        "document_hash":         uuid.uuid4().hex + uuid.uuid4().hex,   # unique 64-hex
        "canonical_event_id":    canonical_event_id,
        "bronze_ref":            str(uuid.uuid4()),
        "source_name":           "newsapi",
        "author":                "Reuters",
        "original_url":          "https://example.com/article",
        "publish_date":          "2026-07-01T12:00:00Z",
        "full_text_raw":         "Full body text.",
        "inverted_pyramid_lead": "Lead sentence.",
        "detected_entities":     [],
        "relevance_score":       0.05,
        "sniper_keywords":       [],
    }
    doc.update(overrides)
    return doc


# ==========================================================
# [1] filter_rejects round-trip
# ==========================================================

@pytest.mark.usefixtures("cleanup_filter_rejects")
class TestFilterRejectsRoundTrip:

    def test_insert_and_read_back_field_equality(self, test_run_id):
        run_id = f"test_{test_run_id}_rt"
        doc = _reject_doc()

        reject_id = insert_reject(doc, 0.2137, run_id=run_id)
        assert reject_id is not None, "insert_reject must return the new reject_id"

        rows = fetch_rejects(run_id=run_id)
        assert len(rows) == 1
        row = rows[0]
        assert row["reject_id"] == reject_id
        assert row["run_id"] == run_id
        assert row["source_name"] == doc["source_name"]
        assert row["original_url"] == doc["original_url"]
        assert row["title"] == doc["title"]
        assert row["inverted_pyramid_lead"] == doc["inverted_pyramid_lead"]
        assert row["relevance_score"] == pytest.approx(0.05)
        assert row["rescue_cosine"] == pytest.approx(0.2137, abs=1e-6)
        assert row["rejected_at"] is not None

    def test_full_text_untruncated_in_db(self, test_run_id):
        """§2.1: FULL text — T7B.1 manual review reads the whole article."""
        run_id = f"test_{test_run_id}_full"
        long_text = "geopolitics " * 5000   # ~60k chars
        insert_reject(_reject_doc(full_text_raw=long_text), 0.1, run_id=run_id)

        row = fetch_rejects(run_id=run_id)[0]
        assert row["full_text_raw"] == long_text

    def test_sniper_keywords_jsonb_intact(self, test_run_id):
        run_id = f"test_{test_run_id}_jsonb"
        keywords = ["oil", "opec", "sanctions"]
        insert_reject(_reject_doc(sniper_keywords=keywords), 0.3, run_id=run_id)

        row = fetch_rejects(run_id=run_id)[0]
        assert row["sniper_keywords"] == keywords

    def test_zero_cosine_edge_persists(self, test_run_id):
        """The empty-text edge writes rescue_cosine = 0.0 (NOT NULL column)."""
        run_id = f"test_{test_run_id}_zero"
        insert_reject(
            _reject_doc(title="", inverted_pyramid_lead="", full_text_raw=""),
            0.0, run_id=run_id,
        )
        row = fetch_rejects(run_id=run_id)[0]
        assert row["rescue_cosine"] == 0.0

    def test_empty_run_id_stored_as_null(self, test_run_id):
        """
        run_id='' → NULL (D4 semantics). Fetched by reject_id since NULL
        rows can't be selected by run tag; cleaned up by reject_id too.
        """
        reject_id = insert_reject(_reject_doc(), 0.2, run_id="")
        try:
            with get_cursor() as cur:
                cur.execute(
                    "SELECT run_id FROM filter_rejects WHERE reject_id = %s::uuid;",
                    (reject_id,),
                )
                assert cur.fetchone()["run_id"] is None
        finally:
            with get_cursor() as cur:
                cur.execute(
                    "DELETE FROM filter_rejects WHERE reject_id = %s::uuid;",
                    (reject_id,),
                )


# ==========================================================
# [2] llm_cost_events round-trip (real inserts — llm_cost_db)
# ==========================================================

@pytest.mark.llm_cost_db
@pytest.mark.usefixtures("cleanup_llm_cost_events")
class TestLlmCostEventsRoundTrip:

    def test_insert_event_and_read_back(self, test_run_id):
        from persistence.llm_cost_events import fetch_events, insert_event

        run_id = f"test_{test_run_id}_ev"
        event_id = insert_event(
            site="gold_enrich",
            model="gpt-4o",
            prompt_tokens=200,
            completion_tokens=100,
            total_tokens=300,
            cost_usd=0.0015,
            source_name="newsapi",
            trace_id="evt-gate3-1",
            run_id=run_id,
        )
        assert event_id

        rows = fetch_events(run_id=run_id)
        assert len(rows) == 1
        row = rows[0]
        assert row["event_id"] == event_id
        assert row["site"] == "gold_enrich"
        assert row["model"] == "gpt-4o"
        assert row["prompt_tokens"] == 200
        assert row["completion_tokens"] == 100
        assert row["total_tokens"] == 300
        assert float(row["cost_usd"]) == pytest.approx(0.0015)
        assert row["source_name"] == "newsapi"
        assert row["trace_id"] == "evt-gate3-1"
        assert row["created_at"] is not None

    def test_record_usage_writes_a_real_row(self, test_run_id):
        """The full write path: response → extract → price → INSERT."""
        from types import SimpleNamespace

        from persistence.llm_cost_events import fetch_events
        from utils.llm_cost import record_usage

        run_id = f"test_{test_run_id}_ru"
        response = SimpleNamespace(
            usage=SimpleNamespace(
                prompt_tokens=1000, completion_tokens=1000, total_tokens=2000,
            )
        )
        total, cost = record_usage(
            "gpt-3.5-turbo", response,
            site="translate", source_name="telegram",
            trace_id="evt-gate3-2", run_id=run_id,
        )
        assert total == 2000
        assert cost == pytest.approx(0.0020)

        rows = fetch_events(run_id=run_id)
        assert len(rows) == 1
        assert rows[0]["site"] == "translate"
        assert float(rows[0]["cost_usd"]) == pytest.approx(0.0020)


# ==========================================================
# [3] knowledge_vault.rescue_cosine round-trip
# ==========================================================

@pytest.mark.usefixtures("cleanup_knowledge_vault")
class TestVaultRescueCosineRoundTrip:

    def test_rescued_doc_carries_exact_cosine(self, test_run_id):
        doc = _vault_doc(f"test_{test_run_id}_rescued")
        doc["rescue_cosine"] = 0.4123          # what apply_rescue_outcome threads in

        doc_id = archive(doc)
        assert doc_id is not None

        row = fetch_by_doc_id(doc_id)
        assert row["rescue_cosine"] == pytest.approx(0.4123, abs=1e-6)

    def test_sniper_passed_doc_reads_back_null(self, test_run_id):
        doc = _vault_doc(f"test_{test_run_id}_direct", relevance_score=0.6)
        assert "rescue_cosine" not in doc      # sniper-passed: key never added

        doc_id = archive(doc)
        row = fetch_by_doc_id(doc_id)
        assert row["rescue_cosine"] is None


# ==========================================================
# [4] View correctness — seeded events → exact rollups
# ==========================================================

@pytest.mark.llm_cost_db
@pytest.mark.usefixtures("cleanup_llm_cost_events")
class TestCostSummaryViews:

    # (site, source, prompt, completion, cost) — 3 detail groups, 2 sources
    _SEED = [
        ("gold_enrich",  "newsapi",  200, 100, 0.001500),
        ("gold_enrich",  "newsapi",  300, 150, 0.002250),
        ("rescue_embed", "newsapi",  120,   0, 0.000002),
        ("translate",    "telegram",  80,  20, 0.000070),
        ("translate",    "telegram",  40,  10, 0.000035),
    ]

    def _seed(self, run_id: str) -> None:
        from persistence.llm_cost_events import insert_event
        for site, source, prompt, completion, cost in self._SEED:
            insert_event(
                site=site,
                model="gpt-4o" if site == "gold_enrich" else "gpt-3.5-turbo",
                prompt_tokens=prompt,
                completion_tokens=completion,
                total_tokens=prompt + completion,
                cost_usd=cost,
                source_name=source,
                trace_id="evt-view-seed",
                run_id=run_id,
            )

    def _run_summary(self, run_id: str) -> dict:
        with get_cursor() as cur:
            cur.execute(
                "SELECT * FROM llm_cost_run_summary WHERE run_id = %s;",
                (run_id,),
            )
            return {
                (r["source_name"], r["usage_type"]): r for r in cur.fetchall()
            }

    def test_run_summary_rollup_levels(self, test_run_id):
        run_id = f"test_{test_run_id}_view"
        self._seed(run_id)
        rows = self._run_summary(run_id)

        # 3 detail rows + 2 per-source ALL rows + 1 grand total = 6
        assert len(rows) == 6, f"Expected 6 rollup rows, got {sorted(rows)}"

        # Detail level — per usage-type within each source
        detail = rows[("newsapi", "gold_enrich")]
        assert detail["calls"] == 2
        assert detail["prompt_tokens"] == 500
        assert detail["completion_tokens"] == 250
        assert detail["total_tokens"] == 750
        assert float(detail["cost_usd"]) == pytest.approx(0.0038)   # round(,4)

        assert rows[("newsapi", "rescue_embed")]["calls"] == 1
        assert rows[("telegram", "translate")]["calls"] == 2

        # Intermediate level — per-source totals
        newsapi_all = rows[("newsapi", "ALL")]
        assert newsapi_all["calls"] == 3
        assert newsapi_all["total_tokens"] == 750 + 120
        telegram_all = rows[("telegram", "ALL")]
        assert telegram_all["calls"] == 2
        assert telegram_all["total_tokens"] == 150

        # Grand total
        grand = rows[("ALL", "ALL")]
        assert grand["calls"] == 5
        assert grand["prompt_tokens"] == sum(s[2] for s in self._SEED)
        assert grand["completion_tokens"] == sum(s[3] for s in self._SEED)
        expected_total_cost = round(sum(s[4] for s in self._SEED), 4)
        assert float(grand["cost_usd"]) == pytest.approx(expected_total_cost)

    def test_macro_equals_sum_of_micro(self, test_run_id):
        """Views are derived — the grand total MUST equal the row sum (§2.3)."""
        run_id = f"test_{test_run_id}_derx"
        self._seed(run_id)

        with get_cursor() as cur:
            cur.execute(
                "SELECT sum(cost_usd) AS micro FROM llm_cost_events WHERE run_id = %s;",
                (run_id,),
            )
            micro = float(cur.fetchone()["micro"])

        grand = self._run_summary(run_id)[("ALL", "ALL")]
        assert float(grand["cost_usd"]) == pytest.approx(round(micro, 4))

    def test_daily_summary_carries_todays_contribution(self, test_run_id):
        run_id = f"test_{test_run_id}_daily"
        self._seed(run_id)
        seeded_cost = sum(s[4] for s in self._SEED)

        with get_cursor() as cur:
            cur.execute(
                """
                SELECT cost_usd, calls FROM llm_cost_daily_summary
                WHERE day = CURRENT_DATE
                  AND source_name = 'ALL' AND usage_type = 'ALL';
                """
            )
            row = cur.fetchone()

        assert row is not None, "Grand-total row for today must exist"
        # Other rows (dev traffic) may add to today's totals — assert at-least
        assert row["calls"] >= 5
        assert float(row["cost_usd"]) >= round(seeded_cost, 4) - 1e-9
