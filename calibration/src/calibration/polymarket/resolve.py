"""
Resolution parsing — what a CLOB market payload says about the outcome.

Pure function over a payload. No I/O; `client.fetch_clob_market` does that.

This module produces the ground truth every Brier score is measured against,
so its bias is deliberate and one-directional: **when in doubt, report
UNRESOLVED**. A market wrongly called still-open costs one polling cycle. A
market wrongly called YES permanently corrupts the score of every forecast
attached to it, and does so invisibly — the number still looks like a number.

Detection follows plan D1. A market is resolved when:

    (a) it is closed AND exactly one outcome is flagged the winner; or
    (b) it is closed AND outcomePrices has settled to [1, 0] or [0, 1] and
        has been closed for longer than the settle window.

Path (b) exists because Polymarket does not always populate a `winner` flag,
but it is guarded by the settle window: prices can briefly touch 1.0/0.0
during the final minutes of an active market without that being a settlement.
Reading such a moment as resolution would assign ground truth to a market
that has not actually resolved.

AMBIGUOUS is reported for disputed, void, or invalid markets. It is recorded
for audit and excluded from every aggregation (plan D2) — never coerced to
0.0, which would silently score every attached forecast as though the answer
had been NO.

References:
    - calibration_plan.md §3 D1-D4
    - calibration_plan.md §6 T10A.8
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)

# How long a market must have been closed before a [1,0] price pattern with no
# explicit winner flag is trusted as a settlement (plan D1).
SETTLE_WINDOW = timedelta(hours=24)

# How close to 1.0 / 0.0 prices must be to count as settled. Not exact
# equality: Polymarket returns these as strings that have been observed as
# "0.999" and "1" for the same settled state.
_PRICE_EPSILON = Decimal("0.01")


class ResolutionReading(BaseModel):
    """
    The verdict on one CLOB payload.

    `resolved` False means "no ground truth yet" — the caller should poll
    again later. `outcome` is meaningful only when `resolved` is True.
    `detail` always explains the verdict and is what the CLI prints and the
    logs record; a resolver that says "not resolved" without saying why is
    impossible to debug against a market the operator can see has settled.
    """

    resolved: bool
    outcome: Optional[str] = None            # 'YES' | 'NO' | 'AMBIGUOUS'
    outcome_numeric: Optional[Decimal] = None
    resolved_at: Optional[datetime] = None
    detail: str
    raw: dict[str, Any] = {}


def _to_decimal(value: Any) -> Optional[Decimal]:
    """Best-effort Decimal conversion; None when the value is unusable."""
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _parse_ts(value: Any) -> Optional[datetime]:
    """Parse an ISO timestamp into an aware UTC datetime, or None."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_yes(label: Any) -> bool:
    return isinstance(label, str) and label.strip().lower() in {"yes", "true"}


def _is_no(label: Any) -> bool:
    return isinstance(label, str) and label.strip().lower() in {"no", "false"}


def _extract_prices(market: dict) -> list[Decimal]:
    """
    Pull outcome prices out of the payload.

    CLOB returns this as a list of strings, a list of floats, or a
    JSON-encoded string depending on the endpoint and the era.
    """
    raw = market.get("outcomePrices") or market.get("outcome_prices")
    if isinstance(raw, str):
        import json
        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    if not isinstance(raw, list):
        return []
    prices = [_to_decimal(p) for p in raw]
    return [p for p in prices if p is not None]


def _closed_at(market: dict) -> Optional[datetime]:
    for key in ("closedTime", "closed_time", "endDate", "end_date_iso", "updatedAt"):
        ts = _parse_ts(market.get(key))
        if ts is not None:
            return ts
    return None


def parse_resolution(
    market: Optional[dict],
    now: Optional[datetime] = None,
) -> ResolutionReading:
    """
    Determine whether a CLOB market payload represents a resolved market.

    Args:
        market: the CLOB payload, or None when the market 404'd.
        now:    reference time for the settle-window guard; defaults to UTC
                now. Injectable so tests are not time-dependent.

    Returns:
        A ResolutionReading. `resolved=False` is a normal, expected answer.
    """
    now = now or datetime.now(timezone.utc)

    if not market:
        return ResolutionReading(
            resolved=False, detail="no_payload (market not found on CLOB)", raw={}
        )

    # --- Ambiguity first: a disputed market is not resolvable ground truth,
    # regardless of what its winner flags or prices say. Checking this before
    # the winner path prevents a disputed-but-flagged market from being
    # recorded as clean ground truth.
    if market.get("disputed") is True:
        return ResolutionReading(
            resolved=True,
            outcome="AMBIGUOUS",
            outcome_numeric=None,
            resolved_at=_closed_at(market) or now,
            detail="disputed=true",
            raw=market,
        )
    if market.get("void") is True or market.get("invalid") is True:
        return ResolutionReading(
            resolved=True,
            outcome="AMBIGUOUS",
            outcome_numeric=None,
            resolved_at=_closed_at(market) or now,
            detail="market void/invalid",
            raw=market,
        )

    is_closed = bool(market.get("closed"))
    if not is_closed:
        return ResolutionReading(resolved=False, detail="market still open", raw=market)

    # --- Path (a): an explicit winner flag on exactly one outcome.
    tokens = market.get("tokens") or market.get("outcomes") or []
    if isinstance(tokens, list):
        winners = [
            t for t in tokens
            if isinstance(t, dict) and t.get("winner") is True
        ]
        if len(winners) == 1:
            label = winners[0].get("outcome") or winners[0].get("label")
            if _is_yes(label):
                return ResolutionReading(
                    resolved=True, outcome="YES", outcome_numeric=Decimal("1.0"),
                    resolved_at=_closed_at(market) or now,
                    detail="winner flag on YES outcome", raw=market,
                )
            if _is_no(label):
                return ResolutionReading(
                    resolved=True, outcome="NO", outcome_numeric=Decimal("0.0"),
                    resolved_at=_closed_at(market) or now,
                    detail="winner flag on NO outcome", raw=market,
                )
            return ResolutionReading(
                resolved=True, outcome="AMBIGUOUS", outcome_numeric=None,
                resolved_at=_closed_at(market) or now,
                detail=f"winner outcome label not YES/NO: {label!r}", raw=market,
            )
        if len(winners) > 1:
            return ResolutionReading(
                resolved=True, outcome="AMBIGUOUS", outcome_numeric=None,
                resolved_at=_closed_at(market) or now,
                detail=f"{len(winners)} outcomes flagged winner", raw=market,
            )

    # --- Path (b): settled prices, guarded by the settle window.
    prices = _extract_prices(market)
    if len(prices) == 2:
        yes_price, no_price = prices[0], prices[1]
        settled_yes = abs(yes_price - Decimal(1)) <= _PRICE_EPSILON and no_price <= _PRICE_EPSILON
        settled_no = abs(no_price - Decimal(1)) <= _PRICE_EPSILON and yes_price <= _PRICE_EPSILON

        if settled_yes or settled_no:
            closed_at = _closed_at(market)
            if closed_at is None:
                return ResolutionReading(
                    resolved=False,
                    detail="prices settled but no close timestamp — cannot apply "
                           "settle-window guard, waiting",
                    raw=market,
                )
            if now - closed_at < SETTLE_WINDOW:
                remaining = SETTLE_WINDOW - (now - closed_at)
                return ResolutionReading(
                    resolved=False,
                    detail=f"prices settled but within settle window "
                           f"({remaining} remaining)",
                    raw=market,
                )
            return ResolutionReading(
                resolved=True,
                outcome="YES" if settled_yes else "NO",
                outcome_numeric=Decimal("1.0") if settled_yes else Decimal("0.0"),
                resolved_at=closed_at,
                detail="settled outcomePrices past settle window",
                raw=market,
            )

    return ResolutionReading(
        resolved=False,
        detail="closed but no winner flag and prices not settled",
        raw=market,
    )
