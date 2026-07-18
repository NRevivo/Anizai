"""
agent/agents/market_bridge.py — Market Bridge retrieval agent (spec §8.4.3).

Purpose: provide hard numerical evidence — Polymarket odds + 30-day price
history, cross-platform linkages, FRED macro anomalies, and Google Trends
hype signals — for downstream synthesis.

Sprint 19 scope (per `data-pipeline/docs/agentic_hub_implementation.md` T19.4):
    - Polymarket Tier 1 / Tier 2 split
    - mapping_dict cross-platform linkage
    - FRED anomalies (14-day window)
    - Google Trends per entity
    - Weather + aviation are deferred to Sprint 21+ (impl. doc T21.6)

Sprint 21 T21.6 audit:
    Tier 2 path (polymarket_slug=None → polymarket=None) verified correct;
    no changes needed. KG-PHASE8-12 (auto-pick resolver) still deferred.

KG-PHASE8-18 investigation (T21.6):
    KG-PHASE8-18 described "empty summary_text" on vault_market evidence
    items. Investigation confirms this was a misnomer — the relevant field
    is `snippet`, not `summary_text`, and it IS populated for vault_market
    items (PulseEvidence.market_consensus rows carry `executive_summary`
    which flows into rate_evidence's `snippet` via `_normalize_pulse()`).
    No code change required. KG-PHASE8-18 is CLOSED.

Algorithm (spec §8.4.3):
    1. If polymarket_slug provided (Tier 1):
       a. fetch_latest("polymarket", slug)               → current odds + momentum
       b. fetch_time_series("polymarket", slug, hours=720) → 30-day history + whale_alerts
       Tier 2: polymarket → None.
    2. If canonical_event_id provided:
       lookup_by_canonical → for each link, fetch_latest(platform, platform_id).
    3. fetch_fred_anomalies(days=14).
    4. For each entity in `entities`: fetch_latest("googletrends", entity).

Sparse `linked_sources` (Sprint 19 design override D4):
    `mapping_dict.find_similar_and_link()` is the Phase-7 Gold-job
    integration that populates cross-platform linkages. Pre-Phase-7, the
    `mapping_dict` table is sparse and most `canonical_event_id` lookups
    will return `[]` — that is the *expected* default, not a bug. Tests
    fixture both populated and empty cases (impl. doc D4 line 147 +
    `task_plan.md` Phase 8 §3.1, audit §4 D-5). Sprint 20+ readers: do
    not treat empty `linked_sources` as a regression.

Spec drifts handled here (T19.4 corrections, 2026-04-30):
    - Drift C: spec wants FRED `indicator_name`; production stores only
      `series_id`. Resolved with an agent-layer convenience lookup
      (`FRED_INDICATOR_NAMES`) that maps well-known series → human-readable
      names; falls back to `series_id` for unknown series. See OQ-1 note
      on the constant.
    - Drift D: spec step 3 mentions filtering FRED anomalies by
      `impact_area` relevance. No `impact_area` field exists in production.
      Sprint 19 returns all anomalies in the 14-day window; domain-aware
      filtering belongs to Query Understanding output (Sprint 22+).
    - Drift E: spec wants `trend_direction: "rising"|"falling"|"stable"`.
      Production stores only `change_24h` (numeric). Derived at agent via
      ±5.0 score-point threshold (matches the Public_Hype_Alert magnitude
      floor used by gold_job).

Service isolation (CLAUDE.md §3.3): persistence is reached only through
`agent/tools/{market,mapping}_tools` — this module does not import
persistence directly.

Per Sprint 19 D2: agentEvents emission and budget tracking deferred to
Sprint 25 / Sprint 22+ (see agent/agents/__init__.py).

Spec references:
    - data-pipeline/docs/agentic_hub_spec.md §8.4.3
    - data-pipeline/docs/sprint19_persistence_audit.md §3.3, §4 D-2/D-5/D-6
    - data-pipeline/docs/agentic_hub_implementation.md T19.4 (Sprint 19 scope)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from agent.tools import mapping_tools, market_tools


# ==========================================================
# Spec constants
# ==========================================================

POLYMARKET_TIME_SERIES_HOURS = 720       # spec §8.4.3 step 1b — 30-day history
                                         # (Sprint 22 T22.4: same window is
                                         # written to the predictionSeries
                                         # subcollection — tune in one place)
FRED_ANOMALY_DAYS = 14                   # spec §8.4.3 step 3

# Threshold for trend_direction derivation (Drift E). Google Trends scores
# are 0-100; |change_24h| > 5.0 = same magnitude floor that gold_job uses
# to fire Public_Hype_Alert (see _GOOGLETRENDS_HYPE_THRESHOLD in
# processing/gold_job.py). Below the threshold the change is treated as
# noise → "stable".
TREND_DIRECTION_THRESHOLD = 5.0

# Agent-layer convenience dict (Drift C resolution). Maps the FRED series
# IDs the platform actively ingests today to human-readable indicator
# names so synthesis output reads as "10-Year vs 2-Year Treasury Yield
# Spread" rather than "T10Y2Y". This is NOT authoritative — the canonical
# mapping should eventually live in the FRED producer (or a shared
# lookup table consumed by both the producer and this agent) so the
# series-to-name mapping is enforced at ingestion. Until then, additions
# here are advisory only and unknown series fall back to their `series_id`.
FRED_INDICATOR_NAMES: dict[str, str] = {
    "T10Y2Y":     "10-Year vs 2-Year Treasury Yield Spread",
    "VIXCLS":     "CBOE Volatility Index",
    "FEDFUNDS":   "Federal Funds Effective Rate",
    "DGS10":      "10-Year Treasury Constant Maturity",
    "UNRATE":     "Unemployment Rate",
    "CPIAUCSL":   "Consumer Price Index — All Urban Consumers",
    "PAYEMS":     "Total Nonfarm Payroll Employment",
    "GDPC1":      "Real Gross Domestic Product",
    "DCOILWTICO": "WTI Crude Oil Price",
    "DEXUSEU":    "USD/EUR Exchange Rate",
}


def run(
    *,
    polymarket_slug: Optional[str] = None,
    canonical_event_id: Optional[str] = None,
    entities: Optional[list[str]] = None,
    now: Optional[datetime] = None,
    raw_question: Optional[str] = None,
    has_market_question_intent: bool = False,
) -> dict:
    """
    Execute the Market Bridge retrieval algorithm.

    Args:
        polymarket_slug:    Forward-compat entry point for a future QU
                            auto-pick (when KG-PHASE8-12's vector index
                            lands — see revised plan §Future Enhancement
                            4). Always None in V1; if non-None, takes
                            precedence over the fuzzy-match resolver.
        canonical_event_id: Canonical event key for cross-platform linkage
                            (mapping_dict). None or unmatched → empty
                            `linked_sources`.
        entities:           List of entity names extracted by Query
                            Understanding (people, institutions, assets).
                            One Google Trends fetch fires per entity; an
                            empty list / None means "no Trends fetches".
        now:                Reference "now". Currently unused (Market
                            Bridge does no recency weighting), but kept
                            on the signature for symmetry with Researcher
                            and Pulse Analyst so the `vault_query` node
                            (T19.8) can dispatch all three uniformly.
        raw_question:       The user's free-text question. Used by the
                            Sprint 22 T22.1 fuzzy-match resolver only
                            when `has_market_question_intent` is True
                            AND `polymarket_slug` is not provided.
        has_market_question_intent:
                            QU's boolean classification — True only when
                            the question asks about a discrete,
                            market-resolvable outcome. Gates the resolver
                            so Tier 2 (open-ended) questions don't pay
                            for a pg_trgm DB round-trip.

    Returns:
        MarketEvidence dict per spec §8.4.3 with the T19.4 drift
        resolutions documented at module-level.

        `empty=True` only when *all four* sources returned nothing
        (polymarket is None AND linked_sources=[] AND fred_anomalies=[]
        AND google_trends=[]). Any single populated source flips
        `empty=False` so synthesis doesn't misinterpret partial results.

    Polymarket resolution order (Sprint 22 T22.2):
        1. If `polymarket_slug` is provided, use it (forward-compat).
        2. Else, if `has_market_question_intent` AND `raw_question`,
           fuzzy-match via `find_polymarket_market_by_question`.
        3. Else, `polymarket` → None (Tier 2 behavior).
    """
    # `now` is part of the contract but not consumed in Sprint 19. Marking
    # it explicitly avoids "unused argument" lint noise without dropping
    # the parameter (Sprint 22+ may add recency weighting to FRED anomalies).
    del now

    polymarket_payload: Optional[dict] = None
    if polymarket_slug:
        polymarket_payload = _build_polymarket(polymarket_slug)
    elif has_market_question_intent and raw_question:
        polymarket_payload = _build_polymarket_from_question(raw_question)

    linked_sources = (
        _build_linked_sources(canonical_event_id) if canonical_event_id else []
    )
    fred_anomalies = _build_fred_anomalies()
    google_trends = _build_google_trends(entities or [])

    empty = (
        polymarket_payload is None
        and not linked_sources
        and not fred_anomalies
        and not google_trends
    )

    return {
        "polymarket": polymarket_payload,
        "linked_sources": linked_sources,
        "fred_anomalies": fred_anomalies,
        "google_trends": google_trends,
        "empty": empty,
    }


# ==========================================================
# Polymarket (Tier 1)
# ==========================================================

def _build_polymarket(slug: str) -> Optional[dict]:
    """
    Spec §8.4.3 step 1: current odds + momentum + 30-day history + whale
    alerts. If `fetch_latest` returns None the market is not in the vault
    yet — return None so the polymarket key drops to Tier-2 shape.

    Naming note (Sprint 22): the parameter is called `slug` and the
    returned key is `market_slug` for historical reasons, but the actual
    value flowing through is the Polymarket `asset_id` (WebSocket) or
    `condition_id` (REST snapshot) — see processing/silver_job.py:163-167.
    `external_reference_id` is keyed on that identifier in momentum_vault,
    so the round-trip works regardless of the variable name. Rename
    deferred — out of Sprint 22 scope.
    """
    latest = market_tools.fetch_latest("polymarket", slug)
    if latest is None:
        return None

    history_rows = market_tools.fetch_time_series(
        source_name="polymarket",
        external_reference_id=slug,
        hours=POLYMARKET_TIME_SERIES_HOURS,
    )

    return _pack_polymarket_payload(
        latest_row=latest,
        external_ref=slug,
        history_rows=history_rows,
    )


def _build_polymarket_from_question(question: str) -> Optional[dict]:
    """
    Sprint 22 T22.2: resolve the user's raw question to a Polymarket
    market via pg_trgm fuzzy-match, then assemble the same MarketEvidence
    shape as `_build_polymarket(slug)`.

    The resolver returns a momentum_vault row whose `external_reference_id`
    is the Polymarket condition_id (REST) or asset_id (WebSocket) — that
    value is the key the time-series query uses to fetch the 720-hour
    history for the same market.

    Edge cases:
        - Resolver returns None (no row above threshold) → Tier 2.
        - Resolver returns a row with empty `external_reference_id` → None
          (defensive — vault data integrity issue if hit; the column is
          NOT NULL in the schema).
        - `fetch_time_series` returns [] → still Tier 1 with an empty
          `price_history`. `current_odds` from the resolver row is the
          headline number; the trend chart is supplementary. The frontend
          renders the chart's empty state and keeps the market_comparison
          BI card.
        - `fetch_time_series` raises → propagates to vault_query's
          `_await` handler. Transient DB errors (DNS/connection races) are
          retried inside the tools wrapper (Sprint 26 T26.6 — market_tools
          wraps every read in `vault_read_retry`); only an exhausted-retry
          or a permanent error reaches here. Same behavior as today's
          `_build_polymarket(slug)` path.

    Skipping a redundant `fetch_latest`:
        The resolver row already carries `current_value`, `change_24h`,
        `change_7d`, `change_30d` — identical to what `fetch_latest`
        would return for the same `external_reference_id`. Reusing it
        saves one round-trip per Tier 1 forecast.
    """
    latest = market_tools.find_polymarket_market_by_question(question)
    if latest is None:
        return None

    external_ref = latest.get("external_reference_id") or ""
    if not external_ref:
        return None

    history_rows = market_tools.fetch_time_series(
        source_name="polymarket",
        external_reference_id=external_ref,
        hours=POLYMARKET_TIME_SERIES_HOURS,
    )

    return _pack_polymarket_payload(
        latest_row=latest,
        external_ref=external_ref,
        history_rows=history_rows,
    )


def _pack_polymarket_payload(
    *,
    latest_row: dict,
    external_ref: str,
    history_rows: list[dict],
) -> dict:
    """
    Shape the spec §8.4.3 step-1 polymarket dict from one latest row +
    the 720-hour history rows. Shared by both `_build_polymarket` (slug
    entry point) and `_build_polymarket_from_question` (fuzzy-match
    resolver entry point) per DRY (CLAUDE.md §3.2).
    """
    return {
        "current_odds": float(latest_row.get("current_value") or 0.0),
        "momentum": {
            "change_24h": float(latest_row.get("change_24h") or 0.0),
            "change_7d":  float(latest_row.get("change_7d") or 0.0),
            "change_30d": float(latest_row.get("change_30d") or 0.0),
        },
        "price_history": [_price_history_point(r) for r in history_rows],
        "whale_alerts": _extract_whale_alerts(history_rows),
        "market_slug": external_ref,
    }


def _price_history_point(row: dict) -> dict:
    """Compact chart-friendly view of one momentum_vault row."""
    ts = row.get("timestamp_utc")
    return {
        "timestamp": ts.isoformat() if isinstance(ts, datetime) else (ts or ""),
        "value": float(row.get("current_value") or 0.0),
    }


def _extract_whale_alerts(history_rows: list[dict]) -> list[dict]:
    """
    OQ-5: each time-series row whose `metadata_extension.whale_alert` is
    True becomes one entry. Caller can render these as event markers on
    the price chart.
    """
    alerts: list[dict] = []
    for row in history_rows:
        meta_ext = row.get("metadata_extension") or {}
        if meta_ext.get("whale_alert"):
            ts = row.get("timestamp_utc")
            alerts.append({
                "timestamp": ts.isoformat() if isinstance(ts, datetime) else (ts or ""),
                "current_value": float(row.get("current_value") or 0.0),
            })
    return alerts


# ==========================================================
# Cross-platform linkage (mapping_dict)
# ==========================================================

def _build_linked_sources(canonical_event_id: str) -> list[dict]:
    """
    Spec §8.4.3 step 2. For each mapping_dict row, fetch the latest
    momentum_vault observation. Sparse-by-default per Sprint 19 D4 —
    most canonical_event_ids will return [].
    """
    links = mapping_tools.lookup_by_canonical(canonical_event_id)
    if not links:
        return []

    out: list[dict] = []
    for link in links:
        platform = link.get("platform") or ""
        external_id = link.get("platform_specific_id") or ""
        if not platform or not external_id:
            continue
        latest = market_tools.fetch_latest(platform, external_id)
        if latest is None:
            continue
        out.append({
            "platform": platform,
            "external_id": external_id,
            "latest_value": float(latest.get("current_value") or 0.0),
            "unit": latest.get("unit") or "",
            "momentum": {
                "change_24h": float(latest.get("change_24h") or 0.0),
                "change_7d":  float(latest.get("change_7d") or 0.0),
                "change_30d": float(latest.get("change_30d") or 0.0),
            },
        })
    return out


# ==========================================================
# FRED anomalies
# ==========================================================

def _build_fred_anomalies() -> list[dict]:
    """
    Spec §8.4.3 step 3. Returns all anomalies in the 14-day window —
    `impact_area` filtering is deferred per Drift D (module docstring).
    """
    rows = market_tools.fetch_fred_anomalies(days=FRED_ANOMALY_DAYS)
    return [_pack_fred_anomaly(row) for row in rows]


def _pack_fred_anomaly(row: dict) -> dict:
    meta_ext = row.get("metadata_extension") or {}
    series_id = meta_ext.get("series_id") or row.get("external_reference_id") or ""
    indicator_name = FRED_INDICATOR_NAMES.get(series_id, series_id)

    flags = meta_ext.get("anomaly_flags") or []
    if not isinstance(flags, list):
        flags = []

    return {
        "series_id": series_id,
        "indicator_name": indicator_name,
        "current_value": float(row.get("current_value") or 0.0),
        "anomaly_flags": flags,
        "impact_level": int(meta_ext.get("impact_level") or 0),
        "change_7d": float(row.get("change_7d") or 0.0),
    }


# ==========================================================
# Google Trends
# ==========================================================

def _build_google_trends(entities: list[str]) -> list[dict]:
    """
    Spec §8.4.3 step 4. One fetch_latest per entity; entries with no
    vault hit are skipped (returning a stub would mislead synthesis).
    """
    out: list[dict] = []
    for entity in entities:
        if not entity:
            continue
        row = market_tools.fetch_latest("googletrends", entity)
        if row is None:
            continue
        out.append(_pack_google_trends(row, entity))
    return out


def _pack_google_trends(row: dict, keyword: str) -> dict:
    meta_ext = row.get("metadata_extension") or {}
    change_24h = float(row.get("change_24h") or 0.0)
    return {
        "keyword": keyword,
        "current_score": float(row.get("current_value") or 0.0),
        "trend_direction": _derive_trend_direction(change_24h),
        "hype_alert": bool(meta_ext.get("public_hype_alert")),
    }


def _derive_trend_direction(change_24h: float) -> str:
    """
    Drift E: derive the spec's three-state trend label from numeric
    change_24h. Threshold mirrors the Public_Hype_Alert magnitude floor
    so "rising" lines up with what gold_job already flags as hype-worthy.
    """
    if change_24h > TREND_DIRECTION_THRESHOLD:
        return "rising"
    if change_24h < -TREND_DIRECTION_THRESHOLD:
        return "falling"
    return "stable"
