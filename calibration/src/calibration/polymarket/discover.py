"""
Discovery — which Polymarket markets qualify as calibration questions.

Pure functions. No I/O, no database. Takes raw Gamma market payloads and a
reference date; returns `MarketCandidate` objects. This is what makes the
filtering logic testable against a recorded fixture rather than a live API
whose contents change hourly.

The filter, in order:
    1. Structural   — the payload must have the fields we need
    2. Cohort       — days-to-resolution must fall in one of three windows
    3. Category     — tags must map to an allowed category (taxonomy.py)
    4. Liquidity    — cumulative volume must clear the cohort's floor

Cohort windows have gaps between them (9-12 days, 16-28 days) and that is
deliberate. A market resolving in 10 days is neither a "1 week" nor a
"2 week" question; forcing it into one blurs exactly the horizon comparison
the cohort split exists to make. Markets in the gaps are skipped.

References:
    - calibration_plan.md §3 C1 (auto-selection criteria)
    - calibration_plan.md §6 T10A.6
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Iterable, Optional

from calibration import config
from calibration.models import Cohort, MarketCandidate
from calibration.polymarket import taxonomy

logger = logging.getLogger(__name__)

# Inclusive day windows per cohort (plan C1). Gaps are intentional.
COHORT_WINDOWS: dict[Cohort, tuple[int, int]] = {
    "7d": (5, 9),
    "14d": (12, 16),
    "30-45d": (28, 46),
}


def window_bounds(cohort: Cohort, today: Optional[date] = None) -> tuple[str, str]:
    """
    The ISO date range to ask Gamma for when filling one cohort.

    Returns `(end_date_min, end_date_max)` as ISO-8601 UTC timestamps. The
    upper bound extends to the end of the last day in the window, because a
    market resolving at 23:59 on day 9 is still a day-9 market and a bare date
    bound would exclude it.

    This exists so the server does the date filtering. The alternative —
    fetching everything and binning client-side — cannot work: Gamma refuses
    offsets past ~2000, so an unfiltered scan sees an arbitrary slice of the
    exchange rather than all of it.
    """
    today = today or datetime.now(timezone.utc).date()
    low, high = COHORT_WINDOWS[cohort]
    start = today + timedelta(days=low)
    end = today + timedelta(days=high)
    return (
        f"{start.isoformat()}T00:00:00Z",
        f"{end.isoformat()}T23:59:59Z",
    )


def cohort_for_days(days: int) -> Optional[Cohort]:
    """
    Map days-to-resolution onto a cohort, or None if it falls in a gap.

    Windows are checked shortest-first and do not overlap, so the first match
    is the only match.
    """
    for cohort, (low, high) in COHORT_WINDOWS.items():
        if low <= days <= high:
            return cohort
    return None


def parse_end_date(market: dict) -> Optional[date]:
    """
    Extract the market's resolution date.

    Gamma has used several field names and two timestamp formats for this
    ('endDate', 'end_date_iso', 'endDateIso'), with and without a trailing Z.
    Rather than pin one and break on the next upstream change, try each in
    turn. Returns None when none parse — the caller skips the market, which is
    the safe outcome: a market with an unreadable end date cannot be binned
    into a cohort, and guessing would corrupt the horizon comparison.
    """
    for key in ("endDate", "end_date_iso", "endDateIso", "end_date"):
        raw = market.get(key)
        if not raw or not isinstance(raw, str):
            continue
        try:
            cleaned = raw.replace("Z", "+00:00")
            parsed = datetime.fromisoformat(cleaned)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).date()
        except ValueError:
            continue
    return None


def parse_volume(market: dict) -> Decimal:
    """
    Extract cumulative volume in USD, defaulting to 0.

    Gamma returns this as a string on some endpoints and a float on others,
    and occasionally as null for a brand-new market. A market with unknown
    volume is treated as zero volume, so it fails the liquidity floor and is
    skipped — the conservative direction.
    """
    for key in ("volumeNum", "volume", "volume_num", "liquidity"):
        raw = market.get(key)
        if raw in (None, ""):
            continue
        try:
            return Decimal(str(raw))
        except (InvalidOperation, ValueError):
            continue
    return Decimal(0)


def extract_condition_id(market: dict) -> str:
    """Condition id is the stable resolution key; slug is not."""
    return str(market.get("conditionId") or market.get("condition_id") or "").strip()


def evaluate_market(
    market: dict,
    today: Optional[date] = None,
) -> tuple[Optional[MarketCandidate], str]:
    """
    Decide whether one market qualifies.

    Returns `(candidate, reason)`. Exactly one of the two is meaningful:
    on acceptance the reason is "accepted"; on rejection the candidate is None
    and the reason names the failing filter.

    Returning the reason rather than a bare bool is what makes discovery
    debuggable: "we found 3 of 10 questions" is not actionable, whereas
    "47 blocked by category, 12 outside cohort windows, 5 below liquidity" is.

    Args:
        market: a raw Gamma market payload.
        today:  reference date for the cohort window; defaults to UTC today.
                Injectable so tests are not time-dependent.
    """
    today = today or datetime.now(timezone.utc).date()

    # --- 1. Structural ---
    question_text = str(market.get("question") or "").strip()
    if not question_text:
        return None, "no_question_text"

    condition_id = extract_condition_id(market)
    if not condition_id:
        return None, "no_condition_id"

    slug = str(market.get("slug") or "").strip()
    if not slug:
        return None, "no_slug"

    end_date = parse_end_date(market)
    if end_date is None:
        return None, "unparseable_end_date"

    # --- 2. Cohort window ---
    days = (end_date - today).days
    if days < 0:
        return None, "already_past_end_date"
    cohort = cohort_for_days(days)
    if cohort is None:
        return None, f"outside_cohort_windows({days}d)"

    # --- 3. Category ---
    tags = taxonomy.extract_tags(market)
    if taxonomy.is_blocked(tags):
        return None, "blocked_category"
    category = taxonomy.classify(tags)
    if category is None:
        return None, "unrecognised_category"

    # --- 4. Liquidity ---
    volume = parse_volume(market)
    floor = config.liquidity_floor_for(cohort)
    if volume < floor:
        return None, f"below_liquidity_floor({volume}<{floor})"

    return (
        MarketCandidate(
            question_text=question_text,
            polymarket_slug=slug,
            polymarket_condition_id=condition_id,
            category=category,
            cohort=cohort,
            expected_resolution_date=end_date,
            volume_usd=volume,
            days_to_resolution=days,
        ),
        "accepted",
    )


def find_candidates(
    markets: Iterable[dict],
    today: Optional[date] = None,
) -> tuple[list[MarketCandidate], dict[str, int]]:
    """
    Filter a batch of raw markets into candidates.

    Returns `(candidates, rejection_counts)`. The counts are keyed by the
    reason string from `evaluate_market` and are logged by the caller — see
    that function's docstring for why they are worth carrying.

    Candidates are sorted by volume descending within the result, so a caller
    that takes the first N per cohort gets the most liquid markets, which are
    the ones with the least noise in their implied probabilities.
    """
    candidates: list[MarketCandidate] = []
    rejections: dict[str, int] = {}

    for market in markets:
        candidate, reason = evaluate_market(market, today=today)
        if candidate is not None:
            candidates.append(candidate)
        else:
            # Collapse parameterised reasons so the counter stays readable.
            key = reason.split("(", 1)[0]
            rejections[key] = rejections.get(key, 0) + 1

    candidates.sort(key=lambda c: c.volume_usd, reverse=True)
    return candidates, rejections


def select_for_cohorts(
    candidates: Iterable[MarketCandidate],
    needed: dict[str, int],
) -> list[MarketCandidate]:
    """
    Take the top-N most liquid candidates per cohort.

    Args:
        candidates: pre-filtered candidates, ideally already volume-sorted.
        needed:     cohort -> how many more questions that cohort wants.
                    A cohort at or over target passes 0 and gets nothing.

    Returns:
        The selection, in cohort display order. Never returns more than
        `needed[cohort]` for any cohort, and silently returns fewer when the
        candidate pool is thin — the caller reports the shortfall, because a
        cohort that could not be filled is a real finding about market
        availability, not an error.
    """
    by_cohort: dict[str, list[MarketCandidate]] = {}
    for candidate in candidates:
        by_cohort.setdefault(candidate.cohort, []).append(candidate)

    selected: list[MarketCandidate] = []
    for cohort in ("7d", "14d", "30-45d"):
        want = max(0, needed.get(cohort, 0))
        pool = sorted(by_cohort.get(cohort, []), key=lambda c: c.volume_usd, reverse=True)
        selected.extend(pool[:want])
    return selected
