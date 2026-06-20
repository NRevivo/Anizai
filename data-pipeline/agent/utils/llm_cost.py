"""
agent/utils/llm_cost.py — central LLM cost layer (Sprint 23.5, Track 2 / T23.5.8).

The 2026-06-18 audit (KG-B-6) found cost tracking structurally insufficient
across all four agent LLM call sites: each accumulated a single
`total_tokens` number, none split prompt/completion or knew any model's
price, and there was no cost-computation layer anywhere. A single
`total_tokens` value is unpriceable — output tokens cost ~3-4x input on
gpt-4o, and the four sites use three different models.

This module is the **one** place model prices live and the **one**
`compute_cost(...)` helper every call site routes through (hub-principles:
DRY for cost; agent-design D4 — central cost layer, not per-node copies).
Both the agent-side cost layer (this module) and the Gold-side pipeline
cost analysis (KG-PHASE-9.5-9) must use the SAME per-model figures — the
final reconciliation against 9.5-9 is a named pre-test gate, NOT a 23.5
task (Advisor↔Ron decision record §3). The 23.5 requirement is only that
the numbers are env-overridable and the defaults come from a real source.

Units: per-1K-token USD, matching the OpenAI pricing sheet's native unit.

Env-overridability (Advisor↔Ron §3):
    Every default below can be overridden without a code change via a
    per-model env var, so the future gpt-4o → gpt-5.5 swap (and the final
    9.5-9 reconciliation) is a config change, not a rewrite. The env var
    name is derived from the model id: upper-cased, every non-alphanumeric
    run collapsed to a single underscore, prefixed `LLM_COST_` and
    suffixed `_INPUT_PER_1K` / `_OUTPUT_PER_1K`. Examples:

        gpt-4o                  → LLM_COST_GPT_4O_INPUT_PER_1K
                                  LLM_COST_GPT_4O_OUTPUT_PER_1K
        gpt-4o-mini             → LLM_COST_GPT_4O_MINI_INPUT_PER_1K ...
        text-embedding-3-small  → LLM_COST_TEXT_EMBEDDING_3_SMALL_INPUT_PER_1K

Unknown-model policy:
    `compute_cost` for a model not in the table logs a warning and returns
    0.0 — it NEVER raises. A pricing gap must not be able to fail a
    forecast; a $0.00 line in the usage log is the visible signal that a
    new model was added without a price (the warning is the actionable
    half). Embedding models legitimately have no completion price (0.0).

Spec references:
    - data-pipeline/docs/B_hub/sprint23_5_pre26_remediation.md §3 (Track 2)
    - cabinet-outputs/advisor/problem-reports/sprint23_5_advisor-ron-decisions.md
      §3 (cost machinery now; exact numbers env-overridable; reconciliation
      is a pre-test gate) + §5 (gpt-4o legacy/sunset note)
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
# Source: OpenAI public pricing sheet, pulled 2026-06-20 (Advisor↔Ron
# decision record §3). Per-1K-token USD. (input_usd_per_1k, output_usd_per_1k).
#
# These three models are exactly the distinct models across the four
# instrumented sites:
#     query_understanding = gpt-4o-mini
#     evidence_rating     = gpt-4o-mini
#     synthesis           = gpt-4o
#     embedding           = text-embedding-3-small
#
# IMPORTANT — gpt-4o is a LEGACY / grandfathered rate (Advisor↔Ron §5):
#   gpt-4o and gpt-4o-mini are still served via the API as of 2026-06-20
#   (what was retired on 2026-02-17 was the separate `chatgpt-4o-latest`
#   variant, which this code does not use). However gpt-4o is on a sunset
#   path: snapshot gpt-4o-2024-05-13 shuts down 2026-10-23, recommended
#   replacement gpt-5.5. NO model migration happens in Sprint 23.5 — it
#   changes synthesis behavior and needs its own validation. When the swap
#   lands later, it is a config change (env override or this table) thanks
#   to the env-overridable design, not a rewrite.
_DEFAULT_PRICING: dict[str, tuple[float, float]] = {
    "gpt-4o":                 (0.0025, 0.0100),   # legacy/grandfathered; sunset 2026-10-23 → gpt-5.5
    "gpt-4o-mini":            (0.00015, 0.00060),  # also covers future followup / suggested_actions
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
# Public helper
# ==========================================================

def compute_cost(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> float:
    """
    Compute the USD cost of one LLM call.

    Args:
        model:             OpenAI model id (e.g. "gpt-4o", "gpt-4o-mini",
                           "text-embedding-3-small").
        prompt_tokens:     Input/prompt tokens consumed.
        completion_tokens: Output/completion tokens produced. Pass 0 for
                           embedding calls (they have no completion tokens).

    Returns:
        USD cost as a float. For an unknown model returns 0.0 (and logs a
        warning) — never raises, so a pricing gap cannot fail a forecast.

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

    Shared by all four call sites (T23.5.9) so prompt/completion capture is
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


def record_usage(model: str, response: object, *, site: str) -> tuple[int, float]:
    """
    Extract token usage, compute USD cost, emit the structured usage log
    line, and return `(total_tokens, cost_usd)` for state accumulation.

    The one place the per-call usage log line is shaped (T23.5.10), so all
    four sites (and the two future Sprint 24/25 nodes per T23.5.11) emit an
    identical, parseable record. The line carries `model`, `prompt_tokens`,
    `completion_tokens`, `total_tokens`, and `cost_usd` — exactly the fields
    Sprint 26 T26.4's Prometheus `agent_llm_cost_usd_total` counter reads.

    Args:
        model:    OpenAI model id used for the call.
        response: The OpenAI SDK response object (must expose `.usage`).
        site:     Short call-site name for the log line (e.g.
                  "query_understand", "synthesize").

    Returns:
        (total_tokens, cost_usd) — caller adds these to
        `state.total_tokens_used` / `state.total_cost_usd`.
    """
    prompt, completion, total = extract_usage(response)
    cost = compute_cost(model, prompt, completion)
    logger.info(
        "llm_usage site=%s model=%s prompt_tokens=%d completion_tokens=%d "
        "total_tokens=%d cost_usd=%.6f",
        site, model, prompt, completion, total, cost,
    )
    return total, cost
