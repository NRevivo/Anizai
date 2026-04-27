"""
T7 — DAG Import Validation Tests (Section 9.3 Gate 1, Phase 6A / Sprint 15).

Validates that all 8 Airflow DAG files:
  1. Import without errors (no syntax bugs, no import-time side effects)
  2. Define exactly one task
  3. Use the correct schedule_interval
  4. Have max_active_runs=1 (overlap prevention)
  5. Have catchup=False (no historical backfill)
  6. Contain the expected task_id

These are pure structural tests — no Kafka broker, no database, no API calls.
All imports are deferred inside each DAG's callable, so DAG parsing does not
trigger producer side effects (Section 2.3, Design Decision D3).

Test matrix:
  [01] fred_daily                  — 0 6 * * *     (daily 06:00 UTC)
  [02] arxiv_daily                 — 0 7 * * *     (daily 07:00 UTC)
  [03] googletrends_daily          — 0 8 * * *     (daily 08:00 UTC)
  [04] newsapi_high_frequency      — */20 * * * *  (every 20 min)
  [05] hackernews_high_frequency   — */20 * * * *  (every 20 min)
  [06] openweather_high_frequency  — */10 * * * *  (every 10 min)
  [07] opensky_high_frequency      — */3 * * * *   (every 3 min, rate-limit-safe)
  [08] newsapi_scraper             — */30 * * * *  (every 30 min, post-Silver enrichment)

Why test_id format "[NN]" — mirrors the Gate 1 checklist numbering in the
spec (Section 9.3) so failures map directly to a checklist item.

References:
    - Section 2.3:  Ingestion Orchestration — schedule cadences
    - Section 6A:   Phase 6A design decisions (D3, D4, D5)
    - Section 8.2:  Docker stack — Airflow services
    - Section 9.3:  Triple-Gate Test Matrix — Gate 1
    - Section B.1–B.7: Source-specific schedule and retry parameters
    - Sprint 15:    newsapi_scraper DAG — post-Silver article scraping layer
"""

import pytest

# ==========================================================
# DAG import fixtures
# ==========================================================
# Each fixture imports the DAG object at test-collection time.
# If any DAG file has a syntax error or an import-time side effect that
# raises (e.g., a Kafka producer created at module scope), the test
# fails here with a clear ImportError — not a cryptic AttributeError.

from orchestration.dags.fred_dag import dag as fred_dag
from orchestration.dags.arxiv_dag import dag as arxiv_dag
from orchestration.dags.googletrends_dag import dag as googletrends_dag
from orchestration.dags.newsapi_dag import dag as newsapi_dag
from orchestration.dags.hackernews_dag import dag as hackernews_dag
from orchestration.dags.openweather_dag import dag as openweather_dag
from orchestration.dags.opensky_dag import dag as opensky_dag
from orchestration.dags.scraper_dag import dag as scraper_dag

_ALL_DAGS = [
    fred_dag,
    arxiv_dag,
    googletrends_dag,
    newsapi_dag,
    hackernews_dag,
    openweather_dag,
    opensky_dag,
    scraper_dag,
]

# Ingestion-only DAGs: scraper_dag is a post-Silver enrichment DAG, not ingestion,
# so it carries "enrichment" tag instead of "ingestion" (Section 3.3 service isolation).
_INGESTION_DAGS = [
    fred_dag,
    arxiv_dag,
    googletrends_dag,
    newsapi_dag,
    hackernews_dag,
    openweather_dag,
    opensky_dag,
]


# ==========================================================
# [01] fred_daily
# ==========================================================

class TestFredDag:
    """[01] fred_daily DAG structural invariants."""

    def test_01_dag_id(self):
        assert fred_dag.dag_id == "fred_daily"

    def test_01_schedule(self):
        assert fred_dag.schedule_interval == "0 6 * * *"

    def test_01_single_task(self):
        assert len(fred_dag.tasks) == 1

    def test_01_task_id(self):
        assert fred_dag.tasks[0].task_id == "fred_pulse"

    def test_01_max_active_runs(self):
        assert fred_dag.max_active_runs == 1

    def test_01_catchup_false(self):
        assert fred_dag.catchup is False

    def test_01_retries(self):
        assert fred_dag.default_args["retries"] == 3


# ==========================================================
# [02] arxiv_daily
# ==========================================================

class TestArxivDag:
    """[02] arxiv_daily DAG structural invariants."""

    def test_02_dag_id(self):
        assert arxiv_dag.dag_id == "arxiv_daily"

    def test_02_schedule(self):
        assert arxiv_dag.schedule_interval == "0 7 * * *"

    def test_02_single_task(self):
        assert len(arxiv_dag.tasks) == 1

    def test_02_task_id(self):
        assert arxiv_dag.tasks[0].task_id == "arxiv_pulse"

    def test_02_max_active_runs(self):
        assert arxiv_dag.max_active_runs == 1

    def test_02_catchup_false(self):
        assert arxiv_dag.catchup is False

    def test_02_retries(self):
        assert arxiv_dag.default_args["retries"] == 3


# ==========================================================
# [03] googletrends_daily
# ==========================================================

class TestGoogletrendsDag:
    """[03] googletrends_daily DAG structural invariants."""

    def test_03_dag_id(self):
        assert googletrends_dag.dag_id == "googletrends_daily"

    def test_03_schedule(self):
        assert googletrends_dag.schedule_interval == "0 8 * * *"

    def test_03_single_task(self):
        assert len(googletrends_dag.tasks) == 1

    def test_03_task_id(self):
        assert googletrends_dag.tasks[0].task_id == "googletrends_static"

    def test_03_max_active_runs(self):
        assert googletrends_dag.max_active_runs == 1

    def test_03_catchup_false(self):
        assert googletrends_dag.catchup is False

    def test_03_retries(self):
        # 2 retries (not 3) — rapid Pytrends retries risk captcha/rate-limit blocks
        assert googletrends_dag.default_args["retries"] == 2


# ==========================================================
# [04] newsapi_high_frequency
# ==========================================================

class TestNewsapiDag:
    """[04] newsapi_high_frequency DAG structural invariants."""

    def test_04_dag_id(self):
        assert newsapi_dag.dag_id == "newsapi_high_frequency"

    def test_04_schedule(self):
        assert newsapi_dag.schedule_interval == "*/20 * * * *"

    def test_04_single_task(self):
        assert len(newsapi_dag.tasks) == 1

    def test_04_task_id(self):
        assert newsapi_dag.tasks[0].task_id == "newsapi_pulse"

    def test_04_max_active_runs(self):
        assert newsapi_dag.max_active_runs == 1

    def test_04_catchup_false(self):
        assert newsapi_dag.catchup is False

    def test_04_retries(self):
        assert newsapi_dag.default_args["retries"] == 2


# ==========================================================
# [05] hackernews_high_frequency
# ==========================================================

class TestHackernewsDag:
    """[05] hackernews_high_frequency DAG structural invariants."""

    def test_05_dag_id(self):
        assert hackernews_dag.dag_id == "hackernews_high_frequency"

    def test_05_schedule(self):
        assert hackernews_dag.schedule_interval == "*/20 * * * *"

    def test_05_single_task(self):
        assert len(hackernews_dag.tasks) == 1

    def test_05_task_id(self):
        assert hackernews_dag.tasks[0].task_id == "hackernews_pulse"

    def test_05_max_active_runs(self):
        assert hackernews_dag.max_active_runs == 1

    def test_05_catchup_false(self):
        assert hackernews_dag.catchup is False

    def test_05_retries(self):
        assert hackernews_dag.default_args["retries"] == 2


# ==========================================================
# [06] openweather_high_frequency
# ==========================================================

class TestOpenweatherDag:
    """[06] openweather_high_frequency DAG structural invariants."""

    def test_06_dag_id(self):
        assert openweather_dag.dag_id == "openweather_high_frequency"

    def test_06_schedule(self):
        assert openweather_dag.schedule_interval == "*/10 * * * *"

    def test_06_single_task(self):
        assert len(openweather_dag.tasks) == 1

    def test_06_task_id(self):
        assert openweather_dag.tasks[0].task_id == "openweather_static"

    def test_06_max_active_runs(self):
        assert openweather_dag.max_active_runs == 1

    def test_06_catchup_false(self):
        assert openweather_dag.catchup is False

    def test_06_retries(self):
        assert openweather_dag.default_args["retries"] == 2


# ==========================================================
# [07] opensky_high_frequency
# ==========================================================

class TestOpenskyDag:
    """[07] opensky_high_frequency DAG structural invariants."""

    def test_07_dag_id(self):
        assert opensky_dag.dag_id == "opensky_high_frequency"

    def test_07_schedule(self):
        # 3-min cadence: 480 cycles/day × 7 boxes = 3,360 calls/day (within 4,000/day cap)
        assert opensky_dag.schedule_interval == "*/3 * * * *"

    def test_07_single_task(self):
        assert len(opensky_dag.tasks) == 1

    def test_07_task_id(self):
        assert opensky_dag.tasks[0].task_id == "opensky_static"

    def test_07_max_active_runs(self):
        assert opensky_dag.max_active_runs == 1

    def test_07_catchup_false(self):
        assert opensky_dag.catchup is False

    def test_07_retries(self):
        # 1 retry — minimal; within 3-min window a second attempt is timely
        assert opensky_dag.default_args["retries"] == 1


# ==========================================================
# [08] newsapi_scraper
# ==========================================================

class TestScraperDag:
    """[08] newsapi_scraper DAG structural invariants (Sprint 15)."""

    def test_08_dag_id(self):
        assert scraper_dag.dag_id == "newsapi_scraper"

    def test_08_schedule(self):
        # 30-min cadence: up to 40 articles/hour scraped at MAX_SCRAPE_PER_RUN=20
        assert scraper_dag.schedule_interval == "*/30 * * * *"

    def test_08_single_task(self):
        assert len(scraper_dag.tasks) == 1

    def test_08_task_id(self):
        assert scraper_dag.tasks[0].task_id == "scrape_articles"

    def test_08_max_active_runs(self):
        assert scraper_dag.max_active_runs == 1

    def test_08_catchup_false(self):
        assert scraper_dag.catchup is False

    def test_08_retries(self):
        # 2 retries — brief retry window acceptable for 30-min cadence
        assert scraper_dag.default_args["retries"] == 2

    def test_08_enrichment_tag(self):
        """Scraper is post-Silver enrichment — must carry 'enrichment' tag, not 'ingestion'."""
        assert "enrichment" in scraper_dag.tags
        assert "ingestion" not in scraper_dag.tags


# ==========================================================
# Cross-DAG invariants
# ==========================================================

class TestAllDagsInvariants:
    """Invariants that must hold across all 8 DAGs."""

    def test_all_have_anizai_tag(self):
        """Every DAG must carry the 'anizai' tag for UI filtering."""
        for dag in _ALL_DAGS:
            assert "anizai" in dag.tags, f"{dag.dag_id} missing 'anizai' tag"

    def test_ingestion_dags_have_ingestion_tag(self):
        """Ingestion DAGs must carry the 'ingestion' tag (scraper_dag excluded — it's enrichment)."""
        for dag in _INGESTION_DAGS:
            assert "ingestion" in dag.tags, f"{dag.dag_id} missing 'ingestion' tag"

    def test_all_catchup_false(self):
        """All DAGs must have catchup=False — no historical backfill on deploy."""
        for dag in _ALL_DAGS:
            assert dag.catchup is False, f"{dag.dag_id} has catchup=True"

    def test_all_max_active_runs_one(self):
        """All DAGs must have max_active_runs=1 — no overlapping executions."""
        for dag in _ALL_DAGS:
            assert dag.max_active_runs == 1, (
                f"{dag.dag_id} max_active_runs={dag.max_active_runs}"
            )

    def test_all_owner_is_anizai(self):
        """All DAGs must set owner='anizai'."""
        for dag in _ALL_DAGS:
            assert dag.default_args.get("owner") == "anizai", (
                f"{dag.dag_id} owner={dag.default_args.get('owner')}"
            )

    def test_all_depends_on_past_false(self):
        """All DAGs must have depends_on_past=False — independent run cadence."""
        for dag in _ALL_DAGS:
            assert dag.default_args.get("depends_on_past") is False, (
                f"{dag.dag_id} depends_on_past is not False"
            )

    def test_all_have_exactly_one_task(self):
        """All DAGs are single-task — one producer invocation per schedule tick."""
        for dag in _ALL_DAGS:
            assert len(dag.tasks) == 1, (
                f"{dag.dag_id} has {len(dag.tasks)} tasks (expected 1)"
            )

    def test_no_duplicate_dag_ids(self):
        """All 8 DAG IDs must be unique."""
        dag_ids = [d.dag_id for d in _ALL_DAGS]
        assert len(dag_ids) == len(set(dag_ids)), f"Duplicate DAG IDs: {dag_ids}"
