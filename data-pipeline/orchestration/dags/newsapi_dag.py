# ==========================================================
# Anizai Data Pipeline — NewsAPI High-Frequency DAG
# ==========================================================
# Runs the NewsAPI producer every 20 minutes (Section 2.3).
#
# Producer:   ingestion/newsapi_producer.py  main(mode="pulse")
# Provider:   newsapi.ai (Event Registry) — eventregistry.org (Phase 7A)
# Schedule:   */20 * * * *   (every 20 minutes)
# Retries:    2 attempts, 2 minutes apart
# Mode:       "pulse" — fetches latest articles for all 5 category URIs
#
# Design decisions (Section 6A):
#   D3 — PythonOperator: direct Python function call.
#   D4 — LocalExecutor: KAFKA_BOOTSTRAP_SERVERS=kafka:29092 inherited from
#        airflow-scheduler container environment.
#   High-frequency justification: newsapi.ai publishes articles continuously;
#   20-minute cadence balances freshness vs. request cost.
#   Shorter retry delay (2 min vs 5 min for daily jobs) because within a
#   20-minute window a quick retry is still timely; a 5-minute wait would
#   consume the full interval.
#
# Reactive coexistence: Airflow calls run_pulse(); ingestion_trigger_consumer.py
# calls run_reactive() via daemon threads — independent code paths, no conflict.
#
# max_active_runs=1: prevents overlapping runs if a previous 20-min task
# is still executing (e.g., slow newsapi.ai response under load).
#
# PYTHONPATH=/opt/airflow/data-pipeline resolves `from ingestion.newsapi_producer`.
# ==========================================================

from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator


def _run_newsapi_pulse() -> None:
    """
    Invoke NewsAPI producer in pulse mode.

    Imports at call time so the DAG parser does not trigger Kafka client
    init or dotenv loading during DAG file parsing.
    """
    from ingestion.newsapi_producer import main  # noqa: PLC0415
    main(mode="pulse")


with DAG(
    dag_id="newsapi_high_frequency",
    description="NewsAPI articles — every 20 min pulse fetch (Section B.4)",
    schedule_interval="*/20 * * * *",
    start_date=datetime(2024, 1, 1),
    catchup=False,
    max_active_runs=1,
    is_paused_upon_creation=True,
    default_args={
        "owner": "anizai",
        "depends_on_past": False,
        "retries": 2,
        "retry_delay": timedelta(minutes=2),
    },
    tags=["anizai", "ingestion", "newsapi", "high-frequency"],
) as dag:
    PythonOperator(
        task_id="newsapi_pulse",
        python_callable=_run_newsapi_pulse,
    )
