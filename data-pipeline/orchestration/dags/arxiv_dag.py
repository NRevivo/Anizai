# ==========================================================
# Anizai Data Pipeline — ArXiv Daily DAG
# ==========================================================
# Runs the ArXiv producer once per day at 07:00 UTC (Section 2.3).
#
# Producer:   ingestion/arxiv_producer.py  main(mode="pulse")
# Schedule:   0 7 * * *   (daily, 07:00 UTC)
# Retries:    3 attempts, 5 minutes apart
# Mode:       "pulse" — fetches latest papers for all ARXIV_CATEGORIES
#
# Design decisions (Section 6A):
#   D3 — PythonOperator: direct Python function call.
#   D4 — LocalExecutor: env vars (KAFKA_BOOTSTRAP_SERVERS=kafka:29092) inherited
#        from the airflow-scheduler container environment.
#   D5 — Staggered 1 hour after FRED (07:00 vs 06:00) to spread resource usage.
#
# ArXiv rate-limit note (Section B.1):
#   The arxiv library enforces a mandatory 3-second inter-request delay per ToS.
#   A single daily run fetches up to ARXIV_MAX_RESULTS=200 papers per category.
#   Retry delay of 5 minutes is well within ArXiv's per-IP throttle window.
#
# Reactive coexistence: Airflow calls run_pulse(); ingestion_trigger_consumer.py
# calls run_reactive() via daemon threads — independent code paths, no conflict.
#
# PYTHONPATH=/opt/airflow/data-pipeline resolves `from ingestion.arxiv_producer`.
# ==========================================================

from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator


def _run_arxiv_pulse() -> None:
    """
    Invoke ArXiv producer in pulse mode.

    Imports at call time so the DAG parser does not trigger producer
    side effects (Kafka client init, arxiv library session) during parsing.
    """
    from ingestion.arxiv_producer import main  # noqa: PLC0415
    main(mode="pulse")


with DAG(
    dag_id="arxiv_daily",
    description="ArXiv research papers — daily pulse fetch (Section B.1)",
    schedule_interval="0 7 * * *",
    start_date=datetime(2024, 1, 1),
    catchup=False,
    max_active_runs=1,
    default_args={
        "owner": "anizai",
        "depends_on_past": False,
        "retries": 3,
        "retry_delay": timedelta(minutes=5),
    },
    tags=["anizai", "ingestion", "arxiv", "daily"],
) as dag:
    PythonOperator(
        task_id="arxiv_pulse",
        python_callable=_run_arxiv_pulse,
    )
