"""
Gate 1 — configuration validation.

The test that matters here is the vault-database guard. A mistyped
CALIBRATION_DATABASE_URL pointed at the pipeline's `anizai` database would run
calibration DDL against the vaults — the one configuration mistake in this
system with irreversible consequences.
"""

from __future__ import annotations

import importlib
import os

import pytest

from calibration import config


@pytest.fixture
def fresh_config():
    """
    Reload the config module so module-level env reads take effect.

    Returns a callable that applies env overrides and reimports.

    Deliberately does NOT use pytest's `monkeypatch` for the environment.
    `monkeypatch` tears down *after* this fixture, so the final reload here
    would run while the test's environment was still in place and leave
    `calibration.config` holding a test connection URL. Every later test that
    touches the database then connects somewhere else — which is exactly the
    failure this suite hit on 2026-07-25: 27 integration tests erroring, with
    nothing wrong in the code under test.

    So the environment is saved and restored by hand, in the right order, and
    the connection pool is dropped because it caches whatever URL was current
    when it was built.
    """
    saved_env = dict(os.environ)

    def _load(**env):
        for key, value in env.items():
            os.environ[key] = str(value)
        return importlib.reload(config)

    yield _load

    for key in [k for k in os.environ if k not in saved_env]:
        del os.environ[key]
    os.environ.update(saved_env)
    importlib.reload(config)

    from calibration import db

    db.close_pool()


# ==========================================================
# Database-name parsing and the vault guard
# ==========================================================

@pytest.mark.parametrize(
    "url,expected",
    [
        ("postgresql://u:p@localhost:5432/anizai_calibration", "anizai_calibration"),
        ("postgresql://u:p@host/mydb", "mydb"),
        ("postgresql://u:p@host/mydb?sslmode=require", "mydb"),
        # No database component at all — must fail closed, not return the host.
        ("postgresql://u:p@localhost:5432", ""),
        ("postgresql://u:p@localhost:5432/", ""),
        ("postgresql://localhost", ""),
    ],
)
def test_parse_database_name(url, expected):
    assert config.parse_database_name(url) == expected


def test_validate_rejects_the_pipeline_vault_database(fresh_config):
    """
    The single most dangerous misconfiguration in the system. It must be a
    hard error, not a warning, and it must fire before anything connects.
    """
    cfg = fresh_config(CALIBRATION_DATABASE_URL="postgresql://u:p@localhost:5432/anizai")
    with pytest.raises(ValueError, match="vault database"):
        cfg.validate()


def test_validate_rejects_a_url_with_no_database_name(fresh_config):
    cfg = fresh_config(CALIBRATION_DATABASE_URL="postgresql://u:p@localhost:5432")
    with pytest.raises(ValueError, match="no database name"):
        cfg.validate()


def test_validate_accepts_a_dedicated_calibration_database(fresh_config):
    cfg = fresh_config(
        CALIBRATION_DATABASE_URL="postgresql://u:p@localhost:5432/anizai_calibration"
    )
    cfg.validate()   # must not raise


# ==========================================================
# Coherence checks
# ==========================================================

def test_validate_rejects_targets_that_exceed_the_ceiling(fresh_config):
    """
    Targets summing past CALIBRATION_MAX_OPEN_QUESTIONS make discovery
    unsatisfiable by construction — it would report a shortfall forever.
    """
    cfg = fresh_config(
        CALIBRATION_DATABASE_URL="postgresql://u:p@h:5432/anizai_calibration",
        CALIBRATION_TARGET_COUNT_7D=20,
        CALIBRATION_TARGET_COUNT_14D=20,
        CALIBRATION_TARGET_COUNT_30_45D=20,
        CALIBRATION_MAX_OPEN_QUESTIONS=30,
    )
    with pytest.raises(ValueError, match="could never reach its targets"):
        cfg.validate()


def test_validate_rejects_negative_values(fresh_config):
    cfg = fresh_config(
        CALIBRATION_DATABASE_URL="postgresql://u:p@h:5432/anizai_calibration",
        CALIBRATION_TARGET_COUNT_7D=-1,
    )
    with pytest.raises(ValueError, match="must be >= 0"):
        cfg.validate()


# ==========================================================
# Cohort lookups
# ==========================================================

def test_target_count_for_each_cohort():
    assert config.target_count_for("7d") == config.CALIBRATION_TARGET_COUNT_7D
    assert config.target_count_for("14d") == config.CALIBRATION_TARGET_COUNT_14D
    assert config.target_count_for("30-45d") == config.CALIBRATION_TARGET_COUNT_30_45D


def test_unknown_cohort_targets_zero():
    """An unknown cohort asks for nothing rather than raising mid-discovery."""
    assert config.target_count_for("90d") == 0


def test_long_horizon_cohort_has_the_lower_liquidity_floor():
    assert config.liquidity_floor_for("30-45d") == config.CALIBRATION_LIQUIDITY_MIN_30_45D_USD
    assert config.liquidity_floor_for("7d") == config.CALIBRATION_LIQUIDITY_MIN_7_14D_USD
    assert config.liquidity_floor_for("14d") == config.CALIBRATION_LIQUIDITY_MIN_7_14D_USD
    assert config.liquidity_floor_for("30-45d") < config.liquidity_floor_for("7d")


# ==========================================================
# Kill switch
# ==========================================================

@pytest.mark.parametrize("raw,expected", [("false", False), ("0", False), ("no", False)])
def test_kill_switch_can_be_thrown_by_env(fresh_config, raw, expected):
    cfg = fresh_config(CALIBRATION_ENABLED=raw)
    assert cfg.CALIBRATION_ENABLED is expected


@pytest.mark.parametrize("raw", ["true", "1", "yes"])
def test_kill_switch_truthy_values(fresh_config, raw):
    assert fresh_config(CALIBRATION_ENABLED=raw).CALIBRATION_ENABLED is True


def test_calibration_is_enabled_by_default():
    """Default-on. The switch is for stopping, not for opting in."""
    assert config.CALIBRATION_ENABLED is True
