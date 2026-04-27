# ==========================================================
# Anizai Data Pipeline — Google Trends Daily DAG
# ==========================================================
# Runs the GoogleTrends producer once per day at 08:00 UTC (Section 2.3).
#
# Producer:   ingestion/googletrends_producer.py  main(mode="static")
# Schedule:   0 8 * * *   (daily, 08:00 UTC)
# Retries:    2 attempts, 5 minutes apart
# Mode:       "static" — fetches interest-over-time for all STATIC_KEYWORDS
#             across GEO_MATRIX (4 geos: US, GB, DE, JP)
#
# Design decisions (Section 6A):
#   D3 — PythonOperator: direct Python function call.
#   D4 — LocalExecutor: KAFKA_BOOTSTRAP_SERVERS=kafka:29092 inherited from
#        airflow-scheduler container environment.
#   D5 — Staggered 2 hours after FRED (08:00 vs 06:00) to avoid simultaneous
#        daily producer startup. GoogleTrends retries set to 2 (vs 3 for FRED/
#        ArXiv) because Pytrends hits Google's unofficial endpoint — repeated
#        rapid retries risk triggering captcha / rate-limit blocks.
#
# GoogleTrends mode note (Section B.5, Phase 4F):
#   Three modes exist: static (daily keyword set), reactive (triggered keywords),
#   backfill (historical fetch). Airflow runs "static" only; reactive mode is
#   triggered by ingestion_trigger_consumer.py via run_reactive() — independent
#   code path, no scheduling conflict.
#
# PYTHONPATH=/opt/airflow/data-pipeline resolves `from ingestion.googletrends_producer`.
# ==========================================================

from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator


def _run_googletrends_static() -> None:
    """
    Invoke GoogleTrends producer in static mode.

    Imports at call time so the DAG parser does not trigger Pytrends session
    initialization or dotenv loading during DAG file parsing.
    """
    from ingestion.googletrends_producer import main  # noqa: PLC0415
    main(mode="static")


with DAG(
    dag_id="googletrends_daily",
    description="Google Trends static keyword fetch — daily (Section B.5)",
    schedule_interval="0 8 * * *",
    start_date=datetime(2024, 1, 1),
    catchup=False,
    max_active_runs=1,
    default_args={
        "owner": "anizai",
        "depends_on_past": False,
        "retries": 2,
        "retry_delay": timedelta(minutes=5),
    },
    tags=["anizai", "ingestion", "googletrends", "daily"],
) as dag:
    PythonOperator(
        task_id="googletrends_static",
        python_callable=_run_googletrends_static,
    )
