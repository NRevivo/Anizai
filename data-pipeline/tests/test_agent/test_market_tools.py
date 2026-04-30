"""
Gate 1 unit tests for agent/tools/market_tools.py (Sprint 19 T19.1).

Pinned behaviour:
    D-2: wrapper has no defaults for `hours` / `days` so a caller cannot
         silently inherit the persistence defaults (24h / 7d) when the spec
         calls for 720h / 14d.
"""

from unittest.mock import patch

from agent.tools import market_tools


# ==========================================================
# fetch_latest
# ==========================================================

def test_fetch_latest_delegates():
    expected = {"metric_id": "m1", "current_value": 0.42}
    with patch.object(
        market_tools.momentum_vault,
        "fetch_latest",
        return_value=expected,
    ) as mock_fetch:
        result = market_tools.fetch_latest("polymarket", "us-election-2026")

    mock_fetch.assert_called_once_with("polymarket", "us-election-2026")
    assert result is expected


def test_fetch_latest_returns_none_on_miss():
    with patch.object(
        market_tools.momentum_vault,
        "fetch_latest",
        return_value=None,
    ):
        assert market_tools.fetch_latest("polymarket", "missing-slug") is None


# ==========================================================
# fetch_time_series — D-2 explicit hours
# ==========================================================

def test_fetch_time_series_passes_spec_hours():
    """
    Spec §8.4.3 step 1b calls fetch_time_series with hours=720 (30 days).
    Wrapper forwards explicitly so persistence default (24h) cannot apply.
    """
    with patch.object(
        market_tools.momentum_vault,
        "fetch_time_series",
        return_value=[],
    ) as mock_fetch:
        market_tools.fetch_time_series("polymarket", "slug-x", hours=720)

    mock_fetch.assert_called_once_with(
        source_name="polymarket",
        external_reference_id="slug-x",
        hours=720,
    )


def test_fetch_time_series_returns_empty_when_no_rows():
    with patch.object(
        market_tools.momentum_vault,
        "fetch_time_series",
        return_value=[],
    ):
        rows = market_tools.fetch_time_series("polymarket", "slug-x", hours=24)
    assert rows == []


# ==========================================================
# fetch_fred_anomalies — D-2 explicit days
# ==========================================================

def test_fetch_fred_anomalies_passes_spec_days():
    """Spec §8.4.3 step 3 calls fetch_fred_anomalies with days=14."""
    with patch.object(
        market_tools.momentum_vault,
        "fetch_fred_anomalies",
        return_value=[],
    ) as mock_fetch:
        market_tools.fetch_fred_anomalies(days=14)

    mock_fetch.assert_called_once_with(days=14)


def test_fetch_fred_anomalies_returns_empty_when_no_anomalies():
    with patch.object(
        market_tools.momentum_vault,
        "fetch_fred_anomalies",
        return_value=[],
    ):
        rows = market_tools.fetch_fred_anomalies(days=14)
    assert rows == []
