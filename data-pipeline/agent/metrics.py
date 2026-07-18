"""
agent/metrics.py — Prometheus metrics for the Agentic Hub (Sprint 26 T26.4).

Single home for the hub's Prometheus metric objects, registered on
prometheus_client's DEFAULT registry (agent/health.py's /metrics handler serves
them via generate_latest()). Defining them here — not in health.py — keeps the
metric DEFINITIONS decoupled from their EXPOSITION so any node can import and
update a metric without importing the HTTP layer (DRY, CLAUDE.md §3.2).

Three metrics ship in Sprint 26, each hung off a hook that ALREADY exists (no new
update mechanism — the agent_queue_depth gauge is deferred to Sprint 27, being
the only one that would need a poll/listener):

- agent_node_duration_seconds{node_name} (Histogram) — per-node wall-clock. Hung
  off the @events.emits decorator (agent/events.py), which wraps the 9 decorated
  pair-nodes, PLUS a manual .observe() in write_to_firestore.run() (not decorated;
  its latency is load-dependent — evidence subcollection size + the pre-`done`
  agentEvents drain). claim_session is deliberately NOT instrumented: its ~3-4
  fixed Firestore round-trips are load-independent bootstrap latency, not the
  load-varying tail this histogram exists to isolate (plan §2 / §3).
- agent_llm_cost_usd_total{model} (Counter) — cumulative USD across all LLM calls.
  Incremented in agent/utils/llm_cost.py record_usage (the AGENT copy — the single
  place model + cost_usd coexist per call; NOT state.total_cost_usd, which is one
  aggregate scalar with no per-model split).
- agent_session_total{tier,status} (Counter) — sessions by terminal outcome.
  Incremented at the `done` terminal (write_to_firestore, after step 6) and the
  `failed` terminal (process_query._mark_failed, after the no-downgrade guard).

Metric objects are module-level singletons; importing this module registers them
on the default registry exactly once. .observe()/.inc() never raise on valid
inputs (cost is always >= 0) and are off the critical path — a metrics update
must never break a node.

Spec: docs/B_hub/plans/sprint26_pretest_hardening.md 26.4 (§8.8.2, patched).
"""

from __future__ import annotations

from prometheus_client import Counter, Histogram

# Per-node wall-clock latency. Buckets (seconds) span the observed range: fast
# deterministic nodes (~0.1s) through synthesize (~15-20s) up to the ~60s p95
# forecast ceiling (KG-B-5, relaxed 2026-07-04). The 26.3 analysis reads these
# against that NFR.
NODE_DURATION_SECONDS = Histogram(
    "agent_node_duration_seconds",
    "Wall-clock duration of a forecast-graph node, labelled by node name.",
    ["node_name"],
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 20.0, 30.0, 45.0, 60.0),
)

# Cumulative USD cost of agent LLM calls, split by model. Fed from record_usage,
# the only site where model + cost_usd coexist per call.
LLM_COST_USD_TOTAL = Counter(
    "agent_llm_cost_usd_total",
    "Cumulative USD cost of agent LLM calls, labelled by model.",
    ["model"],
)

# Terminal session outcomes. `tier` is a real tier ("tier_1"/"tier_2") or "none"
# at the done terminal (tier is set by synthesize), and "unknown" at the failed
# terminal (_mark_failed has no state; a failure often predates tier inference).
SESSION_TOTAL = Counter(
    "agent_session_total",
    "Count of agent sessions by tier and terminal status (done | failed).",
    ["tier", "status"],
)
