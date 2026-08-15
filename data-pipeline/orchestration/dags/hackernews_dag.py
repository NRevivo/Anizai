# ==========================================================
# Anizai Data Pipeline — Hacker News High-Frequency DAG
# ==========================================================
# Runs the Hacker News producer every 60 minutes (Section 2.3).
#
# Producer:   ingestion/hackernews_producer.py  main(mode="pulse")
# Schedule:   0 * * * *   (hourly)
# Retries:    2 attempts, 2 minutes apart
# Mode:       "pulse" — fetches latest stories from Algolia HN API
#
# Design decisions (Section 6A):
#   D3 — PythonOperator: direct Python function call.
#   D4 — LocalExecutor: KAFKA_BOOTSTRAP_SERVERS=kafka:29092 inherited from
#        airflow-scheduler container environment.
#   Algolia HN API is a public endpoint (no key) with generous rate limits,
#   so the cadence is NOT bounded by the upstream API — it is bounded by the
#   OpenAI RPD ceiling downstream.
#
#   CADENCE CHANGED */20 -> hourly, 2026-08-15 (KG-C-1).
#   Measured during the 2026-08-15 continuous bring-up: HN cost 47.3 OpenAI
#   calls per run, which at */20 (72 runs/day) is ~3,406 calls/day — the
#   single largest consumer of the shared Tier-1 10,000 RPD ceiling. Total
#   projected load was ~6-7k/day measured on a SATURDAY, i.e. at the weekly
#   minimum; a weekday at 1.5-2x would reach 9-14k and exhaust the ceiling,
#   which blocks the agent's own forecasts (KG-C-1) as well as Gold. Hourly
#   removes ~2,270 calls/day at the least cost to signal, because HN
#   front-page turnover is slower than 20 minutes — a */20 pulse largely
#   re-reads the same stories (compare KG-A-19 on repeat rescue evals).
#
#   This deliberately BREAKS the previous alignment with newsapi_dag.py.
#   That symmetry was for Silver consensus (both social sources refreshing
#   together); consensus still works with differing cadences, and the RPD
#   ceiling is the harder constraint. Revisit if Tier is upgraded.
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
    description="Hacker News stories — hourly pulse fetch (Section B.2)",
    schedule_interval="0 * * * *",
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
