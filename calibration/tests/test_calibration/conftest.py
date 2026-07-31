"""
Shared fixtures for the calibration test suite.

Every test in this package runs without a database, without a network, and
without Firestore — Phase 10A has no Firestore surface at all. The market
payloads below are hand-built to the shapes Gamma and CLOB actually return,
including the field-name inconsistencies that `discover.parse_end_date` and
`resolve._extract_prices` exist to absorb.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

# Put `calibration/src` on the path so `import calibration` resolves whether
# pytest is invoked from this directory (where pytest.ini already sets it) or
# from the repository root (where it does not).
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SRC = _PROJECT_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


# A fixed "today" so cohort binning is deterministic regardless of when the
# suite runs. Every fixture below is built relative to it.
TODAY = date(2026, 7, 25)
NOW = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)


def _iso(days_from_today: int) -> str:
    return (TODAY + timedelta(days=days_from_today)).isoformat() + "T00:00:00Z"


def make_market(
    *,
    slug: str = "will-x-happen",
    question: str = "Will X happen before the deadline?",
    condition_id: str = "0xcondition0001",
    days_out: int = 7,
    volume: object = 250_000,
    tags: object = None,
    end_date_key: str = "endDate",
) -> dict:
    """
    Build a Gamma-shaped market payload.

    `end_date_key` and the loose `volume` type are parameterised because the
    real API varies both, and the parsers are written to absorb that variance —
    tests that only ever passed the canonical shape would not exercise it.
    """
    payload: dict = {
        "slug": slug,
        "question": question,
        "conditionId": condition_id,
        "volumeNum": volume,
        "tags": ["Politics"] if tags is None else tags,
        "active": True,
        "closed": False,
    }
    payload[end_date_key] = _iso(days_out)
    return payload


@pytest.fixture
def today() -> date:
    return TODAY


@pytest.fixture
def now() -> datetime:
    return NOW


@pytest.fixture
def gamma_markets() -> list[dict]:
    """
    A realistic discovery batch: 4 qualifying markets across three cohorts,
    plus 5 that must be rejected for five distinct reasons.
    """
    return [
        # --- qualifying ---
        make_market(
            slug="us-govt-shutdown-august", question="Will the US government shut down in August?",
            condition_id="0xaaa1", days_out=7, volume=500_000, tags=["Politics"],
        ),
        make_market(
            slug="fed-cuts-rates-september", question="Will the Fed cut rates in September?",
            condition_id="0xaaa2", days_out=14, volume=1_200_000, tags=["Macroeconomy"],
        ),
        make_market(
            slug="gpt6-released-by-q4", question="Will GPT-6 be released before Q4?",
            condition_id="0xaaa3", days_out=35, volume=90_000, tags=["AI", "Tech"],
        ),
        make_market(
            slug="ceasefire-holds-30-days", question="Will the ceasefire hold for 30 days?",
            condition_id="0xaaa4", days_out=30, volume=310_000, tags=["Geopolitics"],
        ),
        # --- rejected: blocked category (note it ALSO carries an allowed tag) ---
        make_market(
            slug="lakers-win-title", question="Will the Lakers win the title?",
            condition_id="0xbbb1", days_out=14, volume=2_000_000, tags=["Sports", "Politics"],
        ),
        # --- rejected: unrecognised category ---
        make_market(
            slug="weather-thing", question="Will it rain in Lisbon?",
            condition_id="0xbbb2", days_out=7, volume=800_000, tags=["Weather"],
        ),
        # --- rejected: outside every cohort window (the 9-12 day gap) ---
        make_market(
            slug="gap-market", question="Will the gap market resolve?",
            condition_id="0xbbb3", days_out=10, volume=900_000, tags=["Politics"],
        ),
        # --- rejected: below the liquidity floor ---
        make_market(
            slug="thin-market", question="Will the thin market resolve?",
            condition_id="0xbbb4", days_out=7, volume=1_000, tags=["Politics"],
        ),
        # --- rejected: no condition id ---
        {
            "slug": "no-condition", "question": "Will this be skipped?",
            "endDate": _iso(7), "volumeNum": 900_000, "tags": ["Politics"],
        },
    ]


@pytest.fixture
def clob_resolved_yes() -> dict:
    return {
        "condition_id": "0xaaa1",
        "closed": True,
        "tokens": [
            {"outcome": "Yes", "winner": True, "price": "1"},
            {"outcome": "No", "winner": False, "price": "0"},
        ],
        "closedTime": "2026-07-20T09:00:00Z",
    }


@pytest.fixture
def clob_resolved_no() -> dict:
    return {
        "condition_id": "0xaaa2",
        "closed": True,
        "tokens": [
            {"outcome": "Yes", "winner": False},
            {"outcome": "No", "winner": True},
        ],
        "closedTime": "2026-07-21T09:00:00Z",
    }


@pytest.fixture
def clob_disputed() -> dict:
    """Disputed markets carry a winner flag too — ambiguity must still win."""
    return {
        "condition_id": "0xaaa3",
        "closed": True,
        "disputed": True,
        "tokens": [{"outcome": "Yes", "winner": True}],
        "closedTime": "2026-07-19T09:00:00Z",
    }


@pytest.fixture
def clob_still_open() -> dict:
    return {
        "condition_id": "0xaaa4",
        "closed": False,
        "outcomePrices": ["0.62", "0.38"],
    }


@pytest.fixture
def clob_settled_prices_recent() -> dict:
    """Closed, prices at [1, 0], but only an hour ago — inside the settle window."""
    return {
        "condition_id": "0xccc1",
        "closed": True,
        "outcomePrices": ["1.0", "0.0"],
        "closedTime": (NOW - timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
    }


@pytest.fixture
def clob_settled_prices_old() -> dict:
    """Same, but closed three days ago — past the settle window."""
    return {
        "condition_id": "0xccc2",
        "closed": True,
        "outcomePrices": ["0.0", "1.0"],
        "closedTime": (NOW - timedelta(days=3)).isoformat().replace("+00:00", "Z"),
    }
