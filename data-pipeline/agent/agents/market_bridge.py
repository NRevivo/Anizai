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

import logging
import time
from datetime import datetime, timezone
from typing import Optional

from agent.config import settings
from agent.tools import mapping_tools, market_tools, polymarket_api

logger = logging.getLogger(__name__)


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

    Polymarket resolution order (A1, superseding Sprint 22 T22.2):
        1. `polymarket_slug` (the picker's conditionId) → exact lookup on
           momentum_vault.external_reference_id, latest row.
        2. On a miss, or when no slug was supplied and the question is
           market-shaped → pg_trgm fuzzy match via
           `find_polymarket_market_by_question` (unchanged).
        3. Still nothing → `polymarket` is None (Tier 2 behavior), which
           synthesize renders as an explicit no-market state rather than a
           fabricated benchmark.
    """
    # `now` is part of the contract but not consumed in Sprint 19. Marking
    # it explicitly avoids "unused argument" lint noise without dropping
    # the parameter (Sprint 22+ may add recency weighting to FRED anomalies).
    del now

    # Resolution cascade (A1). Exact identity first, similarity second.
    polymarket_payload: Optional[dict] = None
    if polymarket_slug:
        polymarket_payload = _build_polymarket(polymarket_slug)

    # An exact-lookup MISS falls through to the resolver rather than going
    # straight to Tier 2. The miss is real and expected: `external_reference_id`
    # is the asset_id for WebSocket rows and the condition_id for REST snapshots
    # (silver_job), so a market held under the other key is invisible to an exact
    # lookup while still matching on question text. Without this fallthrough,
    # supplying a conditionId could resolve to LESS than supplying nothing —
    # a strictly worse outcome for handing us better information.
    #
    # `polymarket_slug` also unlocks the resolver on its own: the intent flag
    # exists so open-ended Tier 2 questions don't pay for a pg_trgm round-trip,
    # but a user who picked a market off the picker has asked a market-shaped
    # question by construction, whatever QU inferred from the text.
    if polymarket_payload is None and raw_question and (
        has_market_question_intent or polymarket_slug
    ):
        polymarket_payload = _build_polymarket_from_question(raw_question)

    # A3 — last resort: the user picked a market we have never collected. Ask
    # Polymarket directly rather than telling them their own selection does not
    # exist. Only reachable when an identifier was supplied, so it costs a
    # network call only on the picker path, never on free-text questions.
    if polymarket_payload is None and polymarket_slug:
        polymarket_payload = _build_polymarket_live(polymarket_slug)

    # A4 — vault-derived payloads carry a stored end date that may have passed
    # since the last sweep. The live path skipped this deliberately: it already
    # read Polymarket directly, so its resolution state is current by
    # construction and a second confirmation would spend budget to learn nothing.
    if polymarket_payload is not None and not polymarket_payload.get(
        "resolution_verified"
    ):
        polymarket_payload = _apply_resolved_market_guard(polymarket_payload)

    # The single line that makes the new V6 criterion checkable from logs rather
    # than from a database dig: what we actually resolved, by name. The partner
    # is investigating a picker defect where clicking one event submits a market
    # from ANOTHER (clicked "Fed Decision in December", got the September
    # meeting). Such a forecast is green on every mechanical check — real price,
    # rendered chart, tier_1 — and wrong about the month. Naming the resolved
    # question and its end date at resolution time is what makes a confidently
    # wrong match visible.
    if polymarket_payload is not None:
        logger.info(
            "market_bridge: RESOLVED market_slug=%s via=%s question=%r "
            "end_date=%r odds=%.4f",
            polymarket_payload.get("market_slug", ""),
            "conditionId" if polymarket_slug else "question-match",
            str(polymarket_payload.get("question", ""))[:90],
            polymarket_payload.get("end_date_iso", ""),
            float(polymarket_payload.get("current_odds") or 0.0),
            extra={"always_emit": True},
        )

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


def _build_polymarket_live(condition_id: str) -> Optional[dict]:
    """
    A3 — build the MarketEvidence payload from a live Polymarket lookup when the
    vault has no row for a conditionId the frontend supplied.

    Why this exists:
        The producer collects hourly over a topic-filtered universe. A market can
        therefore be pickable in the UI minutes before we first store it, and
        indefinitely if it sits outside the collector's filters. Both are normal.
        Without this net, the user picks a market off our own screen and is told
        no market matched — which reads as a broken product rather than as a
        collection lag. It also makes us independent of whether the partner ever
        aligns his picker's filters with the collector's (PT1).

    What is and is not available on this path:
        `current_odds` and `price_history` are real. `momentum` is zeroed and
        `whale_alerts` is empty — both are derived from a stored series we do not
        have, and fabricating them would be exactly the "default served as a
        measurement" failure this sprint exists to remove. Zeroed momentum is
        already the Silver-layer convention for "not yet calculated", so
        downstream consumers read it correctly.

    Returns None when the market does not exist, cannot be priced, or Gamma is
    unreachable — in which case the forecast proceeds as Tier 2 and synthesize
    renders an explicit no-market state (A5). Never raises.
    """
    # ONE wall-clock budget across both calls, enforced HERE.
    #
    # It has to be here because `requests` cannot do it. A `timeout` value bounds
    # the connect phase and the read phase separately — it is not a deadline —
    # and the read half only bounds the gap between socket reads, so a slow-drip
    # response outlives its nominal limit. Two nominally-5s calls were therefore
    # never worth 10s; they were worth "at least 10s". Measuring elapsed time
    # between them is the only thing that actually bounds the pair, and this path
    # runs inside vault_query's shared 15s per-agent future, where an overrun
    # does not degrade the market slice — it fails the whole forecast.
    budget_s = settings.POLYMARKET_A3_COMBINED_BUDGET_S
    started = time.monotonic()

    market = polymarket_api.fetch_market_by_condition_id(
        condition_id, timeout_s=budget_s,
    )
    lookup_elapsed_s = time.monotonic() - started
    if market is None:
        return None

    remaining_s = budget_s - lookup_elapsed_s

    price = (market.get("outcome_prices") or {}).get(
        polymarket_api.AFFIRMATIVE_LABEL
    )
    if price is None:
        # Not a Yes/No market (sports fixtures, Over/Under) or never traded.
        # There is no affirmative probability to report, and inventing one is
        # worse than reporting none.
        logger.warning(
            "market_bridge: live market %s has no %r outcome — no benchmark "
            "available for this question",
            condition_id, polymarket_api.AFFIRMATIVE_LABEL,
        )
        return None

    logger.warning(
        "market_bridge: conditionId=%s resolved LIVE from Polymarket — no vault "
        "row exists for it. The market is pickable in the UI but uncollected; "
        "check the producer's topic filter if this recurs for the same market.",
        condition_id,
    )

    yes_token = str(
        (market.get("clob_token_ids") or {}).get(
            polymarket_api.AFFIRMATIVE_LABEL, ""
        ) or ""
    )

    # Spend what the lookup left, and only if it is worth spending. Skipping
    # here costs a chart; overrunning here costs the whole forecast, because
    # vault_query is fail-fast on a per-agent timeout.
    price_history: list[dict] = []
    history_elapsed_s = 0.0
    if not yes_token:
        pass
    elif remaining_s < settings.POLYMARKET_A3_MIN_HISTORY_S:
        logger.warning(
            "market_bridge: skipping price history for %s — only %.2fs of the "
            "%.0fs combined budget remained after the market lookup. The "
            "forecast proceeds with a real benchmark and no chart.",
            condition_id, remaining_s, budget_s,
        )
    else:
        history_started = time.monotonic()
        price_history = polymarket_api.fetch_price_history(
            yes_token, timeout_s=remaining_s,
        )
        history_elapsed_s = time.monotonic() - history_started

    # OBSERVED elapsed, per call, exempt from INFO sampling. The budget above is
    # what we asked for; this is what we got. Because the underlying timeouts are
    # per-phase rather than wall-clock, the two can diverge — and the only way to
    # know whether the 6s budget holds in the field is to have measured it there.
    # V6 reads these numbers; without them it would confirm the numbers we hoped
    # for rather than the ones that happened.
    total_elapsed_s = lookup_elapsed_s + history_elapsed_s
    log_overrun = logger.warning if total_elapsed_s > budget_s else logger.info
    log_overrun(
        "market_bridge: A3 live path for %s took %.2fs total "
        "(lookup %.2fs, history %.2fs, budget %.0fs) -> %d points",
        condition_id, total_elapsed_s, lookup_elapsed_s, history_elapsed_s,
        budget_s, len(price_history),
        extra={"always_emit": True},
    )

    return {
        "current_odds": float(price),
        # Zeroed, not fabricated — see docstring.
        "momentum": {"change_24h": 0.0, "change_7d": 0.0, "change_30d": 0.0},
        "price_history": price_history,
        "whale_alerts": [],
        "market_slug": condition_id,
        "question": str(market.get("question", "") or ""),
        "end_date_iso": str(
            market.get("end_date_iso") or market.get("end_date") or ""
        ),
        "market_status": str(market.get("market_status", "") or ""),
        # This payload WAS the live read, so its resolution state is current by
        # construction — A4's guard would only re-ask Polymarket what it just
        # answered, spending a second call out of a shared budget.
        "has_ended": (
            str(market.get("market_status", "")) == "closed"
            or _market_end_date_passed(
                str(market.get("end_date_iso") or market.get("end_date") or ""),
                datetime.now(timezone.utc),
            )
        ),
        "resolution_verified": True,
    }


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
        "price_history": _resolve_price_history(latest_row, history_rows),
        "whale_alerts": _extract_whale_alerts(history_rows),
        "market_slug": external_ref,
        # The resolved market's own question text. Propagated by silver_job into
        # metadata_extension since Sprint 22 T22.1 (it is what the pg_trgm
        # resolver matches on) but never surfaced on the payload until now. It is
        # what makes a wrong-market resolution legible to a human.
        "question": str(
            (latest_row.get("metadata_extension") or {}).get("question", "") or ""
        ),
        # A4 — resolution state. Carried on the payload rather than acted on
        # here so the decision of what to TELL the user stays in synthesize,
        # which owns user-facing copy (agent-design: one node, one job).
        "end_date_iso": str(
            (latest_row.get("metadata_extension") or {}).get("end_date_iso", "") or ""
        ),
        "market_status": str(latest_row.get("status", "") or ""),
        "has_ended": False,       # set by _apply_resolved_market_guard
        "resolution_verified": False,
    }


def _market_end_date_passed(end_date_iso: str, now: datetime) -> bool:
    """
    True when a market's end date is strictly in the past.

    Accepts both shapes Gamma emits: the date-only `endDateIso`
    ("2026-09-16") and a full timestamp ("2026-09-16T00:00:00Z"). A date-only
    value is read as midnight UTC, so a market is treated as ended only once its
    end DAY has fully passed — the conservative direction, since calling a live
    market resolved is the more damaging error of the two.

    An empty or unparseable value returns False. Absence is handled separately
    by the caller (it triggers a live confirmation); it is not evidence of
    having ended.
    """
    if not end_date_iso:
        return False
    try:
        parsed = datetime.fromisoformat(end_date_iso.replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed < now


def _apply_resolved_market_guard(
    payload: dict, *, now: Optional[datetime] = None,
) -> dict:
    """
    A4 — never present a resolved market as a live one.

    The stored `status` is stale by construction. The producer sweeps hourly, so
    a market can resolve in the interval between our snapshot and the user's
    question, and `status` would still read "active" from the last sweep. The
    end date is the honest signal, and when it says the market is over we confirm
    against Polymarket rather than trusting either stored field.

    Two triggers for the live confirmation (plan §2.3):
      - the stored end date has passed;
      - there is NO stored end date. Silence is not evidence: pre-S1 vault rows
        carry no `end_date_iso` at all, and ~1.6% of live markets (23 of 1,396,
        measured 2026-07-30) genuinely lack `endDateIso` on Gamma.

    Mutates and returns the payload with `has_ended`, `resolution_verified`, and
    — when the live call succeeds — a refreshed `current_odds`, `market_status`
    and `end_date_iso`. On any failure it leaves the stored values and marks
    `resolution_verified=False`, so synthesize can hedge its wording rather than
    assert something it could not check. Never raises.

    Note during the pre-wipe transition: existing vault rows have no
    `end_date_iso`, so this confirms live on every Tier-1 forecast. After the
    wipe, S1 guarantees the field on 98.4% of rows and this becomes rare.
    """
    now = now or datetime.now(timezone.utc)
    end_date_iso = str(payload.get("end_date_iso", "") or "")
    condition_id = str(payload.get("market_slug", "") or "")

    ended_by_stored_date = _market_end_date_passed(end_date_iso, now)
    if not ended_by_stored_date and end_date_iso:
        # A future end date we actually hold — nothing to verify.
        payload["has_ended"] = False
        payload["resolution_verified"] = True
        return payload

    if not condition_id:
        payload["has_ended"] = ended_by_stored_date
        payload["resolution_verified"] = False
        return payload

    live = polymarket_api.fetch_market_by_condition_id(condition_id)
    if live is None:
        payload["has_ended"] = ended_by_stored_date
        payload["resolution_verified"] = False
        logger.warning(
            "market_bridge: could not confirm resolution state for %s "
            "(stored end_date_iso=%r). Proceeding on stored values.",
            condition_id, end_date_iso or "<absent>",
        )
        return payload

    live_end_date = str(live.get("end_date_iso") or live.get("end_date") or "")
    live_status = str(live.get("market_status", "") or "")
    has_ended = live_status == "closed" or _market_end_date_passed(live_end_date, now)

    payload["end_date_iso"] = live_end_date or end_date_iso
    payload["market_status"] = live_status or payload.get("market_status", "")
    payload["has_ended"] = has_ended
    payload["resolution_verified"] = True

    live_price = (live.get("outcome_prices") or {}).get(
        polymarket_api.AFFIRMATIVE_LABEL
    )
    if live_price is not None:
        payload["current_odds"] = float(live_price)

    if has_ended:
        logger.warning(
            "market_bridge: market %s has RESOLVED (status=%r end_date=%r) — "
            "the forecast is retrospective, not predictive.",
            condition_id, live_status, live_end_date,
        )
    return payload


def _resolve_price_history(latest_row: dict, history_rows: list[dict]) -> list[dict]:
    """
    The market's price history: CLOB first, our own vault rows as the fallback.

    Why CLOB is preferred:
        The vault holds only what we have collected. Straight after a database
        wipe that is a handful of points, so the chart renders as a stub — a
        chart that is technically working and visually empty, which is the worst
        of both worlds because nothing looks broken. CLOB returns a rolling 30
        days on the very first request, regardless of when collection started.

    Why the vault fallback stays:
        CLOB is a third-party dependency on a strictly-optional feature. If it is
        unreachable the chart degrades to sparse — never to empty-on-error. The
        two sources also happen to cover the same span (CLOB's rolling 30 days
        vs POLYMARKET_TIME_SERIES_HOURS = 720h), so the fallback is a genuine
        substitute rather than a different chart.

    Why the YES token specifically:
        `clob_token_ids` is a dict keyed by outcome label. Charting the NO token
        would render the exact complement of the market's probability — a 7%
        market drawn as 93% — and nothing downstream could detect it, because a
        complement is a perfectly plausible-looking series.

    Returns the in-state `{timestamp, value}` shape either way, so
    write_to_firestore and the partner-facing document shape are untouched.
    """
    meta_ext = latest_row.get("metadata_extension") or {}
    token_ids = meta_ext.get("clob_token_ids") or {}

    # Pre-S1 vault rows carry no token ids at all, and rows written before P8
    # carry a positional LIST rather than a label-keyed dict. Both degrade to the
    # vault series rather than guessing which element is the affirmative one.
    if isinstance(token_ids, dict):
        yes_token = str(token_ids.get(polymarket_api.AFFIRMATIVE_LABEL, "") or "")
        if yes_token:
            started = time.monotonic()
            clob_history = polymarket_api.fetch_price_history(yes_token)
            elapsed_s = time.monotonic() - started
            # Observed, not nominal — the timeout bounds phases, not wall clock
            # (see polymarket_api). This is the common Tier-1 path, so it is the
            # number that matters most for the vault_query budget.
            logger.info(
                "market_bridge: CLOB price history took %.2fs -> %d points "
                "(vault fallback holds %d rows)",
                elapsed_s, len(clob_history), len(history_rows),
                extra={"always_emit": True},
            )
            if clob_history:
                return clob_history

    return [_price_history_point(r) for r in history_rows]


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
