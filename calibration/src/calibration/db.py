"""
Calibration DB — connection management for the dedicated calibration Postgres.

A deliberate near-duplicate of `utils/db.py`. It is not imported from there,
and that is the point: `utils/db.py` is wired to the pipeline's POSTGRES_*
settings and to the vault database. A calibration module that imported it
would be one refactor away from writing to the vaults. The plan forbids the
import (§2.5 N4) and this module is the cost of that guarantee.

Two differences from the pipeline's version beyond the connection target:

    1. It re-checks the forbidden-database rule before opening the pool.
       config.validate() already checks it at CLI startup, but the pool is the
       last point at which a bad URL can still be stopped, and a duplicated
       four-line guard is cheap insurance against an irreversible mistake.

    2. A smaller pool (min=1, max=5). Calibration is a batch workload run by a
       scheduler a few times an hour, not a streaming job with concurrent
       Flink task slots.

Deviation from the plan (§6 T10A.3): the plan specified psycopg3 async. This
uses psycopg2 sync, matching the rest of the repository (`requirements.txt`
pins psycopg2-binary; every persistence module is sync). Calibration has no
concurrency requirement that would justify introducing a second Postgres
driver and an async test harness into a codebase that has neither. Recorded
here rather than silently diverging.

Public interface:
    get_connection()  -> context manager yielding a psycopg2 connection
    get_cursor()      -> context manager yielding a RealDictCursor
    apply_schema()    -> apply sql/init.sql (idempotent)
    close_pool()      -> teardown for tests and clean shutdown

References:
    - calibration_plan.md §2.5 N4 (no vault access)
    - calibration_plan.md §4 (schema)
    - calibration_plan.md §6 T10A.3
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Generator

import psycopg2
from psycopg2 import pool as pg_pool
from psycopg2.extras import RealDictCursor

from calibration import config

logger = logging.getLogger(__name__)

# ==========================================================
# Connection pool (module-level singleton)
# ==========================================================
_pool: pg_pool.ThreadedConnectionPool | None = None

_POOL_MIN = 1
_POOL_MAX = 5

SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sql", "init.sql")


def _assert_not_pipeline_db(url: str) -> None:
    """
    Refuse to connect to the pipeline's vault database.

    The last line of defence before a connection is opened. See the module
    docstring for why this is duplicated from config.validate().

    Raises:
        RuntimeError: if the URL's database name is on the forbidden list.
    """
    db_name = config.parse_database_name(url)
    if db_name in config.FORBIDDEN_DATABASE_NAMES:
        raise RuntimeError(
            f"Refusing to open a calibration connection to database {db_name!r} — "
            "that is the pipeline's vault database. Calibration requires its own "
            "database (plan P6/B1). Check CALIBRATION_DATABASE_URL."
        )


def _get_pool() -> pg_pool.ThreadedConnectionPool:
    """Return the module-level pool, creating it on first use."""
    global _pool
    if _pool is None:
        url = config.CALIBRATION_DATABASE_URL
        _assert_not_pipeline_db(url)
        logger.info(
            "[calibration.db] Initializing pool (db=%s, min=%d, max=%d)",
            config.parse_database_name(url), _POOL_MIN, _POOL_MAX,
        )
        _pool = pg_pool.ThreadedConnectionPool(
            minconn=_POOL_MIN, maxconn=_POOL_MAX, dsn=url
        )
    return _pool


# ==========================================================
# Public interface
# ==========================================================

@contextmanager
def get_connection() -> Generator["psycopg2.extensions.connection", None, None]:
    """
    Check out a pooled connection; commit on success, roll back on exception,
    and always return the slot to the pool.
    """
    pool = _get_pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


@contextmanager
def get_cursor(
    cursor_factory=RealDictCursor,
) -> Generator["psycopg2.extensions.cursor", None, None]:
    """
    Connection + cursor in one step. Rows come back as dicts by default, which
    keeps repository code from breaking silently when a column is added.
    """
    with get_connection() as conn:
        with conn.cursor(cursor_factory=cursor_factory) as cur:
            yield cur


def apply_schema() -> None:
    """
    Apply `sql/init.sql` to the configured database.

    Idempotent — every statement in that file is IF NOT EXISTS — so this is
    safe to run against an existing database. Executed as a single transaction:
    a partially-applied schema is worse than no schema.

    Raises:
        FileNotFoundError: if init.sql is missing from the package.
        psycopg2.Error: on any DDL failure (the transaction rolls back).
    """
    with open(SCHEMA_PATH, "r", encoding="utf-8") as fh:
        ddl = fh.read()

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(ddl)
    logger.info("[calibration.db] Schema applied from %s", SCHEMA_PATH)


def table_names() -> list[str]:
    """
    Return the calibration tables that currently exist, sorted.

    Used by the CLI's `init-db` to report what it created and by tests to
    assert the schema applied. Scoped to the `calibration_` prefix so it
    reports nothing about any other table that happens to share the database.
    """
    sql = """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_name LIKE 'calibration\\_%'
        ORDER BY table_name;
    """
    with get_cursor() as cur:
        cur.execute(sql)
        return [row["table_name"] for row in cur.fetchall()]


def close_pool() -> None:
    """Close every pooled connection. Call in test teardown and on shutdown."""
    global _pool
    if _pool is not None:
        _pool.closeall()
        _pool = None
        logger.info("[calibration.db] Pool closed.")
