"""
utils/llm_cost.py — pipeline LLM cost layer (Phase 7B.5-I, T1).

COPY of `agent/utils/llm_cost.py` (Sprint 23.5 Track 2), NOT an import.
Copy-not-import rationale (decision D1, 2026-07-01): a `processing/` →
`agent/` import would invert the A→B dependency direction and force the
Flink image to carry `agent/`. Copy-not-import is the established project
pattern; number-drift between the two copies is guarded by the
KG-PHASE-9.5-9 reconciliation gate — both copies must use the SAME
per-model figures, and that reconciliation is a named pre-test gate.

Exactly TWO deliberate deltas from the agent original (plan §2.4):

1. Pricing: `gpt-3.5-turbo` added to `_DEFAULT_PRICING` — the pipeline's
   translation model (processing/translation.py TRANSLATION_MODEL), which
   the agent never calls. Without it every translation call would price at
   $0.00 + warning. Env-overridable like the rest via the derived
   `LLM_COST_GPT_3_5_TURBO_*` names. (Deferred follow-up, NOT this sprint:
   migrating translation to gpt-4o-mini is a behavior change needing its
   own validation.)

2. Extended `record_usage(model, response, *, site, source_name=None,
   trace_id=None, run_id=None)` — emits the identical structured
   `llm_usage …` log line AND inserts one `llm_cost_events` row (via
   persistence/llm_cost_events, Section 3.3 Service Isolation). The insert
   is FAIL-OPEN: a DB error logs a warning and never raises — a
   cost-tracking failure must not fail message processing (mirror of the
   `compute_cost` unknown-model philosophy). Rationale for rows-not-
   accumulator: decision D2 — Flink has no per-request shared state.

Everything else is line-identical to the agent copy (pricing math,
env-override resolution, `extract_usage`, unknown-model → $0.00 + warning).

Units: per-1K-token USD, matching the OpenAI pricing sheet's native unit.

Env-overridability (inherited from the agent copy):
    Every default below can be overridden without a code change via a
    per-model env var. The env var name is derived from the model id:
    upper-cased, every non-alphanumeric run collapsed to a single
    underscore, prefixed `LLM_COST_` and suffixed `_INPUT_PER_1K` /
    `_OUTPUT_PER_1K`. Examples:

        gpt-4o                  → LLM_COST_GPT_4O_INPUT_PER_1K
                                  LLM_COST_GPT_4O_OUTPUT_PER_1K
        gpt-3.5-turbo           → LLM_COST_GPT_3_5_TURBO_INPUT_PER_1K ...
        text-embedding-3-small  → LLM_COST_TEXT_EMBEDDING_3_SMALL_INPUT_PER_1K

Unknown-model policy:
    `compute_cost` for a model not in the table logs a warning and returns
    0.0 — it NEVER raises. A pricing gap must not be able to fail message
    processing; a $0.00 line in the usage log is the visible signal that a
    new model was added without a price (the warning is the actionable
    half). Embedding models legitimately have no completion price (0.0).

Spec references:
    - docs/A_pipeline/plans/phase7b5i_filter_observability_and_cost.md §2.4, §2.5
    - agent/utils/llm_cost.py (the source of this copy — do NOT modify it here)
    - KG-PHASE-9.5-9 (cross-copy price reconciliation gate)
"""

from __future__ import annotations

import logging
import os
import re
from typing import Optional

logger = logging.getLogger(__name__)


# ==========================================================
# Authoritative price defaults
# ==========================================================
# Source: OpenAI public pricing sheet, pulled 2026-06-20 (agent copy) +
# gpt-3.5-turbo added 2026-07-02 (7B.5-I delta 1). Per-1K-token USD.
# (input_usd_per_1k, output_usd_per_1k).
#
# The four models are exactly the distinct models across the pipeline's
# runtime call sites (§2.5 tag registry):
#     gold_enrich / gold_consensus = gpt-4o        (via OPENAI_MODEL_NAME)
#     translate                    = gpt-3.5-turbo (TRANSLATION_MODEL)
#     gold_embed / rescue_embed    = text-embedding-3-small
# gpt-4o-mini is kept price-identical to the agent copy (KG-PHASE-9.5-9:
# both copies carry the SAME figures, even for models only one side calls).
#
# IMPORTANT — gpt-4o is a LEGACY / grandfathered rate (agent copy note):
#   still served via the API as of 2026-06-20, but on a sunset path
#   (snapshot gpt-4o-2024-05-13 shuts down 2026-10-23 → gpt-5.5). Any model
#   migration is a config change (env override or this table), not a rewrite.
_DEFAULT_PRICING: dict[str, tuple[float, float]] = {
    "gpt-4o":                 (0.0025, 0.0100),   # legacy/grandfathered; sunset 2026-10-23 → gpt-5.5
    "gpt-4o-mini":            (0.00015, 0.00060),  # parity with agent copy (KG-PHASE-9.5-9)
    "gpt-3.5-turbo":          (0.0005, 0.0015),    # 7B.5-I delta 1 — translation model
    "text-embedding-3-small": (0.00002, 0.0),      # embeddings have no output tokens
}


# ==========================================================
# Env-override resolution
# ==========================================================

def _env_key(model: str, side: str) -> str:
    """
    Derive the env-var name for a model's `input`/`output` per-1K price.

    Upper-cases the model id and collapses every run of non-alphanumeric
    characters to a single underscore, so `gpt-4o-mini` → `GPT_4O_MINI`.
    `side` is "INPUT" or "OUTPUT".
    """
    slug = re.sub(r"[^0-9A-Za-z]+", "_", model).strip("_").upper()
    return f"LLM_COST_{slug}_{side}_PER_1K"


def _resolve_price(model: str, default: tuple[float, float]) -> tuple[float, float]:
    """
    Return (input_per_1k, output_per_1k) for `model`, applying env overrides
    on top of the supplied default. A malformed env value is ignored (kept
    at the default) with a warning rather than crashing the cost layer.
    """
    input_default, output_default = default
    return (
        _read_env_float(_env_key(model, "INPUT"), input_default),
        _read_env_float(_env_key(model, "OUTPUT"), output_default),
    )


def _read_env_float(key: str, default: float) -> float:
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning(
            "[llm_cost] ignoring non-numeric override %s=%r — using default %s",
            key, raw, default,
        )
        return default


def get_pricing() -> dict[str, tuple[float, float]]:
    """
    Return the effective pricing table (defaults with env overrides applied).

    Resolved fresh on each call so an env change (e.g. a test monkeypatch or
    a future gpt-4o → gpt-5.5 reprice) takes effect without a reload. The
    keys are the model ids; values are (input_usd_per_1k, output_usd_per_1k).
    """
    return {
        model: _resolve_price(model, default)
        for model, default in _DEFAULT_PRICING.items()
    }


# ==========================================================
# Public helpers
# ==========================================================

def compute_cost(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> float:
    """
    Compute the USD cost of one LLM call.

    Args:
        model:             OpenAI model id (e.g. "gpt-4o", "gpt-3.5-turbo",
                           "text-embedding-3-small").
        prompt_tokens:     Input/prompt tokens consumed.
        completion_tokens: Output/completion tokens produced. Pass 0 for
                           embedding calls (they have no completion tokens).

    Returns:
        USD cost as a float. For an unknown model returns 0.0 (and logs a
        warning) — never raises, so a pricing gap cannot fail processing.

    cost = prompt_tokens/1000 * input_per_1k
         + completion_tokens/1000 * output_per_1k
    """
    default = _DEFAULT_PRICING.get(model)
    if default is None:
        logger.warning(
            "[llm_cost] unknown model %r — pricing it at $0.00. Add it to "
            "_DEFAULT_PRICING (or set %s / %s) so its cost is captured.",
            model, _env_key(model, "INPUT"), _env_key(model, "OUTPUT"),
        )
        return 0.0

    input_per_1k, output_per_1k = _resolve_price(model, default)
    prompt = int(prompt_tokens or 0)
    completion = int(completion_tokens or 0)
    return (prompt / 1000.0) * input_per_1k + (completion / 1000.0) * output_per_1k


def extract_usage(response: object) -> tuple[int, int, int]:
    """
    Pull (prompt_tokens, completion_tokens, total_tokens) off an OpenAI
    response's `.usage`, defaulting any missing field to 0.

    Shared by all runtime call sites so prompt/completion capture is
    defined once. Embedding responses carry `prompt_tokens` + `total_tokens`
    but no `completion_tokens` — that field defaults to 0, which is exactly
    what `compute_cost` wants for an embedding.
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0, 0, 0
    prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
    completion = int(getattr(usage, "completion_tokens", 0) or 0)
    total = int(getattr(usage, "total_tokens", 0) or 0)
    # Defensive: if total is absent but the split is present, reconstruct it
    # so the usage-log line is internally consistent.
    if total == 0 and (prompt or completion):
        total = prompt + completion
    return prompt, completion, total


def record_usage(
    model: str,
    response: object,
    *,
    site: str,
    source_name: Optional[str] = None,
    trace_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> tuple[int, float]:
    """
    Extract token usage, compute USD cost, emit the structured usage log
    line, AND insert one `llm_cost_events` row (7B.5-I delta 2).

    The log line is shaped identically to the agent copy's so both sides'
    usage records stay parseable by the same tooling. The DB insert is the
    pipeline-specific addition: one row per API call into `llm_cost_events`,
    aggregated by the two ROLLUP views (plan §2.3).

    Fail-open contract (plan §2.4): the insert catches ALL exceptions, logs
    a warning, and never raises — a cost-tracking failure must not turn a
    successful enrichment/translation/embedding into a processing failure.
    The log line above is emitted BEFORE the insert is attempted, so the
    per-call audit trail survives even when the DB write fails.

    Args:
        model:       OpenAI model id used for the call.
        response:    The OpenAI SDK response object (must expose `.usage`).
        site:        Stable call-site tag from the §2.5 registry
                     (gold_enrich / gold_consensus / translate /
                     gold_embed / rescue_embed).
        source_name: Producing source (newsapi / arxiv / telegram /
                     polymarket / hackernews / googletrends). Passed
                     per-call because the embedding helper is shared by 5
                     sources — a function-level tag alone would lose "who
                     is expensive" (§2.5).
        trace_id:    canonical_event_id of the processed object — joins
                     rescue_embed events to filter_rejects for wasted-spend
                     analysis (§2.3).
        run_id:      Explicit run tag; when None, resolved from
                     settings.RUN_ID (approved P3 — avoids threading RUN_ID
                     through every call site). Empty string → stored NULL.

    Returns:
        (total_tokens, cost_usd) — same contract as the agent copy.
    """
    prompt, completion, total = extract_usage(response)
    cost = compute_cost(model, prompt, completion)
    logger.info(
        "llm_usage site=%s model=%s prompt_tokens=%d completion_tokens=%d "
        "total_tokens=%d cost_usd=%.6f",
        site, model, prompt, completion, total, cost,
    )

    try:
        if run_id is None:
            # Lazy import: settings resolution stays off the module-import
            # path so pricing-only consumers (tests, tooling) need no config.
            from config.settings import RUN_ID as _settings_run_id
            run_id = _settings_run_id

        from persistence.llm_cost_events import insert_event
        insert_event(
            site=site,
            model=model,
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=total,
            cost_usd=cost,
            source_name=source_name,
            trace_id=trace_id,
            run_id=run_id or None,   # "" → NULL
        )
    except Exception as exc:
        logger.warning(
            "[llm_cost] llm_cost_events insert failed (fail-open, §2.4) "
            "site=%s model=%s: %s",
            site, model, exc,
        )

    return total, cost
