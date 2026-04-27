# ==========================================================
# Anizai Data Pipeline — FRED Daily DAG
# ==========================================================
# Runs the FRED producer once per day at 06:00 UTC (Section 2.3).
#
# Producer:   ingestion/fred_producer.py  main(mode="pulse")
# Schedule:   0 6 * * *   (daily, 06:00 UTC)
# Retries:    3 attempts, 5 minutes apart
# Mode:       "pulse" — fetches latest values for all FRED_SERIES
#
# Design decisions (Section 6A):
#   D3 — PythonOperator: direct Python function call, no subprocess overhead.
#   D4 — LocalExecutor: tasks run as scheduler subprocesses; env vars inherited
#        from docker-compose (including KAFKA_BOOTSTRAP_SERVERS=kafka:29092).
#   D5 — Daily pollers staggered at 6/7/8 AM UTC to avoid FRED/ArXiv/Trends
#        all hammering resources simultaneously on first run.
#
# Reactive coexistence (Section 2.4):
#   Airflow calls run_pulse() via main(mode="pulse").
#   ingestion_trigger_consumer.py calls run_reactive() on-demand via daemon threads.
#   These are independent code paths with separate Kafka producer connections.
#   No scheduling conflict.
#
# PYTHONPATH=/opt/airflow/data-pipeline (set in docker-compose airflow-scheduler)
# enables `from ingestion.fred_producer import main` to resolve correctly.
# ==========================================================

from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator

# Import deferred to task callable — avoids import-time side effects
# (e.g. load_dotenv(), logging setup) running inside the Airflow parser process.
def _run_fred_pulse() -> None:
    """
    Invoke FRED producer in pulse mode.

    Imports at call time so the Airflow DAG parser does not execute producer
    side effects (Kafka client init, dotenv loading) during DAG file parsing.
    """
    from ingestion.fred_producer import main  # noqa: PLC0415
    main(mode="pulse")


with DAG(
    dag_id="fred_daily",
    description="FRED economic indicators — daily pulse fetch (Section B.3)",
    schedule_interval="0 6 * * *",
    start_date=datetime(2024, 1, 1),
    catchup=False,
    max_active_runs=1,
    default_args={
        "owner": "anizai",
        "depends_on_past": False,
        "retries": 3,
        "retry_delay": timedelta(minutes=5),
    },
    tags=["anizai", "ingestion", "fred", "daily"],
) as dag:
    PythonOperator(
        task_id="fred_pulse",
        python_callable=_run_fred_pulse,
    )
