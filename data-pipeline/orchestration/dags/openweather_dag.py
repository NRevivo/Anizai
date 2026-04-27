# ==========================================================
# Anizai Data Pipeline — OpenWeather High-Frequency DAG
# ==========================================================
# Runs the OpenWeather producer every 10 minutes (Section 2.3).
#
# Producer:   ingestion/openweather_producer.py  main(mode="static")
# Schedule:   */10 * * * *   (every 10 minutes)
# Retries:    2 attempts, 1 minute apart
# Mode:       "static" — fetches current conditions for all MONITORING_HOTSPOTS
#
# Design decisions (Section 6A):
#   D3 — PythonOperator: direct Python function call.
#   D4 — LocalExecutor: KAFKA_BOOTSTRAP_SERVERS=kafka:29092 inherited from
#        airflow-scheduler container environment.
#   10-minute cadence chosen because weather conditions (wind, humidity,
#   precipitation) are meaningful signals at sub-hourly resolution for
#   supply-chain and shipping disruption alerts (Section B.6).
#   Retry delay of 1 min (not 2) — within a 10-minute window, faster
#   retry recovery is critical to avoid losing the window entirely.
#
# Reactive coexistence: Airflow calls main(mode="static"); ingestion_trigger_consumer.py
# calls run_reactive() via daemon threads — independent code paths, no conflict.
#
# max_active_runs=1: prevents overlapping invocations if OpenWeather API is slow.
#
# PYTHONPATH=/opt/airflow/data-pipeline resolves `from ingestion.openweather_producer`.
# ==========================================================

from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator


def _run_openweather_static() -> None:
    """
    Invoke OpenWeather producer in static mode.

    Imports at call time so the DAG parser does not trigger Kafka client
    init or dotenv loading during DAG file parsing.
    """
    from ingestion.openweather_producer import main  # noqa: PLC0415
    main(mode="static")


with DAG(
    dag_id="openweather_high_frequency",
    description="OpenWeather conditions — every 10 min static fetch (Section B.6)",
    schedule_interval="*/10 * * * *",
    start_date=datetime(2024, 1, 1),
    catchup=False,
    max_active_runs=1,
    default_args={
        "owner": "anizai",
        "depends_on_past": False,
        "retries": 2,
        "retry_delay": timedelta(minutes=1),
    },
    tags=["anizai", "ingestion", "openweather", "high-frequency"],
) as dag:
    PythonOperator(
        task_id="openweather_static",
        python_callable=_run_openweather_static,
    )
