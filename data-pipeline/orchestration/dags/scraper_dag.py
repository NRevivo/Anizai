# ==========================================================
# Anizai Data Pipeline — NewsAPI Article Scraper DAG
# ==========================================================
# Runs the post-Silver article scraping cycle every 30 minutes (Sprint 15).
#
# Runner:   orchestration/scraper_runner.py  run()
# Schedule: */30 * * * *  (every 30 minutes)
# Cap:      MAX_SCRAPE_PER_RUN = 20 articles per cycle
#
# Architecture position:
#   Runs AFTER newsapi Silver articles land in knowledge_vault.
#   Reads articles where scrape_attempted=FALSE, fetches full text
#   from scrapable outlets (BBC, Guardian, ToI, JPost, Ynetnews, i24news),
#   and updates knowledge_vault.full_text_raw for RAG retrieval quality.
#
# Design decisions (Section 6A / Sprint 15):
#   D3 — PythonOperator: direct Python function call (same pattern as fred_dag.py).
#   max_active_runs=1: prevents overlapping scrape cycles on the same rows.
#   catchup=False: historical DAG runs are not backfilled; the runner queries
#       the live DB on each execution and naturally processes oldest articles first.
#   30-min interval: balances freshness against rate-limit risk on scrapable outlets.
#       At MAX_SCRAPE_PER_RUN=20 per cycle → up to 40 articles/hour scraped.
#
# PYTHONPATH=/opt/airflow/data-pipeline (set in docker-compose airflow-scheduler)
# enables `from orchestration.scraper_runner import run` to resolve correctly.
# ==========================================================

from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator


# Import deferred to task callable — avoids import-time side effects
# (DB connection, dotenv loading) running inside the Airflow parser process
# (Section 6A Design Decision D3).
def _run_scraping_cycle() -> None:
    """
    Invoke the scraper runner for one 30-minute cycle.

    Imports at call time so the Airflow DAG parser does not execute
    DB or library side effects during DAG file parsing.
    """
    from orchestration.scraper_runner import run  # noqa: PLC0415
    stats = run()
    # Log cycle summary to Airflow task logs for observability
    import logging  # noqa: PLC0415
    logging.getLogger(__name__).info(
        "Scraping cycle complete: attempted=%d  succeeded=%d  "
        "skipped=%d  failed=%d",
        stats["attempted"],
        stats["succeeded"],
        stats["skipped"],
        stats["failed"],
    )


with DAG(
    dag_id="newsapi_scraper",
    description="Post-Silver article scraping — full-text enrichment every 30 min (Sprint 15)",
    schedule_interval="*/30 * * * *",
    start_date=datetime(2024, 1, 1),
    catchup=False,
    max_active_runs=1,
    default_args={
        "owner": "anizai",
        "depends_on_past": False,
        "retries": 2,
        "retry_delay": timedelta(minutes=3),
    },
    tags=["anizai", "scraping", "newsapi", "enrichment"],
) as dag:
    PythonOperator(
        task_id="scrape_articles",
        python_callable=_run_scraping_cycle,
    )
