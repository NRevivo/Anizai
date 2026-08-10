"""
Taxonomy — Polymarket tag to internal category mapping (plan C4).

The rule, in one sentence: a market qualifies if at least one of its tags maps
to an allowed category and none of its tags is blocked.

Blocklist wins. A market tagged both "Politics" and "NBA" is a basketball
market with a political framing; its outcome is driven by a game, and the
agent's vaults (news, social, prediction-market momentum) carry no signal
about it. Including it would depress the calibration score for a reason that
has nothing to do with the agent's reasoning quality — which is the specific
failure mode this whole system exists to avoid producing.

Category precedence when several tags map: the first category encountered in
the fixed order (geopolitical, financial, ai) wins, so a market tagged both
"Politics" and "Economy" classifies as geopolitical. The order is a
convention, not a judgement about which matters more — what matters is that
it is deterministic, so the same market always lands in the same cohort
report.

References:
    - calibration_plan.md §3 C4 (category mapping)
    - calibration_plan.md §6 T10A.7
"""

from __future__ import annotations

import json
import os
from typing import Iterable, Optional

from calibration.models import Category

_TAXONOMY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "taxonomy.json")

# Deterministic precedence when a market's tags map to more than one category.
_CATEGORY_PRECEDENCE: tuple[Category, ...] = ("geopolitical", "financial", "ai")

_cache: Optional[dict] = None


def normalize_tag(tag: str) -> str:
    """
    Canonicalize a tag for lookup.

    Polymarket returns tags with inconsistent casing and separators across
    endpoints ("US-Current-Affairs", "us current affairs", "Crypto Prices").
    Lowercasing and collapsing whitespace/underscores to hyphens makes the
    JSON file readable with one spelling per concept instead of four.
    """
    return "-".join(tag.strip().lower().replace("_", " ").replace("-", " ").split())


def _load() -> dict:
    """Load and cache taxonomy.json."""
    global _cache
    if _cache is None:
        with open(_TAXONOMY_PATH, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        _cache = {
            "allow": {normalize_tag(k): v for k, v in raw.get("allow", {}).items()},
            "block": {normalize_tag(t) for t in raw.get("block", [])},
        }
    return _cache


def reset_cache() -> None:
    """Drop the cached taxonomy. For tests that patch the JSON on disk."""
    global _cache
    _cache = None


def allowed_tags() -> dict[str, Category]:
    """The normalized allowlist: tag -> category."""
    return dict(_load()["allow"])


def blocked_tags() -> set[str]:
    """The normalized blocklist."""
    return set(_load()["block"])


def is_blocked(tags: Iterable[str]) -> bool:
    """True if any tag is on the blocklist."""
    block = _load()["block"]
    return any(normalize_tag(t) in block for t in tags)


def classify(tags: Iterable[str]) -> Optional[Category]:
    """
    Map a market's tags to an internal category.

    Returns None when the market is blocked or when no tag is recognised.
    None means "do not use this market" in both cases — the distinction
    between "explicitly blocked" and "not in the allowlist" matters for
    debugging, not for the decision, and `is_blocked` is available when the
    caller wants to tell them apart.

    Args:
        tags: raw tag strings as Polymarket returned them.

    Returns:
        The internal category, or None if the market does not qualify.
    """
    tag_list = list(tags)
    if is_blocked(tag_list):
        return None

    allow = _load()["allow"]
    matched = {allow[n] for t in tag_list if (n := normalize_tag(t)) in allow}
    if not matched:
        return None

    for category in _CATEGORY_PRECEDENCE:
        if category in matched:
            return category
    # Unreachable while _CATEGORY_PRECEDENCE covers every value in the JSON;
    # returning a stable member rather than None keeps a future taxonomy edit
    # from silently dropping markets if someone adds a category here and
    # forgets the precedence tuple.
    return sorted(matched)[0]  # type: ignore[return-value]


def extract_tags(market: dict) -> list[str]:
    """
    Pull tag strings out of a Gamma market payload.

    Gamma is inconsistent about this field across endpoints and over time: it
    has returned `tags` as a list of strings, as a list of objects with a
    `label` or `slug`, and has used `category` as a bare string. Rather than
    pin one shape and break on the next upstream change, accept all of them
    and let an unrecognised shape yield no tags — which safely results in the
    market being skipped rather than miscategorised.
    """
    out: list[str] = []

    raw_tags = market.get("tags") or []
    if isinstance(raw_tags, list):
        for t in raw_tags:
            if isinstance(t, str):
                out.append(t)
            elif isinstance(t, dict):
                label = t.get("label") or t.get("slug") or t.get("name")
                if isinstance(label, str):
                    out.append(label)

    for key in ("category", "categoryLabel", "eventCategory"):
        value = market.get(key)
        if isinstance(value, str) and value:
            out.append(value)

    return out
