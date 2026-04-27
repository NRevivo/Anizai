# ==========================================================
# Anizai Data Pipeline — Hacker News High-Frequency DAG
# ==========================================================
# Runs the Hacker News producer every 20 minutes (Section 2.3).
#
# Producer:   ingestion/hackernews_producer.py  main(mode="pulse")
# Schedule:   */20 * * * *   (every 20 minutes)
# Retries:    2 attempts, 2 minutes apart
# Mode:       "pulse" — fetches latest stories from Algolia HN API
#
# Design decisions (Section 6A):
#   D3 — PythonOperator: direct Python function call.
#   D4 — LocalExecutor: KAFKA_BOOTSTRAP_SERVERS=kafka:29092 inherited from
#        airflow-scheduler container environment.
#   Algolia HN API is a public endpoint (no key) with generous rate limits —
#   20-minute cadence is well within safe usage bounds.
#   Aligned with newsapi_dag.py schedule so both social signal sources
#   refresh at the same cadence (symmetric ingestion for Silver consensus).
#
# Reactive coexistence: Airflow calls run_pulse(); ingestion_trigger_consumer.py
# calls run_reactive() via daemon threads — independent code paths, no conflict.
#
# max_active_runs=1: prevents schedule overlap if Algolia is slow to respond.
#
# PYTHONPATH=/opt/airflow/data-pipeline resolves `from ingestion.hackernews_producer`.
# ==========================================================

from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator


def _run_hackernews_pulse() -> None:
    """
    Invoke Hacker News producer in pulse mode.

    Imports at call time so the DAG parser does not trigger Kafka client
    init or dotenv loading during DAG file parsing.
    """
    from ingestion.hackernews_producer import main  # noqa: PLC0415
    main(mode="pulse")


with DAG(
    dag_id="hackernews_high_frequency",
    description="Hacker News stories — every 20 min pulse fetch (Section B.2)",
    schedule_interval="*/20 * * * *",
    start_date=datetime(2024, 1, 1),
    catchup=False,
    max_active_runs=1,
    default_args={
        "owner": "anizai",
        "depends_on_past": False,
        "retries": 2,
        "retry_delay": timedelta(minutes=2),
    },
    tags=["anizai", "ingestion", "hackernews", "high-frequency"],
) as dag:
    PythonOperator(
        task_id="hackernews_pulse",
        python_callable=_run_hackernews_pulse,
    )
