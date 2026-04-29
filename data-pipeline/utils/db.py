"""
Database Connections — Centralized PostgreSQL connection management.

Why centralized here: all DB logic must live in dedicated modules only
(Section 3.3 Service Isolation). Producers and Flink jobs must never
hold their own connection strings — they import from here (Section 3.2).

Single PostgreSQL engine hosts all four vaults via extensions (Section 5):
  - Core JSONB store: Knowledge Vault + Social Vault (Section 5.1)
  - pgvector extension: Vector Vault with HNSW indexing (Section 5.2)
  - TimescaleDB extension: Momentum Vault hypertables (Section 5.3)
  - Relational store: Mapping Dictionary (Section 5.4)

Connection strategy: psycopg2 connection pool via a module-level singleton.
Why a pool and not a single connection: the Flink Gold Job and persistence
layer will write concurrently from multiple task slots. A pool (min=2, max=10)
handles bursts without exhausting DB connections or spawning new ones per write.

References:
    - Section 3.2: DRY — database connections in utils/db.py
    - Section 3.3: Service Isolation — only persistence modules write to DB
    - Section 5: Multi-Vault Strategy (unified PostgreSQL engine)
    - Section 9.1: Environment parity (.env-driven credentials)
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Generator

import psycopg2
from psycopg2 import pool as pg_pool
from psycopg2.extras import RealDictCursor

from config.settings import (
    POSTGRES_DB,
    POSTGRES_HOST,
    POSTGRES_PASSWORD,
    POSTGRES_PORT,
    POSTGRES_USER,
)

from utils.logging_config import setup_logging

logger = logging.getLogger(__name__)
setup_logging()

# ==========================================================
# Connection Pool (module-level singleton)
# ==========================================================
# Initialized lazily on first call to get_connection().
# Min connections kept alive = 2 (Flink Silver + Gold jobs).
# Max connections = 10 (headroom for concurrent persistence calls).
_pool: pg_pool.ThreadedConnectionPool | None = None

_POOL_MIN = 2
_POOL_MAX = 10


def _get_pool() -> pg_pool.ThreadedConnectionPool:
    """
    Return the module-level connection pool, creating it on first call.

    ThreadedConnectionPool is used because Flink TaskManagers run in
    threads — SimpleConnectionPool is not thread-safe.
    """
    global _pool
    if _pool is None:
        logger.info(
            "Initializing PostgreSQL connection pool "
            "(host=%s, db=%s, min=%d, max=%d)",
            POSTGRES_HOST, POSTGRES_DB, _POOL_MIN, _POOL_MAX,
        )
        _pool = pg_pool.ThreadedConnectionPool(
            minconn=_POOL_MIN,
            maxconn=_POOL_MAX,
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD,
            dbname=POSTGRES_DB,
        )
    return _pool


# ==========================================================
# Public Interface
# ==========================================================

@contextmanager
def get_connection() -> Generator[psycopg2.extensions.connection, None, None]:
    """
    Context manager that checks out a connection from the pool and
    returns it automatically on exit — even on exception.

    Usage:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT 1")

    Why context manager: prevents connection leaks in Flink jobs that
    process millions of messages per day. The pool slot is always
    released regardless of what happens inside the block.
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
) -> Generator[psycopg2.extensions.cursor, None, None]:
    """
    Convenience context manager: connection + cursor in one step.

    Returns rows as dicts (RealDictCursor) by default — avoids positional
    column indexing bugs when schemas evolve.

    Usage:
        with get_cursor() as cur:
            cur.execute("SELECT * FROM knowledge_vault WHERE doc_id = %s", (doc_id,))
            row = cur.fetchone()
    """
    with get_connection() as conn:
        with conn.cursor(cursor_factory=cursor_factory) as cur:
            yield cur


# ==========================================================
# Extension Verification (run once at startup)
# ==========================================================

def verify_extensions() -> None:
    """
    Confirm that pgvector and TimescaleDB extensions are installed.

    Called once at application startup by persistence modules to fail
    fast if the database was initialized without the required extensions
    (Section 5.2 pgvector, Section 5.3 TimescaleDB).

    Raises:
        RuntimeError: If a required extension is missing.
    """
    required = {"vector", "timescaledb"}
    with get_cursor() as cur:
        cur.execute("SELECT extname FROM pg_extension;")
        installed = {row["extname"] for row in cur.fetchall()}

    missing = required - installed
    if missing:
        raise RuntimeError(
            f"Required PostgreSQL extensions not installed: {missing}. "
            "Run infrastructure/sql/init.sql to initialize the database."
        )
    logger.info("PostgreSQL extensions verified: %s", required)


# ==========================================================
# Teardown (for clean shutdown in tests / Docker stop)
# ==========================================================

def close_pool() -> None:
    """
    Close all connections in the pool.

    Call this in application shutdown hooks or test teardown to prevent
    'connection already closed' warnings from the PostgreSQL server.
    """
    global _pool
    if _pool is not None:
        _pool.closeall()
        _pool = None
        logger.info("PostgreSQL connection pool closed.")
