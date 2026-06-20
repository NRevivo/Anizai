"""
Shared pytest fixtures for the Anizai test suite.

Scope hierarchy:
    session  — DB availability check (runs once per pytest session)
    module   — test-run isolation prefix (unique per module invocation)
    function — per-test cleanup of rows written to the DB

Isolation strategy for persistence tests (Gate 3):
    Every test record is written with a canonical_event_id prefixed by
    a session-scoped UUID (e.g., "test_<uuid>_<test_name>"). A function-
    scoped autouse fixture deletes all rows matching that prefix after
    each test, keeping the shared DB clean without needing transactions
    or test-specific schemas.

References:
    - Section 9.3: Triple-Gate Test Matrix
    - Section 9.5: Reliability & Schema Enforcement
"""

import uuid
from pathlib import Path

import pytest
import psycopg2

from utils.db import get_cursor, close_pool


# ==========================================================
# Session-scoped: verify DB is reachable before running any
# persistence tests. Fails fast with a clear message rather
# than letting individual tests crash with connection errors.
# ==========================================================

@pytest.fixture(scope="session", autouse=False)
def db_available():
    """
    Confirm the PostgreSQL container is reachable.

    Mark any test class or module that requires a live DB with:
        @pytest.mark.usefixtures("db_available")

    Skips (not fails) the entire session if the DB is unreachable,
    so Gate 1/2 tests (which need no DB) are never blocked.
    """
    try:
        with get_cursor() as cur:
            cur.execute("SELECT 1;")
    except Exception as exc:
        pytest.skip(
            f"PostgreSQL not reachable — skipping persistence tests. "
            f"Start the stack with: docker compose -f infrastructure/docker-compose.yml up -d postgres\n"
            f"Error: {exc}"
        )


# Path to the Sprint 19 smoke-check vault seed, relative to this conftest.
_VAULT_SEED_SQL = Path(__file__).parent / "fixtures" / "sprint19_vault_seed.sql"


@pytest.fixture(scope="session", autouse=False)
def vault_seed_loaded(db_available):
    """
    Ensure the Sprint 19 smoke seed (tests/fixtures/sprint19_vault_seed.sql)
    is present before running seed-dependent smoke tests.

    Why this exists (Sprint 23.5 Track 4, Disposition D):
        `db_available` proves only that Postgres is *reachable*. A
        reachable-but-unseeded DB makes the vault-wrapper smoke tests fail
        with empty result sets — a red that has nothing to do with the code
        under test. This fixture makes those tests self-contained and
        deterministic: it loads the seed idempotently (the fixture uses
        ON CONFLICT DO NOTHING for the static rows and DELETE-before-INSERT
        for the two NOW()-relative momentum rows), so the suite is green from
        a freshly `docker compose up`-ed Postgres with no manual psql step,
        and the NOW()-relative rows are refreshed into the query windows on
        every run.

    Mark seed-dependent tests with:
        @pytest.mark.usefixtures("vault_seed_loaded")

    If the seed cannot be loaded, the requesting tests are *skipped* (not
    failed) — the suite stays deterministic without live, seeded infra.
    """
    try:
        sql = _VAULT_SEED_SQL.read_text()
        with get_cursor() as cur:
            cur.execute(sql)
    except Exception as exc:
        pytest.skip(
            f"Could not load vault seed {_VAULT_SEED_SQL.name} — skipping "
            f"seed-dependent smoke tests.\nError: {exc}"
        )


@pytest.fixture(scope="session", autouse=False)
def kafka_available():
    """
    Confirm the Kafka broker is reachable (Sprint 23 T23.9).

    Mark any test class or module that requires a live Kafka broker with:
        @pytest.mark.usefixtures("kafka_available")

    Skips (not fails) the session if Kafka is unreachable, mirroring the
    db_available pattern so unit-level Gate 1/2 tests stay independent
    of broker presence.

    A short request_timeout_ms is used here so the skip is fast — the
    full producer default is 30s and would block CI runs without Kafka
    for the whole interval.
    """
    from utils.kafka_utils import make_producer
    try:
        # Short timeout so we don't hang the session if Kafka is down.
        producer = make_producer(request_timeout_ms=3_000)
        producer.close(timeout=2.0)
    except Exception as exc:
        pytest.skip(
            f"Kafka not reachable — skipping Kafka integration tests. "
            f"Start the stack with: docker compose -f infrastructure/docker-compose.yml up -d kafka\n"
            f"Error: {exc}"
        )


# ==========================================================
# Session-scoped: unique prefix for all test records in
# this pytest run. Prevents collisions when tests are run
# in parallel or the DB is shared between developers.
# ==========================================================

@pytest.fixture(scope="session")
def test_run_id() -> str:
    """Short UUID prefix used to namespace all test canonical_event_ids."""
    return str(uuid.uuid4())[:8]


# ==========================================================
# Function-scoped: delete every row inserted by the current
# test, identified by the test_run_id prefix in the
# canonical_event_id column.
# ==========================================================

@pytest.fixture(autouse=False)
def cleanup_social_vectors(test_run_id):
    """
    Delete test rows from social_vectors after each test function.

    Usage: request this fixture explicitly in Gate 3 test classes, or
    mark the class with @pytest.mark.usefixtures("cleanup_social_vectors").

    Why autouse=False: Gate 1/2 tests never touch the DB and should not
    pay the overhead of a teardown SQL call on every test function.
    """
    yield  # test runs here
    try:
        with get_cursor() as cur:
            cur.execute(
                "DELETE FROM social_vectors WHERE canonical_event_id LIKE %s;",
                (f"test_{test_run_id}%",),
            )
    except Exception as exc:
        # Non-fatal: log but don't fail the test on cleanup errors
        print(f"\n[conftest] Warning: social_vectors cleanup failed: {exc}")


@pytest.fixture(autouse=False)
def cleanup_knowledge_vault(test_run_id):
    """Delete test rows from knowledge_vault after each test function."""
    yield
    try:
        with get_cursor() as cur:
            cur.execute(
                "DELETE FROM knowledge_vault WHERE canonical_event_id LIKE %s;",
                (f"test_{test_run_id}%",),
            )
    except Exception as exc:
        print(f"\n[conftest] Warning: knowledge_vault cleanup failed: {exc}")


@pytest.fixture(autouse=False)
def cleanup_knowledge_vectors(test_run_id):
    """Delete test rows from knowledge_vectors after each test function."""
    yield
    try:
        with get_cursor() as cur:
            cur.execute(
                "DELETE FROM knowledge_vectors WHERE canonical_event_id LIKE %s;",
                (f"test_{test_run_id}%",),
            )
    except Exception as exc:
        print(f"\n[conftest] Warning: knowledge_vectors cleanup failed: {exc}")


@pytest.fixture(autouse=False)
def cleanup_social_vault(test_run_id):
    """Delete test rows from social_vault after each test function."""
    yield
    try:
        with get_cursor() as cur:
            cur.execute(
                "DELETE FROM social_vault WHERE canonical_event_id LIKE %s;",
                (f"test_{test_run_id}%",),
            )
    except Exception as exc:
        print(f"\n[conftest] Warning: social_vault cleanup failed: {exc}")


@pytest.fixture(autouse=False)
def cleanup_momentum_vault(test_run_id):
    """Delete test rows from momentum_vault after each test function."""
    yield
    try:
        with get_cursor() as cur:
            cur.execute(
                "DELETE FROM momentum_vault WHERE canonical_event_id LIKE %s;",
                (f"test_{test_run_id}%",),
            )
    except Exception as exc:
        print(f"\n[conftest] Warning: momentum_vault cleanup failed: {exc}")


@pytest.fixture(autouse=False)
def cleanup_mapping_dict(test_run_id):
    """Delete test rows from mapping_dict after each test function."""
    yield
    try:
        with get_cursor() as cur:
            cur.execute(
                "DELETE FROM mapping_dict WHERE canonical_event_id LIKE %s;",
                (f"test_{test_run_id}%",),
            )
    except Exception as exc:
        print(f"\n[conftest] Warning: mapping_dict cleanup failed: {exc}")


@pytest.fixture(autouse=False)
def cleanup_reactive_triggers_log(test_run_id):
    """
    Delete test rows from reactive_triggers_log after each test function
    (Sprint 23). Matches by session_id prefix since this table has no
    canonical_event_id column — its grain is the agent session.
    """
    yield
    try:
        with get_cursor() as cur:
            cur.execute(
                "DELETE FROM reactive_triggers_log WHERE session_id LIKE %s;",
                (f"test_{test_run_id}%",),
            )
    except Exception as exc:
        print(f"\n[conftest] Warning: reactive_triggers_log cleanup failed: {exc}")


# ==========================================================
# Session teardown: close the connection pool cleanly so
# pytest exits without 'connection already closed' warnings.
# ==========================================================

@pytest.fixture(scope="session", autouse=True)
def close_db_pool():
    """Close the psycopg2 connection pool at the end of the test session."""
    yield
    close_pool()
