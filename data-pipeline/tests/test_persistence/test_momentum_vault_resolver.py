"""
Sprint 22 T22.1 — Gate 1 tests for find_polymarket_market_by_question().

Two test layers:

  [A] Pure-mock guards (TestResolverGuards):
      Verify behavior that doesn't require pg_trgm — empty/whitespace
      input, parameter binding, default threshold. `get_cursor` is
      patched at the lookup site (persistence.momentum_vault.get_cursor)
      per project test convention (mock at the lookup site, not where
      the function is defined).

  [B] Live-DB fixture markets (TestResolverFuzzyMatching):
      Insert fixture rows into momentum_vault with carefully chosen
      `question` text in metadata_extension, then verify pg_trgm
      similarity behavior end-to-end. Requires the dev Postgres to be
      reachable; the `db_available` session fixture skips the whole
      class otherwise.

Why both layers:
    Sprint 22 T22.8 calls for "unit tests for fuzzy match (with fixture
    markets)" — the "fixture markets" half needs actual pg_trgm running
    (the resolver's value lives in the SQL, not in Python). Mocking
    `similarity()` would not be a test of the function. The guard tests
    cover the parts that don't need a DB.

Why fixture questions are distinctive sentences:
    The resolver does not filter by canonical_event_id, so any
    pre-existing polymarket row in the DB whose `question` is similar
    to a fixture question could pollute results. We pick distinctive
    sentence patterns ("Will the Sprint 22 T22.1 fixture market resolve
    correctly here?") with little overlap with real Polymarket
    questions. The live DB at sprint open also has only seed rows with
    empty metadata_extension — none carry a `question` key, so the
    `metadata_extension ? 'question'` filter excludes them.

Why isolation via canonical_event_id prefix:
    Same pattern as every other persistence test in the project
    (conftest.py:74-150). `cleanup_momentum_vault` fixture deletes any
    row whose canonical_event_id starts with `test_<test_run_id>_`
    after each test function.

References:
    - data-pipeline/docs/agentic_hub_implementation_phase8_revised.md
      §Sprint 22 T22.1
    - persistence/momentum_vault.find_polymarket_market_by_question
    - tests/conftest.py (db_available, test_run_id, cleanup_momentum_vault)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from persistence import momentum_vault
from persistence.momentum_vault import (
    POLYMARKET_FUZZY_MATCH_DEFAULT_THRESHOLD,
    find_polymarket_market_by_question,
)


# ==========================================================
# Layer [A] — Pure-mock guards
# ==========================================================

class TestResolverGuards:
    """
    Behavior that does not require pg_trgm. `get_cursor` is patched at
    the lookup site (persistence.momentum_vault.get_cursor) so the DB
    is never touched.
    """

    def test_empty_question_returns_none_without_db_call(self):
        """Empty string skips the DB round-trip and returns None."""
        with patch("persistence.momentum_vault.get_cursor") as mock_cursor_ctx:
            result = find_polymarket_market_by_question("")
        assert result is None
        mock_cursor_ctx.assert_not_called()

    def test_whitespace_only_question_returns_none_without_db_call(self):
        """
        Whitespace-only input is semantically empty; treat the same as
        the empty-string case so we don't pay for a SQL call we know
        will score 0.0.
        """
        with patch("persistence.momentum_vault.get_cursor") as mock_cursor_ctx:
            result = find_polymarket_market_by_question("   \t\n  ")
        assert result is None
        mock_cursor_ctx.assert_not_called()

    def test_none_question_returns_none_defensively(self):
        """
        Defensive: a None question is the same as missing — the agent
        will never pass None, but the guard keeps the contract obvious.
        """
        with patch("persistence.momentum_vault.get_cursor") as mock_cursor_ctx:
            result = find_polymarket_market_by_question(None)  # type: ignore[arg-type]
        assert result is None
        mock_cursor_ctx.assert_not_called()

    def test_no_matching_row_returns_none(self):
        """
        Cursor returns None (no row passes the threshold) → resolver
        returns None.
        """
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = None

        with patch(
            "persistence.momentum_vault.get_cursor"
        ) as mock_cursor_ctx:
            mock_cursor_ctx.return_value.__enter__.return_value = mock_cur
            result = find_polymarket_market_by_question(
                "Will some unknown event happen by 2026?"
            )

        assert result is None

    def test_sql_parameters_bind_question_twice_then_threshold(self):
        """
        The SQL references the search text twice (once in the SELECT for
        match_score, once in the WHERE for the threshold filter) and the
        threshold once. Parameter ordering matters for psycopg2's
        positional substitution — pin it.
        """
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = None
        with patch("persistence.momentum_vault.get_cursor") as mock_cursor_ctx:
            mock_cursor_ctx.return_value.__enter__.return_value = mock_cur
            find_polymarket_market_by_question(
                "Will rates fall in Q3?", threshold=0.9,
            )
        args, _ = mock_cur.execute.call_args
        _sql, params = args
        assert params == ("Will rates fall in Q3?", "Will rates fall in Q3?", 0.9)

    def test_default_threshold_is_0_85(self):
        """
        Default threshold is the design-decision value (Sprint 22
        Confirmed design decisions). If this changes, the constant
        POLYMARKET_FUZZY_MATCH_DEFAULT_THRESHOLD changes too, and
        callers (Market Bridge in T22.2) get the new default.
        """
        assert POLYMARKET_FUZZY_MATCH_DEFAULT_THRESHOLD == 0.85

        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = None
        with patch("persistence.momentum_vault.get_cursor") as mock_cursor_ctx:
            mock_cursor_ctx.return_value.__enter__.return_value = mock_cur
            find_polymarket_market_by_question("Will X happen?")
        args, _ = mock_cur.execute.call_args
        _sql, params = args
        assert params[2] == 0.85

    def test_returns_dict_when_cursor_returns_row(self):
        """
        Cursor returns a row → resolver returns it as a dict, preserving
        all columns including the added `match_score`.
        """
        mock_row = {
            "metric_id": "test-metric-id",
            "canonical_event_id": "test-event-id",
            "source_name": "polymarket",
            "external_reference_id": "0xabc",
            "current_value": 0.62,
            "unit": "USD",
            "status": "active",
            "timestamp_utc": datetime.now(timezone.utc),
            "change_24h": 0.01,
            "change_7d": -0.04,
            "change_30d": 0.10,
            "is_new_market": False,
            "metadata_extension": {"question": "Will rates fall?"},
            "ingested_at": datetime.now(timezone.utc),
            "match_score": 0.93,
        }
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = mock_row
        with patch("persistence.momentum_vault.get_cursor") as mock_cursor_ctx:
            mock_cursor_ctx.return_value.__enter__.return_value = mock_cur
            result = find_polymarket_market_by_question("Will rates fall?")
        assert isinstance(result, dict)
        assert result["external_reference_id"] == "0xabc"
        assert result["match_score"] == 0.93
        assert result["metadata_extension"]["question"] == "Will rates fall?"


# ==========================================================
# Layer [B] — Live-DB fixture markets
# ==========================================================

@pytest.mark.usefixtures("db_available", "cleanup_momentum_vault")
class TestResolverFuzzyMatching:
    """
    Real pg_trgm behavior against fixture rows in the dev Postgres.

    Fixture market insert pattern:
        Bypass `momentum_vault.insert()` (which requires a full Gold
        Structured Metric envelope) and write directly via get_cursor —
        these tests want to control `metadata_extension.question`
        verbatim, which the insert() path can't do without an upstream
        Gold record. Direct insert is acceptable here because:
          (a) `insert()` is exercised by Gate 2/3 Polymarket Gold tests,
          (b) the resolver under test reads, not writes,
          (c) cleanup_momentum_vault wipes test rows by
              canonical_event_id prefix regardless of insert path.
    """

    @staticmethod
    def _insert_polymarket_row(
        *,
        test_run_id: str,
        question: str,
        external_reference_id: str,
        timestamp_utc: datetime,
        suffix: str = "",
    ) -> str:
        """
        Insert one fixture polymarket row with the test-run prefix
        on canonical_event_id so cleanup wipes it.
        """
        from psycopg2.extras import Json
        from utils.db import get_cursor

        metric_id = str(uuid.uuid4())
        canonical_event_id = f"test_{test_run_id}_resolver{suffix}"

        sql = """
            INSERT INTO momentum_vault (
                metric_id, canonical_event_id, source_name,
                external_reference_id, current_value, unit, status,
                timestamp_utc, change_24h, change_7d, change_30d,
                is_new_market, metadata_extension
            ) VALUES (
                %s, %s, 'polymarket', %s, 0.5, 'USD', 'active',
                %s, 0.0, 0.0, 0.0, false, %s
            );
        """
        meta_ext = {"question": question} if question is not None else {}
        with get_cursor() as cur:
            cur.execute(sql, (
                metric_id, canonical_event_id, external_reference_id,
                timestamp_utc, Json(meta_ext),
            ))
        return metric_id

    def test_exact_match_returns_row_with_score_1_0(self, test_run_id):
        """Verbatim match yields similarity(x, x) = 1.0."""
        self._insert_polymarket_row(
            test_run_id=test_run_id,
            question="Will the Sprint 22 T22.1 fixture market resolve correctly here?",
            external_reference_id="0xfixture_exact",
            timestamp_utc=datetime(2026, 5, 23, tzinfo=timezone.utc),
        )
        result = find_polymarket_market_by_question(
            "Will the Sprint 22 T22.1 fixture market resolve correctly here?"
        )
        assert result is not None
        assert result["external_reference_id"] == "0xfixture_exact"
        assert result["match_score"] == pytest.approx(1.0, abs=1e-9)

    def test_natural_variation_matches_via_pg_trgm_normalization(self, test_run_id):
        """
        pg_trgm normalizes input (lowercase + strip non-alphanumeric +
        unordered trigram set) before computing similarity. A user's
        natural-language rewrite that keeps the same words — different
        case, different punctuation, even reordered — yields
        similarity = 1.0. Empirically verified during T22.1 fixture
        design (2026-05-23): case-only, comma-added, extra-whitespace,
        dollar-sign-stripped, and word-reorder pairs all scored 1.0.

        This is why the 0.85 threshold is paraphrase-defense, NOT
        punctuation-defense — pg_trgm handles punctuation tolerance for
        free. The threshold's real job is rejecting paraphrases that
        change semantics (see test_moderate_paraphrase_below_threshold).
        """
        self._insert_polymarket_row(
            test_run_id=test_run_id,
            question="Will the Fed cut rates before June 2026?",
            external_reference_id="0xfixture_natural",
            timestamp_utc=datetime(2026, 5, 23, tzinfo=timezone.utc),
        )
        # Different case + extra commas + word reorder → still 1.0
        result = find_polymarket_market_by_question(
            "Before June, 2026 — will the Fed cut rates?"
        )
        assert result is not None
        assert result["external_reference_id"] == "0xfixture_natural"
        assert result["match_score"] == pytest.approx(1.0, abs=1e-9)

    def test_moderate_paraphrase_below_threshold_returns_none(self, test_run_id):
        """
        A genuine moderate paraphrase ("Fed" → "Federal Reserve", "cut"
        → "lower interest rates") scores ~0.53 against the original —
        well below the 0.85 threshold and correctly rejected. This is
        the V1 trade-off: paraphrase tolerance is Future Enhancement 4
        in the revised Phase 8 plan (vector index + cosine similarity).
        Empirically verified during T22.1 fixture design (2026-05-23):
        similarity = 0.530 for this exact pair.

        The Fed pair is the highest moderate-paraphrase score observed
        in fixture design — testing it confirms the threshold rejects
        even the closest paraphrase we've measured.
        """
        self._insert_polymarket_row(
            test_run_id=test_run_id,
            question="Will the Fed cut rates before June 2026?",
            external_reference_id="0xfixture_paraphrase",
            timestamp_utc=datetime(2026, 5, 23, tzinfo=timezone.utc),
        )
        result = find_polymarket_market_by_question(
            "Will the Federal Reserve lower interest rates before June 2026?"
        )
        assert result is None

    def test_custom_threshold_admits_lower_score(self, test_run_id):
        """
        Caller-supplied threshold should override the default. A lower
        threshold admits matches that would fail at 0.85.
        """
        self._insert_polymarket_row(
            test_run_id=test_run_id,
            question="Will the Sprint 22 fixture market admit lower-threshold matches?",
            external_reference_id="0xfixture_threshold",
            timestamp_utc=datetime(2026, 5, 23, tzinfo=timezone.utc),
        )
        weak_query = "Sprint 22 fixture market admit lower threshold"

        # Default 0.85 — should NOT match (sentence structure differs enough).
        assert find_polymarket_market_by_question(weak_query) is None

        # Lower threshold of 0.30 — should match.
        result = find_polymarket_market_by_question(weak_query, threshold=0.30)
        assert result is not None
        assert result["external_reference_id"] == "0xfixture_threshold"

    def test_no_match_returns_none(self, test_run_id):
        """
        A fixture market and a totally unrelated query produce a score
        well below threshold → None.
        """
        self._insert_polymarket_row(
            test_run_id=test_run_id,
            question="Will the Sprint 22 fixture market be findable by similarity?",
            external_reference_id="0xfixture_nomatch",
            timestamp_utc=datetime(2026, 5, 23, tzinfo=timezone.utc),
        )
        result = find_polymarket_market_by_question(
            "What is the airspeed velocity of an unladen swallow?"
        )
        assert result is None

    def test_most_recent_tiebreaker_when_scores_match(self, test_run_id):
        """
        Two identical-question rows (same external_reference_id,
        different timestamps) → ORDER BY match_score DESC,
        timestamp_utc DESC picks the latest one. This is what the
        Market Bridge agent wants for `current_odds` — the freshest
        observation.
        """
        same_question = "Will the Sprint 22 tiebreaker fixture pick the latest row?"
        same_ref = "0xfixture_tiebreaker"
        old_ts = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)
        new_ts = datetime(2026, 5, 23, 12, 0, 0, tzinfo=timezone.utc)

        self._insert_polymarket_row(
            test_run_id=test_run_id,
            question=same_question,
            external_reference_id=same_ref,
            timestamp_utc=old_ts,
            suffix="_old",
        )
        self._insert_polymarket_row(
            test_run_id=test_run_id,
            question=same_question,
            external_reference_id=same_ref,
            timestamp_utc=new_ts,
            suffix="_new",
        )

        result = find_polymarket_market_by_question(same_question)
        assert result is not None
        # tzinfo on the returned datetime can be psycopg2's, normalise to UTC
        returned_ts = result["timestamp_utc"]
        if returned_ts.tzinfo is None:
            returned_ts = returned_ts.replace(tzinfo=timezone.utc)
        assert returned_ts == new_ts

    def test_rows_without_question_key_are_excluded(self, test_run_id):
        """
        The `metadata_extension ? 'question'` JSONB key-exists filter
        must exclude rows whose metadata_extension has no question key
        at all (pre-Sprint-22 rows, or WebSocket-only markets that the
        silver_job patch stores as empty string under the key).

        Inserts one row with NO question key plus one row WITH a
        matching question, queries with the matching text. Only the
        latter should be returned, proving the key-exists filter
        actually filters.
        """
        # Row without 'question' key in metadata_extension — must be excluded.
        self._insert_polymarket_row(
            test_run_id=test_run_id,
            question=None,  # → metadata_extension = {}
            external_reference_id="0xfixture_noquestion",
            timestamp_utc=datetime(2026, 5, 24, tzinfo=timezone.utc),
            suffix="_nokey",
        )
        # Row WITH 'question' key — must be the match.
        self._insert_polymarket_row(
            test_run_id=test_run_id,
            question="Will the Sprint 22 exclusion fixture filter out the no-key row?",
            external_reference_id="0xfixture_haskey",
            timestamp_utc=datetime(2026, 5, 23, tzinfo=timezone.utc),
            suffix="_haskey",
        )

        result = find_polymarket_market_by_question(
            "Will the Sprint 22 exclusion fixture filter out the no-key row?"
        )
        assert result is not None
        assert result["external_reference_id"] == "0xfixture_haskey"
