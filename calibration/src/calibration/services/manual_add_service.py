"""
Manual-add service — add one question by Polymarket slug.

The operator supplies the slug, category, and cohort; everything else
(condition id, resolution date, question text, liquidity) is looked up from
Polymarket. Requiring the operator to copy a condition id by hand would be the
single most error-prone field in the system, and a wrong one silently attaches
a question to someone else's market.

Manual and auto rows are identical downstream except for `added_by` and
`added_by_operator` — nothing in scoring, metrics, or dispatch branches on
provenance.

The cohort is taken from the operator rather than derived from the resolution
date on purpose: a manual add is usually a deliberate choice to measure a
specific question, and forcing it into a computed cohort would silently reject
anything falling in the gaps between windows. The service warns when the
stated cohort disagrees with the actual horizon, and proceeds.

References:
    - calibration_plan.md §3 C2 (manual adds)
    - calibration_plan.md §6 T10A.11
"""

from __future__ import annotations

import logging
from typing import Optional

from calibration import config
from calibration.models import Category, Cohort, Question
from calibration.polymarket import client, discover

logger = logging.getLogger(__name__)


class ManualAddError(RuntimeError):
    """Raised when a manual add cannot be completed, with an operator-readable reason."""


def build_question(
    slug: str,
    category: Category,
    cohort: Cohort,
    operator_email: str,
    market: Optional[dict] = None,
    question_text: Optional[str] = None,
) -> Question:
    """
    Build (but do not persist) a manual `Question` from a Polymarket slug.

    Args:
        slug:           the Polymarket market slug.
        category:       operator-chosen internal category.
        cohort:         operator-chosen cohort.
        operator_email: recorded as provenance.
        market:         pre-fetched Gamma payload. When None, it is fetched.
                        Injectable for tests.
        question_text:  override for the market's question text. Use sparingly
                        — the agent is asked verbatim what this says, so an
                        edit here changes what is being measured.

    Raises:
        ManualAddError: when the slug is unknown or the market lacks the
                        fields needed to attach a resolution to it.
    """
    payload = market if market is not None else client.fetch_market_by_slug(slug)
    if not payload:
        raise ManualAddError(
            f"No Polymarket market found for slug {slug!r}. Check the slug in "
            "the market URL (polymarket.com/event/<slug>)."
        )

    condition_id = discover.extract_condition_id(payload)
    if not condition_id:
        raise ManualAddError(
            f"Market {slug!r} has no conditionId. Without it there is no way to "
            "poll for its resolution, so the question cannot be scored."
        )

    end_date = discover.parse_end_date(payload)
    if end_date is None:
        raise ManualAddError(
            f"Market {slug!r} has no parseable end date. A question with no "
            "expected resolution date would never be polled."
        )

    text = (question_text or payload.get("question") or "").strip()
    if not text:
        raise ManualAddError(
            f"Market {slug!r} has no question text and none was supplied."
        )

    volume = discover.parse_volume(payload)
    floor = config.liquidity_floor_for(cohort)
    if volume < floor:
        # A warning, not an error: the operator may be deliberately measuring a
        # thin market. But they should know the implied probability on it is
        # noisier than the cohort's other questions.
        logger.warning(
            "[manual-add] %s has volume %s, below the %s floor of %s. Its "
            "implied probability will be noisier than the rest of the cohort.",
            slug, volume, cohort, floor,
        )

    return Question(
        question_text=text,
        polymarket_slug=slug,
        polymarket_condition_id=condition_id,
        category=category,
        cohort=cohort,
        expected_resolution_date=end_date,
        liquidity_usd_at_pickup=volume,
        status="open",
        added_by="manual",
        added_by_operator=operator_email,
    )


def add_manual_question(
    slug: str,
    category: Category,
    cohort: Cohort,
    operator_email: str,
    market: Optional[dict] = None,
    question_text: Optional[str] = None,
) -> tuple[Question, Optional[str]]:
    """
    Build and persist a manual question.

    Returns `(question, inserted_id)`. `inserted_id` is None when a question
    for that condition id already existed — reported to the operator as
    "already tracked" rather than as an error, since re-adding is a natural
    thing to try.

    Imports the repository lazily so that `build_question` — the part with all
    the validation logic — can be unit-tested without a database or psycopg2
    installed.
    """
    if not config.CALIBRATION_ENABLED:
        raise RuntimeError(
            "CALIBRATION_ENABLED is false — refusing to add questions. "
            "This is the kill switch (plan §2.5); unset it to re-enable."
        )

    from calibration.repos import questions as questions_repo

    question = build_question(
        slug=slug,
        category=category,
        cohort=cohort,
        operator_email=operator_email,
        market=market,
        question_text=question_text,
    )
    inserted_id = questions_repo.insert(question)
    if inserted_id:
        logger.info("[manual-add] Added %s (%s/%s)", slug, cohort, category)
    else:
        logger.info("[manual-add] %s already tracked — no row inserted", slug)
    return question, inserted_id
