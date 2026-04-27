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


# ==========================================================
# Session teardown: close the connection pool cleanly so
# pytest exits without 'connection already closed' warnings.
# ==========================================================

@pytest.fixture(scope="session", autouse=True)
def close_db_pool():
    """Close the psycopg2 connection pool at the end of the test session."""
    yield
    close_pool()
