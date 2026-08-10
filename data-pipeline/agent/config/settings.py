"""
Hub settings — Environment variables and configuration for the Agentic Hub.

This module is the hub's equivalent of `data-pipeline/config/settings.py` and
is intentionally separate from it. Rationale:

- The hub is a logically independent service (different deployment unit,
  different lifecycle, different domain — Firestore worker, not a Kafka
  producer or Flink job).
- Keeping hub config under `agent/config/` keeps the pipeline's config
  namespace clean and matches the directory layout in
  `agentic_hub_spec.md` §8.10 (which already places `agent/config/` under
  the agent package).
- This module is intended to live in a separate repository when the hub is
  eventually extracted; sharing a settings module with the pipeline would
  block that move.

Spec references:
    - data-pipeline/docs/agentic_hub_spec.md §8.11 (Configuration)
    - data-pipeline/docs/agentic_hub_spec_patch.md Patch 14 (env var block)

Loading strategy mirrors `config/settings.py` so the hub picks up the same
.env file in local dev (Section 9.1 — environment parity):

    1. data-pipeline/.env        (primary)
    2. data-pipeline/infrastructure/.env  (fallback)
"""

import os

from dotenv import load_dotenv

# ==========================================================
# 1. Project Paths
# ==========================================================
# This file lives at: data-pipeline/agent/config/settings.py
# BASE_DIR resolves to: data-pipeline/
# (three dirname() hops: settings.py -> config/ -> agent/ -> data-pipeline/)
BASE_DIR = os.path.dirname(
    os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))
    )
)

# ==========================================================
# 2. Load Environment Variables
# ==========================================================
_dotenv_path = os.path.join(BASE_DIR, ".env")
_dotenv_infra_path = os.path.join(BASE_DIR, "infrastructure", ".env")
if os.path.exists(_dotenv_path):
    load_dotenv(_dotenv_path)
elif os.path.exists(_dotenv_infra_path):
    load_dotenv(_dotenv_infra_path)
else:
    print(
        f"[WARNING] hub .env file not found at: "
        f"{_dotenv_path} or {_dotenv_infra_path}"
    )

# ==========================================================
# 3. Firebase Admin SDK
# ==========================================================
# FIREBASE_PROJECT_ID matches client/.env VITE_FIREBASE_PROJECT_ID.
# GOOGLE_APPLICATION_CREDENTIALS — path to service-account JSON in prod.
# Leave unset in dev and use ADC: `gcloud auth application-default login`.
FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID", "anizai-ai")
GOOGLE_APPLICATION_CREDENTIALS = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")

# ==========================================================
# 4. Worker Behavior
# ==========================================================
# AGENT_WORKER_ID must be unique per worker instance — used as the value
# written into `claimedBy` during atomic claim (§8.8.1).
AGENT_WORKER_ID = os.getenv("AGENT_WORKER_ID", "worker-1")
AGENT_MAX_CONCURRENT_SESSIONS = int(os.getenv("AGENT_MAX_CONCURRENT_SESSIONS", "3"))
# If a worker dies mid-claim, others may re-claim after this timeout (seconds).
AGENT_CLAIM_TIMEOUT_SECONDS = int(os.getenv("AGENT_CLAIM_TIMEOUT_SECONDS", "600"))

# ==========================================================
# 5. Sufficiency / Vault Query Loops
# ==========================================================
# Used by Sprint 19+ sufficiency_check node and routing functions.
# Defined here so the env-var contract is in one place from Sprint 18.
AGENT_MAX_VAULT_QUERY_ATTEMPTS = int(os.getenv("AGENT_MAX_VAULT_QUERY_ATTEMPTS", "2"))
AGENT_SUFFICIENCY_MIN_RELEVANCE = float(
    os.getenv("AGENT_SUFFICIENCY_MIN_RELEVANCE", "0.6")
)
AGENT_SUFFICIENCY_MIN_CONFIDENCE = float(
    os.getenv("AGENT_SUFFICIENCY_MIN_CONFIDENCE", "0.5")
)
AGENT_EVIDENCE_MIN_COUNT = int(os.getenv("AGENT_EVIDENCE_MIN_COUNT", "5"))

# ==========================================================
# 6. Reactive Search (Sprint 22+)
# ==========================================================
# AGENT_REACTIVE_* control the reactive_search node. TAVILY_API_KEY and
# REACTIVE_CACHE_TTL_SECONDS are added in Sprint 22 when reactive search is
# implemented.
AGENT_REACTIVE_SEARCH_ENABLED = os.getenv(
    "AGENT_REACTIVE_SEARCH_ENABLED", "true"
).lower() == "true"
AGENT_REACTIVE_MAX_PER_SESSION = int(os.getenv("AGENT_REACTIVE_MAX_PER_SESSION", "1"))
AGENT_REACTIVE_TIMEOUT_MS = int(os.getenv("AGENT_REACTIVE_TIMEOUT_MS", "6000"))
AGENT_REACTIVE_MAX_ARTICLES = int(os.getenv("AGENT_REACTIVE_MAX_ARTICLES", "5"))
AGENT_REACTIVE_DEFAULT_WINDOW_DAYS = int(
    os.getenv("AGENT_REACTIVE_DEFAULT_WINDOW_DAYS", "7")
)

# Reactive trigger (Sprint 23) — distinct from the AGENT_REACTIVE_* block
# above, which was sized for the deferred external-search microservice
# (Future Enhancement 1 in agentic_hub_implementation_phase8_revised.md).
# This counter limits how many `ingestion_triggers` Kafka messages the agent
# may emit per session in the producer-trigger V1 path (revised plan §Sprint
# 23). Naming mirrors AGENT_REACTIVE_MAX_PER_SESSION so a future swap to the
# external microservice becomes a config-only change. See KG-PHASE8-23 in
# task_plan.md for the dead-config cleanup of the older constant.
AGENT_REACTIVE_TRIGGER_MAX_PER_SESSION = int(
    os.getenv("AGENT_REACTIVE_TRIGGER_MAX_PER_SESSION", "1")
)

# ==========================================================
# 7. Staleness / Polymarket Cache
# ==========================================================
AGENT_STALENESS_WINDOW_HOURS = int(os.getenv("AGENT_STALENESS_WINDOW_HOURS", "4"))
AGENT_POLYMARKET_MIN_POOL = int(os.getenv("AGENT_POLYMARKET_MIN_POOL", "10000"))
AGENT_POLYMARKET_DRIFT_THRESHOLD = float(
    os.getenv("AGENT_POLYMARKET_DRIFT_THRESHOLD", "0.03")
)

# ==========================================================
# 8. Follow-up Budget
# ==========================================================
AGENT_FOLLOWUP_BUDGET_MS = int(os.getenv("AGENT_FOLLOWUP_BUDGET_MS", "6000"))

# Suggested-actions budget (Sprint 25, T25.1). The post-synthesis
# `generate_suggested_actions` node is a NON-ESSENTIAL cosmetic call: on
# overrun it degrades to an empty list and never fails the forecast
# (hub-principles G4 — graceful degradation, not a hang or a raise). Kept
# tight (5s) because it runs AFTER synthesis on the critical path, so a hung
# call here would eat into the ≤60s p95 main-forecast NFR (KG-B-5). Enforced
# as a TRUE ceiling: the node builds its OpenAI client with max_retries=0 so
# the timeout cannot be multiplied by SDK retries.
AGENT_SUGGESTED_ACTIONS_BUDGET_MS = int(
    os.getenv("AGENT_SUGGESTED_ACTIONS_BUDGET_MS", "5000")
)

# agentEvents drain timeouts (Sprint 25 T25.6 / T25.13). Bounded waits for the
# non-blocking agentEvents writer to flush. The pre-done drain
# (write_to_firestore, before the session flips to 'done') and the
# process_query finally-drain use the per-run value; worker shutdown uses a
# longer ceiling since it may flush the tail of an in-flight run. Both are hard
# ceilings so a stuck Firestore write can't hang the forecast or shutdown.
AGENT_EVENT_DRAIN_TIMEOUT_MS = int(os.getenv("AGENT_EVENT_DRAIN_TIMEOUT_MS", "5000"))
AGENT_EVENT_SHUTDOWN_DRAIN_TIMEOUT_MS = int(
    os.getenv("AGENT_EVENT_SHUTDOWN_DRAIN_TIMEOUT_MS", "10000")
)

# ==========================================================
# 9. Health Endpoint (Sprint 18 T7)
# ==========================================================
# Internal monitoring only — not user-facing. Bound inside the worker
# container; exposed via docker-compose port mapping.
HUB_HEALTH_HOST = os.getenv("HUB_HEALTH_HOST", "0.0.0.0")
HUB_HEALTH_PORT = int(os.getenv("HUB_HEALTH_PORT", "8000"))

# ==========================================================
# 10. OpenAI — Per-Node Model Selection
# ==========================================================
# Each LLM-using node has its own env var so we can swap models per-node
# without code changes (Patch 14).
# Sprint 18 stub processing does not call any of these; defined so the
# env-var contract is stable from sprint one.
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL_QUERY_UNDERSTANDING = os.getenv(
    "OPENAI_MODEL_QUERY_UNDERSTANDING", "gpt-4o-mini"
)
OPENAI_MODEL_SUFFICIENCY_CHECK = os.getenv(
    "OPENAI_MODEL_SUFFICIENCY_CHECK", "gpt-4o-mini"
)
OPENAI_MODEL_EVIDENCE_RATING = os.getenv(
    "OPENAI_MODEL_EVIDENCE_RATING", "gpt-4o-mini"
)
OPENAI_MODEL_SYNTHESIS = os.getenv("OPENAI_MODEL_SYNTHESIS", "gpt-4o")
OPENAI_MODEL_FOLLOWUP = os.getenv("OPENAI_MODEL_FOLLOWUP", "gpt-4o-mini")
OPENAI_MODEL_SUGGESTED_ACTIONS = os.getenv(
    "OPENAI_MODEL_SUGGESTED_ACTIONS", "gpt-4o-mini"
)
OPENAI_EMBEDDING_MODEL = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")

# ==========================================================
# 11. Agent Version (Sprint 23.5 T23.5.12 — canonical home; Sprint 26 T26.5)
# ==========================================================
# Single source of version truth (Track 3 / absorbs the relocation half of
# the old T26.5). Previously hardcoded in agent/nodes/synthesize.py:93, which
# split version identity from config. synthesize.py and health.py now import
# it from here.
#
# Sprint 26 T26.5: AGENT_VERSION now resolves to `<base>+<git-short-sha>` so the
# deployed build is identifiable from the health response AND every SessionResult
# / follow-up message. The base is the human-readable label; the short-sha
# (AGENT_GIT_COMMIT_SHORT_SHA, injected at image-build time) carries the real
# build identity. Kept as a single `AGENT_VERSION` name — NOT a separate _FULL —
# so all consumers (health, synthesize's re-export, followup) read one value and
# the emulator coupling `result["agentVersion"] == synthesize.AGENT_VERSION`
# holds by construction. When the sha env var is unset (local dev / tests),
# AGENT_VERSION is just the base (no trailing '+').
#
# History: 0.1.0-sprint18-stub → 0.2.0-sprint19-retrieval-stub-synthesis →
#          0.3.0 (real synthesis + Firestore) →
#          0.4.0-sprint21-clarification-tier2 → 0.5.0-sprint23.5 →
#          0.5.0-sprint26 (+sha).
# The BASE is env-overridable (via AGENT_VERSION) so a hotfix build can stamp
# its own label without a code change; the sha still appends on top.
_AGENT_VERSION_BASE = os.getenv("AGENT_VERSION", "0.5.0-sprint26")
AGENT_GIT_COMMIT_SHORT_SHA = os.getenv("AGENT_GIT_COMMIT_SHORT_SHA", "").strip()
AGENT_VERSION = (
    f"{_AGENT_VERSION_BASE}+{AGENT_GIT_COMMIT_SHORT_SHA}"
    if AGENT_GIT_COMMIT_SHORT_SHA
    else _AGENT_VERSION_BASE
)

# ==========================================================
# 12. Logging (T19.14 — closes KG-PHASE8-7)
# ==========================================================
# Worker-entry log level. Without `logging.basicConfig()` at startup, the
# root logger has no handlers and INFO-level lines from every agent
# module disappear (only WARNING+ surfaces, and those go to stderr with
# the bare-default format). KG-PHASE8-7 was raised at the Sprint 18
# closeout when log output was missing despite `logger.info(...)` calls
# throughout worker.py. T19.14 wires basicConfig(level=settings.LOG_LEVEL)
# at the top of `agent/worker.py:main()`. Default INFO matches the
# pipeline-side producers; flip to DEBUG via env var for local triage.
LOG_LEVEL = os.getenv("AGENT_LOG_LEVEL", "INFO")

# ==========================================================
# 13. Vault-read retry profile (Sprint 26 T26.6)
# ==========================================================
# Transient-error retry for the agent's vault READS (agent/tools/*), wrapping
# utils.retry.retry_on_transient at the tools chokepoint (hub-principles P2).
# DELIBERATELY TIGHTER than Gold's insert profile (5 / 1.0 / 16.0 → ~15s
# backoff): agent reads run inside vault_query's per-agent future, hard-capped
# at PER_AGENT_TIMEOUT_S (15s, vault_query.py:69), and vault_query is fail-fast
# — an exhausted-retry read raises and fails the whole forecast. Gold's backoff
# would exceed that window (the future times out first, paying the full wait and
# still failing). 3 attempts / 0.5s base / 2.0s cap → ≤1.5s total backoff rides
# out short transient DNS/connection races (the Phase-9.5 F6 threat model) with
# room for the ~10 DB queries an agent issues. It does NOT paper over a full
# Postgres scale-restart (<15s) — the per-agent timeout fails that regardless
# (accepted existing property, unchanged by 26.6). Env-overridable for tuning
# during the initial cloud test.
AGENT_VAULT_RETRY_MAX_ATTEMPTS = int(os.getenv("AGENT_VAULT_RETRY_MAX_ATTEMPTS", "3"))
AGENT_VAULT_RETRY_BASE_DELAY_S = float(os.getenv("AGENT_VAULT_RETRY_BASE_DELAY_S", "0.5"))
AGENT_VAULT_RETRY_MAX_DELAY_S = float(os.getenv("AGENT_VAULT_RETRY_MAX_DELAY_S", "2.0"))


# ==========================================================
# 14. Polymarket public-API budget (A3 + the CLOB history fetch)
# ==========================================================
# The hub's only outbound calls to a non-OpenAI external service. Both live in
# agent/tools/polymarket_api.py and are made from inside market_bridge, i.e.
# inside vault_query's per-agent future, hard-capped at PER_AGENT_TIMEOUT_S (15s,
# vault_query.py:69) — which that future ALSO spends on FRED anomalies, Google
# Trends per entity, and the vault reads.
#
# 5s, SINGLE ATTEMPT, no retry loop. Deliberately not the vault-read retry
# profile above: a vault read failing is a broken forecast, while these two calls
# are both strictly-optional enrichment. The market lookup is a safety net for a
# market we simply have not collected yet, and the price history only decorates a
# chart. Neither is worth spending a second attempt out of a shared 15s budget,
# and per hub-principles G4 an overrun degrades rather than blocks.
#
# Measured 2026-07-30 against the live endpoints: Gamma condition-id lookup
# 1.08-1.31s, CLOB prices-history at fidelity=60 0.47-1.25s (7/7 calls inside 5s).
# One 20s+ read timeout was observed at fidelity=180, which is why the ceiling is
# a hard cap and the failure path is "carry on without it" rather than a retry.
POLYMARKET_API_TIMEOUT_S = float(os.getenv("POLYMARKET_API_TIMEOUT_S", "5"))

# The READ half of the requests timeout tuple is what the value above sets.
# This is the CONNECT half, and the split matters:
#
#   `requests` does NOT accept a wall-clock deadline. A scalar `timeout=5`
#   applies 5s to the connect phase AND 5s to the read phase independently, and
#   the read timeout governs the gap between socket reads rather than the total
#   — so a server that dribbles bytes can hold a "5s" call open indefinitely.
#   Worst case for a scalar is therefore ~2x at minimum and unbounded at worst.
#
# Passing an explicit (connect, read) tuple makes the two phases separately
# legible, and a short connect timeout fails fast on an unreachable host instead
# of spending the read budget on a TCP handshake that is never going to complete.
# The wall-clock guarantee itself cannot come from here — it is enforced by the
# CALLER, which tracks elapsed time across calls and stops spending.
POLYMARKET_API_CONNECT_TIMEOUT_S = float(
    os.getenv("POLYMARKET_API_CONNECT_TIMEOUT_S", "2")
)

# The A3 path makes TWO calls (market lookup, then price history). Two
# independent 5s ceilings would put 10s of a shared 15s budget at risk, so the
# pair shares ONE 6s allowance: the history call gets 6s minus whatever the
# lookup actually spent.
#
# The asymmetry is deliberate and reflects what each call is worth. The lookup
# decides whether a forecast has a market benchmark at all; the history only
# decorates a chart. So when the budget runs short the history is what gets
# dropped — degrading to a missing chart, never to a failed forecast. A failed
# forecast is the worst outcome this system can produce; a chartless one is
# merely disappointing.
POLYMARKET_A3_COMBINED_BUDGET_S = float(
    os.getenv("POLYMARKET_A3_COMBINED_BUDGET_S", "6")
)

# Below this much remaining budget the history call is skipped outright rather
# than started. Measured floor is ~0.47s on a healthy response, so anything under
# a second is near-certain to time out — and a doomed request still costs the
# full wait before failing, which is time taken from nothing.
POLYMARKET_A3_MIN_HISTORY_S = float(os.getenv("POLYMARKET_A3_MIN_HISTORY_S", "1"))

# Resolution of the CLOB price history, in minutes per point. `interval=max` is a
# ROLLING 30-DAY WINDOW (verified 2026-07-30: markets created 2025-05-02 return
# exactly 30.0 days), so this directly sets the point count: 60 -> ~700 points,
# native (no fidelity) -> ~4,245. Each point becomes ONE Firestore doc in the
# predictionSeries subcollection, so this is also the per-forecast write count.
# Hourly over 30 days is a legible chart at ~2 batched commits; raise it to
# thin the series further.
POLYMARKET_HISTORY_FIDELITY_MIN = int(os.getenv("POLYMARKET_HISTORY_FIDELITY_MIN", "60"))
