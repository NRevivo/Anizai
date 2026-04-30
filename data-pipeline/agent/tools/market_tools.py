"""
agent/tools/market_tools.py — Market Bridge's momentum tools (Sprint 19 §8.4.3).

Wraps `persistence/momentum_vault.py`. The Market Bridge agent
(`agent/agents/market_bridge.py`, T19.4) calls these and never imports
persistence.

Drift normalization handled here (audit §4):
    D-2: spec §8.4.3 step 1b calls fetch_time_series with hours=720, step 3
         calls fetch_fred_anomalies with days=14. Persistence defaults are
         hours=24 / days=7. The wrapper has no defaults for these args, so a
         caller cannot silently pick up a wrong window if they forget to
         pass the value.
    D-6: source-specific fields like `public_hype_alert`, `whale_alert`,
         `anomaly_flags` live inside `metadata_extension` JSONB. The wrapper
         returns the row as-is; the Market Bridge agent extracts per source.
         No new persistence function is needed.

Spec references:
    - data-pipeline/docs/agentic_hub_spec.md §8.4.3
    - data-pipeline/docs/sprint19_persistence_audit.md §3.3, §4 D-2/D-6, §6
"""

from __future__ import annotations

from typing import Optional

from persistence import momentum_vault


def fetch_latest(
    source_name: str,
    external_reference_id: str,
) -> Optional[dict]:
    """
    Latest momentum_vault row for an asset/indicator.

    Spec call sites (§8.4.3):
        step 1a — fetch_latest("polymarket", market_slug)
        step 4  — fetch_latest("googletrends", keyword) per entity
        step 5  — fetch_latest("openweather"|"opensky", id) when relevant

    Args:
        source_name:           'polymarket', 'fred', 'googletrends',
                               'openweather', 'opensky'.
        external_reference_id: Asset/indicator ID (market slug, FRED series,
                               keyword, station id, bounding-box id).

    Returns:
        Most-recent row dict from momentum_vault, or None.
    """
    return momentum_vault.fetch_latest(source_name, external_reference_id)


def fetch_time_series(
    source_name: str,
    external_reference_id: str,
    hours: int,
) -> list[dict]:
    """
    Time-series window for an asset/indicator.

    Spec §8.4.3 step 1b passes hours=720 (30 days). `hours` is required at
    the wrapper level so the persistence default (24) cannot silently apply
    if a caller forgets to pass it.

    Args:
        source_name:           e.g. 'polymarket'.
        external_reference_id: Asset ID.
        hours:                 Lookback window. Required (no default).

    Returns:
        List of rows ordered by timestamp_utc ASC. Empty list if no data.
    """
    return momentum_vault.fetch_time_series(
        source_name=source_name,
        external_reference_id=external_reference_id,
        hours=hours,
    )


def fetch_fred_anomalies(days: int) -> list[dict]:
    """
    FRED rows in the last `days` days where automation triggers fired.

    Spec §8.4.3 step 3 passes days=14. `days` is required at the wrapper
    level so the persistence default (7) cannot silently apply.

    Args:
        days: Lookback window in calendar days. Required (no default).

    Returns:
        List of rows ordered by timestamp_utc DESC. Empty list if no
        anomalies were detected in the window.
    """
    return momentum_vault.fetch_fred_anomalies(days=days)
