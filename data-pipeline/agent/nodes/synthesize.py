"""
agent/nodes/synthesize.py — Node 6 of the forecast graph (placeholder, T19.9).

Placeholder synthesis node. Sprint 19's job is to wire the retrieval pipeline
end-to-end; real reasoning lands in Sprint 20 (T20.x). T19.10 will replace the
body of this module with the Sprint 18 stub helpers (`_build_stub_result`,
`_derive_*`, `_STUB_*`, `AGENT_VERSION`) relocated from `agent/process_query.py`,
producing a §8.7.2-compliant SessionResult dict. Sprint 20 then swaps that
stub for the real GPT-4o synthesis call.

The placeholder shape produced here intentionally does NOT match §8.7.2 — it
is purely a graph-contract satisfier. Tests assert the keys appear; T19.10
flips the body to the full SessionResult shape and updates downstream tests.

Why a separate node (not inline in vault_query):
    Synthesis is its own concern — turning evidence packages into a probability
    + narrative is unrelated to retrieval. agent-design P1 (single
    responsibility) makes this a hard split.

Service isolation (CLAUDE.md §3.3):
    Reads only from state. No I/O. T19.10 will keep this property — Firestore
    writes happen in Node 7 (`write_to_firestore`, deferred to Sprint 20+) or
    in the runner until then.

Spec references:
    - data-pipeline/docs/agentic_hub_spec.md §8.3.2 (Node 6 in topology)
    - data-pipeline/docs/agentic_hub_spec.md §8.7.2 (SessionResult schema —
      target shape for T19.10)
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# ==========================================================
# Public node function
# ==========================================================
def run(state: dict) -> dict:
    """
    Build a placeholder synthesis_result so the graph contract is satisfied.

    Sprint 19 contract: returns a `synthesis_result` dict with `placeholder=True`
    and a `evidence_counts` summary derived from the three retrieval evidence
    packages. T19.10 replaces this body with the full §8.7.2 SessionResult
    shape (finalProbability, confidence, bottomLineAnswer, ...).

    Args:
        state: Running ForecastState. Reads `researcher_evidence`,
               `pulse_evidence`, `market_evidence` if present.

    Returns:
        Partial state dict with `synthesis_result` populated.
    """
    researcher_evidence = state.get("researcher_evidence") or {}
    pulse_evidence = state.get("pulse_evidence") or {}
    market_evidence = state.get("market_evidence") or {}

    placeholder_result = {
        "placeholder": True,
        "sprint": "19",
        "evidence_counts": {
            "researcher_keys": len(researcher_evidence),
            "pulse_keys": len(pulse_evidence),
            "market_keys": len(market_evidence),
        },
    }

    logger.info(
        "synthesize: placeholder result built (researcher=%d pulse=%d market=%d)",
        len(researcher_evidence), len(pulse_evidence), len(market_evidence),
    )

    return {"synthesis_result": placeholder_result}
