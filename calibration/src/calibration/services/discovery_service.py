"""
Discovery service — top the question pool up to its cohort targets.

Orchestrates: fetch active markets -> filter to candidates (discover.py) ->
work out how many each cohort still needs -> select the most liquid -> insert.

Two properties this service must have, and the reasons they matter:

    Idempotent. Discovery runs hourly in production. It inserts only markets
    it has not seen (UNIQUE on condition_id, ON CONFLICT DO NOTHING) and never
    removes anything. Re-running it ten times has the same effect as once.

    Additive only. It tops up toward the target; it never evicts a question to
    make room. An evicted question would take its in-flight forecasts with it
    and silently shrink the sample the calibration curve is built from.

The G8 ceiling (`CALIBRATION_MAX_OPEN_QUESTIONS`) is enforced here and, when
it bites, is reported rather than applied silently — a truncation nobody is
told about reads as full coverage.

References:
    - calibration_plan.md §3 C1, C3 (auto-selection, hourly top-up)
    - calibration_plan.md §3 G8 (ceilings)
    - calibration_plan.md §6 T10A.9
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from calibration import config
from calibration.models import COHORTS, MarketCandidate
from calibration.polymarket import client, discover
from calibration.repos import questions as questions_repo
from calibration.repos import runs as runs_repo

logger = logging.getLogger(__name__)


@dataclass
class DiscoveryReport:
    """
    What one discovery run did.

    Carries the rejection breakdown and the per-cohort shortfall because
    "found 3 questions" is not actionable on its own — the operator needs to
    know whether the pool was thin, the filters were tight, or the ceiling
    bit.
    """

    run_id: Optional[str] = None
    markets_fetched: int = 0
    candidates_found: int = 0
    inserted: int = 0
    already_present: int = 0
    rejections: dict[str, int] = field(default_factory=dict)
    open_before: dict[str, int] = field(default_factory=dict)
    needed: dict[str, int] = field(default_factory=dict)
    shortfall: dict[str, int] = field(default_factory=dict)
    truncated_by_ceiling: int = 0

    def summary_lines(self) -> list[str]:
        """Human-readable summary for the CLI."""
        lines = [
            f"markets fetched : {self.markets_fetched}",
            f"candidates      : {self.candidates_found}",
            f"inserted        : {self.inserted}",
            f"already present : {self.already_present}",
        ]
        if self.truncated_by_ceiling:
            lines.append(
                f"TRUNCATED       : {self.truncated_by_ceiling} candidate(s) not "
                f"inserted — CALIBRATION_MAX_OPEN_QUESTIONS="
                f"{config.CALIBRATION_MAX_OPEN_QUESTIONS} reached"
            )
        for cohort in COHORTS:
            short = self.shortfall.get(cohort, 0)
            note = f"  (SHORT by {short})" if short else ""
            lines.append(
                f"cohort {cohort:<7}: open_before={self.open_before.get(cohort, 0)} "
                f"needed={self.needed.get(cohort, 0)}{note}"
            )
        if self.rejections:
            ordered = sorted(self.rejections.items(), key=lambda kv: -kv[1])
            lines.append("rejections      : " + ", ".join(f"{k}={v}" for k, v in ordered))
        return lines


def compute_needed(open_counts: dict[str, int]) -> dict[str, int]:
    """
    How many more questions each cohort wants.

    Never negative: a cohort over its target (possible after manual adds) asks
    for zero rather than implying an eviction.
    """
    return {
        cohort: max(0, config.target_count_for(cohort) - open_counts.get(cohort, 0))
        for cohort in COHORTS
    }


def apply_ceiling(
    selected: list[MarketCandidate], total_open: int
) -> tuple[list[MarketCandidate], int]:
    """
    Trim the selection so it cannot push the open pool past the G8 ceiling.

    Returns `(kept, dropped_count)`. The caller reports `dropped_count`.
    """
    headroom = max(0, config.CALIBRATION_MAX_OPEN_QUESTIONS - total_open)
    if len(selected) <= headroom:
        return selected, 0
    return selected[:headroom], len(selected) - headroom


def fetch_cohort_windows(today: Optional[date] = None) -> list[dict]:
    """
    Fetch candidate markets by querying Gamma once per cohort window.

    Three narrow server-side queries instead of one broad scan. This is not an
    optimisation — it is the only approach that reaches the markets we want.
    Gamma refuses offsets past roughly 2000, so an unfiltered walk sees an
    arbitrary slice of the exchange; the first live run scanned 2000 markets
    and rejected 1652 of them purely for falling outside cohort windows the
    server could have filtered on directly.

    Each window also carries its own liquidity floor, so the long-horizon
    cohort's lower bar is applied by the server rather than after truncation.

    Duplicates across windows (a market cannot be in two windows, but a
    pathological end date could repeat) are removed by condition id.
    """
    collected: list[dict] = []
    seen: set[str] = set()

    for cohort in COHORTS:
        date_min, date_max = discover.window_bounds(cohort, today=today)
        floor = config.liquidity_floor_for(cohort)
        page = client.fetch_markets_in_window(
            end_date_min=date_min, end_date_max=date_max, volume_min=floor
        )
        fresh = 0
        for market in page:
            key = discover.extract_condition_id(market)
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            collected.append(market)
            fresh += 1
        logger.info(
            "[discovery] cohort %s window %s..%s (min volume $%s) -> %d market(s)",
            cohort, date_min[:10], date_max[:10], f"{floor:,}", fresh,
        )

    return collected


def run_discovery(
    triggered_by: str = "cli",
    today: Optional[date] = None,
    markets: Optional[list[dict]] = None,
) -> DiscoveryReport:
    """
    Execute one discovery cycle.

    Args:
        triggered_by: recorded on the run row ('cli', 'cloud_scheduler', or an
                      operator email).
        today:        reference date for cohort binning; defaults to UTC today.
                      Injectable for tests.
        markets:      pre-fetched raw market payloads. When None, the Gamma API
                      is called. Injecting them is how the integration test
                      runs the full service against a fixture with no network.

    Returns:
        A DiscoveryReport. Writes a `calibration_runs` row of type
        'initial_seed' recording the outcome.

    Raises:
        RuntimeError: if CALIBRATION_ENABLED is false.
    """
    if not config.CALIBRATION_ENABLED:
        raise RuntimeError(
            "CALIBRATION_ENABLED is false — refusing to run discovery. "
            "This is the kill switch (plan §2.5); unset it to re-enable."
        )

    report = DiscoveryReport()
    report.run_id = runs_repo.start(
        run_type="initial_seed",
        triggered_by=triggered_by,
        metadata={"stage": "discovery"},
    )

    try:
        raw_markets = markets if markets is not None else fetch_cohort_windows(today)
        report.markets_fetched = len(raw_markets)

        candidates, rejections = discover.find_candidates(raw_markets, today=today)
        report.candidates_found = len(candidates)
        report.rejections = rejections

        report.open_before = questions_repo.count_open_by_cohort()
        report.needed = compute_needed(report.open_before)

        selected = discover.select_for_cohorts(candidates, report.needed)

        total_open = sum(report.open_before.values())
        selected, dropped = apply_ceiling(selected, total_open)
        report.truncated_by_ceiling = dropped
        if dropped:
            logger.warning(
                "[discovery] Ceiling reached — %d candidate(s) not inserted "
                "(max_open=%d, currently_open=%d)",
                dropped, config.CALIBRATION_MAX_OPEN_QUESTIONS, total_open,
            )

        selected_per_cohort: dict[str, int] = {c: 0 for c in COHORTS}
        for candidate in selected:
            selected_per_cohort[candidate.cohort] += 1
            new_id = questions_repo.insert(candidate.to_question())
            if new_id:
                report.inserted += 1
                logger.info(
                    "[discovery] + %s [%s/%s] %s",
                    candidate.polymarket_condition_id[:12],
                    candidate.cohort, candidate.category,
                    candidate.question_text[:70],
                )
            else:
                report.already_present += 1

        report.shortfall = {
            cohort: max(0, report.needed.get(cohort, 0) - selected_per_cohort.get(cohort, 0))
            for cohort in COHORTS
        }
        for cohort, short in report.shortfall.items():
            if short:
                logger.warning(
                    "[discovery] Cohort %s short by %d — not enough qualifying "
                    "markets in the candidate pool.", cohort, short,
                )

        return report
    finally:
        # In a finally block so a crash mid-discovery still closes the run row
        # with whatever counts were reached, rather than leaving it open
        # forever and looking like a hung run.
        runs_repo.finish(
            report.run_id,
            questions_dispatched=report.inserted,
            metadata={
                "markets_fetched": report.markets_fetched,
                "candidates_found": report.candidates_found,
                "already_present": report.already_present,
                "truncated_by_ceiling": report.truncated_by_ceiling,
                "rejections": report.rejections,
                "shortfall": report.shortfall,
            },
        )
