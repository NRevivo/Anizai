# ==========================================================
# Anizai Data Pipeline — OpenSky DAG
# ==========================================================
# Runs the OpenSky producer every 3 minutes (Section 2.3).
#
# Producer:   ingestion/opensky_producer.py  main(mode="static")
# Schedule:   */3 * * * *   (every 3 minutes)
# Retries:    1 attempt, 30 seconds apart
# Mode:       "static" — fetches aircraft states for all 7 strategic bounding boxes
#
# Rate limit design (Section B.7, March 2026 OAuth2 migration):
#   Authenticated (OPENSKY_CLIENT_ID + OPENSKY_CLIENT_SECRET set):
#     4,000 calls/day cap. 3-min cadence → 480 cycles/day × 7 boxes = 3,360
#     calls/day — 16% safety buffer under the daily cap.
#   Anonymous: ~100 calls/day — effectively unusable at this cadence; the DAG
#     should only run if OAuth2 credentials are set.
#   Original 1-min cadence would produce 10,080 calls/day (2.5× the cap).
#
# Design decisions (Section 6A):
#   D3 — PythonOperator: direct Python function call; no subprocess overhead.
#   D4 — LocalExecutor: KAFKA_BOOTSTRAP_SERVERS=kafka:29092 and
#        OPENSKY_CLIENT_ID/SECRET inherited from airflow-scheduler environment.
#   Retry policy: 1 retry × 30 s — OpenSky is occasionally slow but a second
#     attempt within 30 s is safe within the 3-minute window. A missed cycle
#     is preferable to burning two quota slots on flaky infra.
#
# max_active_runs=1: prevents a slow 7-box cycle from overlapping with the
# next scheduled run (each cycle takes ~8 s under 1.0 s inter-box delay).
#
# PYTHONPATH=/opt/airflow/data-pipeline resolves `from ingestion.opensky_producer`.
# ==========================================================

from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator


def _run_opensky_static() -> None:
    """
    Invoke OpenSky producer in static mode.

    OAuth2 token fetch (_ensure_token) happens inside the producer before
    the first box request — no token management needed at the DAG level.
    Imports at call time so the DAG parser does not trigger Kafka client
    init or dotenv loading during DAG file parsing.
    """
    from ingestion.opensky_producer import main  # noqa: PLC0415
    main(mode="static")


with DAG(
    dag_id="opensky_high_frequency",
    description="OpenSky aircraft states — every 3 min static fetch (Section B.7)",
    schedule_interval="*/3 * * * *",
    start_date=datetime(2024, 1, 1),
    catchup=False,
    max_active_runs=1,
    default_args={
        "owner": "anizai",
        "depends_on_past": False,
        "retries": 1,
        "retry_delay": timedelta(seconds=30),
    },
    tags=["anizai", "ingestion", "opensky", "high-frequency"],
) as dag:
    PythonOperator(
        task_id="opensky_static",
        python_callable=_run_opensky_static,
    )
