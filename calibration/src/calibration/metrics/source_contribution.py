"""
Source contribution — which vault is actually predictive?

For each vault type, compare the mean Brier of forecasts where that vault
contributed evidence against the mean where it did not. A negative delta
(with-vault Brier lower than without) suggests the vault helps.

**This is a correlational metric and must be read as one.** The comparison is
observational: forecasts are not randomly assigned to vaults. If the momentum
vault only fires on questions that already have liquid prediction markets, and
those questions are easier, then "momentum helps" is a statement about which
questions it fires on, not about the vault's contribution. Deciding to
de-prioritise a vault on this number alone would be a mistake, and the payload
says so in a field rather than only in this docstring — the number will
outlive anyone's memory of the caveat.

Each vault's row carries both group sizes. A vault present in 40 of 42
forecasts has a "without" group of 2, and a delta computed against 2 samples
is meaningless however clean it looks.

References:
    - calibration_plan.md §3 E4
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

from calibration.evidence_projection import KNOWN_VAULT_TYPES
from calibration.metrics import brier

# Below this in EITHER group, the delta is not worth reading.
MIN_GROUP_SIZE = 5

INTERPRETATION_WARNING = (
    "Observational, not causal. Vaults are not randomly assigned to questions, "
    "so a vault's delta may reflect which questions it fires on rather than "
    "what it contributes. Do not de-prioritise a vault on this number alone."
)


def _vaults_of(summary: Optional[dict[str, Any]]) -> set[str]:
    """
    Which vaults contributed to one forecast, from its evidence projection.

    A missing or malformed summary yields an empty set — the forecast then
    counts in the "without" group for every vault, which is accurate: we have
    no evidence any vault contributed.
    """
    if not isinstance(summary, dict):
        return set()
    present = summary.get("vault_types_present")
    return set(present) if isinstance(present, list) else set()


def compute(
    rows: Iterable[tuple[Optional[dict[str, Any]], float, float]],
) -> dict:
    """
    Per-vault present-vs-absent Brier comparison.

    Args:
        rows: `(agent_evidence_summary, probability, outcome_numeric)` per
              scorable forecast.

    Returns:
        A payload with one item per known vault type — all of them, including
        vaults that never appeared. A vault that contributed to nothing is a
        finding (it is switched off, or its retrieval is failing), and
        omitting it would hide that.
    """
    scored: list[tuple[set[str], float]] = []
    for summary, probability, outcome in rows:
        score = brier.compute(float(probability), float(outcome))
        scored.append((_vaults_of(summary), score))

    items = []
    for vault in KNOWN_VAULT_TYPES:
        with_vault = [s for vaults, s in scored if vault in vaults]
        without_vault = [s for vaults, s in scored if vault not in vaults]

        mean_with = brier.mean(with_vault)
        mean_without = brier.mean(without_vault)
        delta = (
            mean_with - mean_without
            if mean_with is not None and mean_without is not None
            else None
        )

        items.append(
            {
                "vault_type": vault,
                "n_with": len(with_vault),
                "n_without": len(without_vault),
                "mean_brier_with": mean_with,
                "mean_brier_without": mean_without,
                # Negative = forecasts using this vault scored better.
                "delta": delta,
                "helps": (delta is not None and delta < 0),
                "comparable": (
                    len(with_vault) >= MIN_GROUP_SIZE
                    and len(without_vault) >= MIN_GROUP_SIZE
                ),
            }
        )

    return {
        "items": items,
        "total_forecasts": len(scored),
        "min_group_size": MIN_GROUP_SIZE,
        "interpretation": INTERPRETATION_WARNING,
    }


def render_ascii(payload: dict) -> list[str]:
    """Human-readable contribution table for the CLI."""
    lines = [
        f"{'vault':<16} {'n with':>7} {'n w/o':>7} {'Brier with':>11} "
        f"{'Brier w/o':>10} {'delta':>9}",
        "-" * 68,
    ]
    for item in payload["items"]:
        if item["delta"] is None:
            lines.append(
                f"{item['vault_type']:<16} {item['n_with']:>7} {item['n_without']:>7} "
                f"{'—':>11} {'—':>10} {'—':>9}"
            )
            continue
        flag = "" if item["comparable"] else f"  (groups < {payload['min_group_size']})"
        lines.append(
            f"{item['vault_type']:<16} {item['n_with']:>7} {item['n_without']:>7} "
            f"{item['mean_brier_with']:>11.4f} {item['mean_brier_without']:>10.4f} "
            f"{item['delta']:>+9.4f}{flag}"
        )
    lines.append("-" * 68)
    lines.append("negative delta = forecasts using that vault scored better")
    lines.append("")
    lines.append(payload["interpretation"])
    return lines
