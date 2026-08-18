"""
Calibration Config — every environment variable this package reads.

Deliberately does NOT import `config.settings` (the pipeline's settings
module). That module loads the pipeline's .env, creates a DATA_DIR on import,
and exposes POSTGRES_* constants pointing at the vault database — none of
which calibration wants, and the last of which it must never accidentally
use. Calibration loads its own environment and owns its own constants.

The plan (§6) permitted `config.settings` as the one allowed cross-package
import. We take neither: a duplicated four-line `load_dotenv` is cheaper than
a dependency on a module whose import has side effects.

.env resolution keeps the pipeline's two-location shape so operators do not
have to learn a second one, but both locations are inside `calibration/`:
`calibration/.env`, falling back to `calibration/infrastructure/.env`. Reading
the pipeline's .env would be the one cross-boundary read in a package whose
whole premise is that it has none.

Configuration groups:
    1. Database        — the separate calibration Postgres (plan P6/B1)
    2. Polymarket      — public API bases; no credentials needed
    3. Discovery       — cohort target counts and liquidity floors (plan C1)
    4. Guardrails      — the G8 ceilings and the kill switch

Phase 10A note: the guardrail values in group 4 are DEFINED here but only
partially ENFORCED. `CALIBRATION_MAX_OPEN_QUESTIONS` is enforced by the
discovery service in this sprint. `CALIBRATION_ENABLED`,
`CALIBRATION_MAX_FORECASTS_PER_RUN` and `CALIBRATION_DISPATCH_CONCURRENCY`
govern dispatch, which does not exist until Phase 10B — they are declared now
so the config surface is complete and reviewable in one place, rather than
growing silently sprint by sprint.

References:
    - calibration_plan.md §3 C1 (auto-selection criteria)
    - calibration_plan.md §3 G8 (cost guardrails and kill switch)
    - calibration_plan.md §6 T10A.13 (Phase 10A env vars)
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

# ==========================================================
# 1. .env loading
# ==========================================================
# BASE_DIR points at calibration/ — this file lives at
# calibration/src/calibration/config.py, so three dirnames up.
#
# It was two while the package sat at data-pipeline/calibration/. The move to a
# top-level src/ layout added a directory level and left the count behind, so
# BASE_DIR resolved to calibration/src/ and both .env lookups pointed at a
# directory that will never hold one.
#
# Nothing failed loudly, which is why it survived the move: every setting below
# has a working default, so a stale BASE_DIR degrades into "the .env was
# silently ignored" rather than an error. Anyone who had put a
# CALIBRATION_DATABASE_URL in that file would have been quietly running against
# the local default instead.
BASE_DIR = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

_dotenv_path = os.path.join(BASE_DIR, ".env")
_dotenv_infra_path = os.path.join(BASE_DIR, "infrastructure", ".env")
if os.path.exists(_dotenv_path):
    load_dotenv(_dotenv_path)
elif os.path.exists(_dotenv_infra_path):
    load_dotenv(_dotenv_infra_path)
# No warning when absent: every value below has a working local default, and
# Phase 10A is explicitly a local-only sprint. Phase 10D reads its config from
# Secret Manager, not from a .env at all.


def _int_env(name: str, default: int) -> int:
    """Read an int env var, falling back to `default` when unset or blank."""
    raw = os.getenv(name, "").strip()
    return int(raw) if raw else default


def _bool_env(name: str, default: bool) -> bool:
    """Read a bool env var. Truthy set matches the pipeline's convention."""
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes"}


# ==========================================================
# 2. Database (plan P6 / B1)
# ==========================================================
# A dedicated Postgres, never the pipeline's. The default points at a local
# database named `anizai_calibration` so a developer can run Phase 10A against
# a plain local Postgres with no configuration at all.
#
# The name is checked, not just documented: a URL whose database component is
# the pipeline's `anizai` is rejected outright by `validate()` below. A typo
# there would run calibration DDL against the vaults.
CALIBRATION_DATABASE_URL = os.getenv(
    "CALIBRATION_DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/anizai_calibration",
)

# Databases calibration must never connect to, whatever the URL says.
# `anizai` is the pipeline vault database (config/settings.py POSTGRES_DB).
FORBIDDEN_DATABASE_NAMES: frozenset[str] = frozenset({"anizai"})


# ==========================================================
# 3. Polymarket (public APIs — no credentials)
# ==========================================================
# Mirrored from ingestion/polymarket_producer.py rather than imported: that
# module pulls in asyncio, websockets, and the Kafka producer at import time.
# Copy-not-import is the plan's explicit instruction (§6).
POLYMARKET_GAMMA_API = os.getenv(
    "POLYMARKET_GAMMA_API", "https://gamma-api.polymarket.com"
)
POLYMARKET_CLOB_API = os.getenv(
    "POLYMARKET_CLOB_API", "https://clob.polymarket.com"
)

# Seconds before an HTTP call to either API is abandoned.
POLYMARKET_HTTP_TIMEOUT_SEC = _int_env("POLYMARKET_HTTP_TIMEOUT_SEC", 15)


# ==========================================================
# 4. Discovery — cohort targets and liquidity floors (plan C1)
# ==========================================================
CALIBRATION_TARGET_COUNT_7D = _int_env("CALIBRATION_TARGET_COUNT_7D", 10)
CALIBRATION_TARGET_COUNT_14D = _int_env("CALIBRATION_TARGET_COUNT_14D", 10)
CALIBRATION_TARGET_COUNT_30_45D = _int_env("CALIBRATION_TARGET_COUNT_30_45D", 8)

# Liquidity floors. The long-horizon cohort gets a lower floor because there
# are simply fewer high-volume markets 30-45 days out — holding it to the same
# $50k bar would starve the cohort (plan C1).
CALIBRATION_LIQUIDITY_MIN_7_14D_USD = _int_env(
    "CALIBRATION_LIQUIDITY_MIN_7_14D_USD", 50_000
)
CALIBRATION_LIQUIDITY_MIN_30_45D_USD = _int_env(
    "CALIBRATION_LIQUIDITY_MIN_30_45D_USD", 25_000
)


# ==========================================================
# 5. Guardrails and kill switch (plan G8)
# ==========================================================
# The master switch. Phase 10D checks this first in every /tasks/* handler,
# before any I/O. In Phase 10A it gates the CLI's write commands, so the
# switch is exercised (and therefore trustworthy) from the first sprint
# rather than being introduced untested at the moment it starts to matter.
CALIBRATION_ENABLED = _bool_env("CALIBRATION_ENABLED", True)

# Hard ceiling on simultaneously-open questions. Discovery refuses to exceed
# it; this is the main brake on runaway forecast cost.
CALIBRATION_MAX_OPEN_QUESTIONS = _int_env("CALIBRATION_MAX_OPEN_QUESTIONS", 30)

# How many forecasts one run may dispatch.
#
# Default 3, not 30. The agent's owners are presenting in a month and share an
# OpenAI daily request quota with the rest of the product; a default that
# quietly sends 30 forecasts is a default that eventually sends 30 forecasts
# by accident. A sanity check needs 2-3. Anything larger is a decision, and
# decisions should be typed out.
CALIBRATION_MAX_FORECASTS_PER_RUN = _int_env("CALIBRATION_MAX_FORECASTS_PER_RUN", 3)

# Rolling 24-hour ceiling across ALL runs. This is the one that actually
# protects the shared quota.
#
# The per-run cap does not: a loop that dispatches 3, fails to harvest, and
# retries every five minutes respects the per-run cap perfectly while emitting
# 864 forecasts a day. Counting dispatches in a window is the only guard that
# survives a stuck caller, because it does not depend on the caller behaving.
CALIBRATION_MAX_FORECASTS_PER_DAY = _int_env("CALIBRATION_MAX_FORECASTS_PER_DAY", 30)

CALIBRATION_DISPATCH_CONCURRENCY = _int_env("CALIBRATION_DISPATCH_CONCURRENCY", 3)


# ==========================================================
# 6. Harvest (Phase 10B)
# ==========================================================
# How long a forecast may sit in `dispatched` before the harvester gives up
# and marks it `timed_out`. Generous relative to the agent's ~30s p95 because
# the cost of waiting is one extra polling cycle, whereas the cost of timing
# out a forecast that was merely slow is a lost data point plus a false entry
# in the failure counters.
CALIBRATION_DISPATCH_TIMEOUT_MIN = _int_env("CALIBRATION_DISPATCH_TIMEOUT_MIN", 120)

# Firestore project. `firestore_client.init_app` logs loudly at WARNING when
# FIRESTORE_EMULATOR_HOST is unset, so a run against live Firestore is never
# silent.
FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID", "anizai-ai")


# ==========================================================
# 7. Cloud automation (Phase 10D)
# ==========================================================
# A second switch on top of CALIBRATION_ENABLED, guarding only the endpoint
# that spends money.
#
# Default FALSE, and it must stay false while the agent is held at
# `replicas: 0` and brought up by hand per run. An unattended weekly dispatch
# into an agent nobody switched on queues 28 forecasts that are never claimed,
# ages every one into `timed_out` after two hours, and repeats — a database
# steadily filling with failures that never happened, and no measurements at
# all.
#
# The other four tasks are cheap, idempotent, and safe to schedule right away:
# they cost HTTP calls rather than tokens, and do nothing when there is
# nothing to do.
CALIBRATION_DISPATCH_TASK_ENABLED = _bool_env(
    "CALIBRATION_DISPATCH_TASK_ENABLED", False
)

# The Cloud Run service URL, used as the OIDC audience for /tasks/* tokens.
# Empty means "do not check the audience" — tolerable locally, required in
# production: without it any valid Google token from an allow-listed principal
# is accepted regardless of which service it was minted for.
CALIBRATION_OIDC_AUDIENCE = os.getenv("CALIBRATION_OIDC_AUDIENCE", "")

# Service accounts permitted to call /tasks/*, comma-separated.
# Empty denies everyone — the same fail-closed rule as the operator allowlist.
CALIBRATION_SCHEDULER_SERVICE_ACCOUNTS = os.getenv(
    "CALIBRATION_SCHEDULER_SERVICE_ACCOUNTS", ""
)


# ==========================================================
# 8. Validation
# ==========================================================

def parse_database_name(url: str) -> str:
    """
    Extract the database name from a libpq/psycopg connection URL.

    Returns an empty string when the URL carries no database component.

    The scheme separator has to be stripped before looking for the path
    separator: naively taking the segment after the last '/' turns
    "postgresql://user:pass@localhost:5432" — which names no database at all —
    into "localhost:5432", a plausible-looking string that then passes the
    forbidden-name check. The point of this function is to feed a safety
    guard, so it must fail closed on a malformed URL rather than invent a
    database name.
    """
    _, separator, rest = url.partition("://")
    if not separator:
        rest = url
    if "/" not in rest:
        return ""
    path = rest.split("/", 1)[1]
    return path.split("?", 1)[0].strip()


def validate() -> None:
    """
    Fail fast on configuration that would be dangerous or useless at runtime.

    Called by the CLI before any command runs. Two classes of check:

    1. Safety — the connection URL must not point at the pipeline's vault
       database. This is the single configuration mistake with irreversible
       consequences (calibration DDL applied to the vaults), so it is a hard
       error rather than a warning, and it is checked before anything opens a
       connection.

    2. Coherence — target counts and liquidity floors must be non-negative,
       and the cohort targets must not already exceed the open-question
       ceiling, which would make discovery unsatisfiable by construction.

    Raises:
        ValueError: with a message naming the offending setting.
    """
    db_name = parse_database_name(CALIBRATION_DATABASE_URL)
    if db_name in FORBIDDEN_DATABASE_NAMES:
        raise ValueError(
            f"CALIBRATION_DATABASE_URL points at database {db_name!r}, which is "
            "the pipeline's vault database. Calibration requires its own "
            "database (plan P6/B1). Refusing to continue."
        )
    if not db_name:
        raise ValueError(
            "CALIBRATION_DATABASE_URL has no database name — expected a URL of "
            "the form postgresql://user:pass@host:port/dbname"
        )

    targets = {
        "CALIBRATION_TARGET_COUNT_7D": CALIBRATION_TARGET_COUNT_7D,
        "CALIBRATION_TARGET_COUNT_14D": CALIBRATION_TARGET_COUNT_14D,
        "CALIBRATION_TARGET_COUNT_30_45D": CALIBRATION_TARGET_COUNT_30_45D,
        "CALIBRATION_LIQUIDITY_MIN_7_14D_USD": CALIBRATION_LIQUIDITY_MIN_7_14D_USD,
        "CALIBRATION_LIQUIDITY_MIN_30_45D_USD": CALIBRATION_LIQUIDITY_MIN_30_45D_USD,
        "CALIBRATION_MAX_OPEN_QUESTIONS": CALIBRATION_MAX_OPEN_QUESTIONS,
        "CALIBRATION_MAX_FORECASTS_PER_RUN": CALIBRATION_MAX_FORECASTS_PER_RUN,
        "CALIBRATION_DISPATCH_CONCURRENCY": CALIBRATION_DISPATCH_CONCURRENCY,
    }
    for name, value in targets.items():
        if value < 0:
            raise ValueError(f"{name} must be >= 0, got {value}")

    total_target = (
        CALIBRATION_TARGET_COUNT_7D
        + CALIBRATION_TARGET_COUNT_14D
        + CALIBRATION_TARGET_COUNT_30_45D
    )
    if total_target > CALIBRATION_MAX_OPEN_QUESTIONS:
        raise ValueError(
            f"Cohort targets sum to {total_target} but "
            f"CALIBRATION_MAX_OPEN_QUESTIONS is {CALIBRATION_MAX_OPEN_QUESTIONS}. "
            "Discovery could never reach its targets. Raise the ceiling or "
            "lower the targets."
        )


def target_count_for(cohort: str) -> int:
    """Target number of open questions for one cohort. Unknown cohort -> 0."""
    return {
        "7d": CALIBRATION_TARGET_COUNT_7D,
        "14d": CALIBRATION_TARGET_COUNT_14D,
        "30-45d": CALIBRATION_TARGET_COUNT_30_45D,
    }.get(cohort, 0)


def liquidity_floor_for(cohort: str) -> int:
    """Minimum cumulative volume (USD) for a market to qualify in `cohort`."""
    if cohort == "30-45d":
        return CALIBRATION_LIQUIDITY_MIN_30_45D_USD
    return CALIBRATION_LIQUIDITY_MIN_7_14D_USD
