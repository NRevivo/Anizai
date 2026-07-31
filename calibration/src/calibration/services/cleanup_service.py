"""
Orphan cleanup — the sweep the dispatch contract promised.

A dispatch writes two Firestore documents and then one Postgres row, in that
order. If the process dies between the first Firestore write and the second,
it leaves a `sessions/{id}` document in `queued` with no matching
`forecastQueries` entry and no Postgres row: nothing will ever claim it,
nothing will ever harvest it, and no metric will ever see it.

That is the *benign* failure direction, chosen deliberately over the
alternative (see `firestore_client.write_dispatch`). Benign is not the same as
tidy, though — left alone these accumulate silently in a collection that also
holds real users' sessions, and "silently accumulating rows in a shared
collection" is exactly the shape of thing that turns into an incident two
years later.

Three properties this module must have, and each is a deliberate choice:

  **It only ever considers documents calibration created.** Every query is
  scoped by `userId == "calibration-runner"`. There is no code path here that
  can name a document belonging to anyone else.

  **It cross-checks Postgres before deleting anything.** A session with a
  forecast row is live work, however old it looks. Only sessions with no row
  at all are orphans.

  **It defaults to a dry run.** `sweep()` reports; `sweep(apply=True)` acts.
  A deletion tool whose default is to delete is a tool that eventually deletes
  something it should not.

References:
    - calibration_plan.md §2.5 (the orphan sweep this implements)
    - calibration_plan.md §3 A6 (why the failure direction is benign)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from calibration import config, firestore_client

logger = logging.getLogger(__name__)

# How long a queued calibration session must sit before it is considered
# abandoned. Generously longer than any plausible agent run — a forecast the
# agent is genuinely still working on must never be swept out from under it.
ORPHAN_AGE_HOURS = 24


@dataclass
class CleanupReport:
    """What a sweep found, and what it did about it."""

    applied: bool = False
    scanned: int = 0
    orphans: int = 0
    deleted: int = 0
    errors: int = 0
    orphan_ids: list[str] = field(default_factory=list)

    def summary_lines(self) -> list[str]:
        lines = [
            f"mode            : {'APPLIED' if self.applied else 'dry run — nothing deleted'}",
            f"calibration sessions scanned : {self.scanned}",
            f"orphans found   : {self.orphans}",
        ]
        if self.applied:
            lines.append(f"deleted         : {self.deleted}")
        if self.errors:
            lines.append(f"errors          : {self.errors}")
        if self.orphans and not self.applied:
            lines.append("")
            lines.append("Re-run with --apply to delete them.")
        return lines


def _known_session_ids() -> set[str]:
    """Every session id Postgres has a forecast row for."""
    from calibration.db import get_cursor

    with get_cursor() as cur:
        cur.execute("SELECT session_id FROM calibration_forecasts;")
        return {row["session_id"] for row in cur.fetchall()}


def find_orphans(now: Optional[datetime] = None) -> list[dict[str, Any]]:
    """
    Find calibration sessions with no forecast row, older than the threshold.

    Returns a list of `{id, status, created_at}`.

    This is the one place in the package that queries a Firestore collection
    rather than reading a document by id, and it is why the query is pinned to
    `userId == "calibration-runner"` in the client rather than filtered here.
    A filter applied after the fetch would still have *fetched* other people's
    sessions.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=ORPHAN_AGE_HOURS)

    known = _known_session_ids()
    orphans: list[dict[str, Any]] = []

    for doc in firestore_client.list_calibration_sessions():
        if doc["id"] in known:
            continue
        if doc.get("status") not in (None, "queued"):
            # Past `queued` means a worker claimed it, so the dispatch
            # completed and this is not an orphan — it is a forecast whose
            # Postgres row is missing for some other reason, which is a
            # different problem and not one to solve by deleting evidence.
            continue
        created = doc.get("created_at")
        if created is None or created > cutoff:
            continue
        orphans.append(doc)

    return orphans


def sweep(apply: bool = False, now: Optional[datetime] = None) -> CleanupReport:
    """
    Find, and optionally delete, orphaned calibration sessions.

    Args:
        apply: False (default) reports only. True deletes.
        now:   reference time for the age threshold. Injectable for tests.
    """
    if not config.CALIBRATION_ENABLED:
        raise RuntimeError(
            "CALIBRATION_ENABLED is false — refusing to sweep. "
            "This is the kill switch (plan §2.5); unset it to re-enable."
        )

    report = CleanupReport(applied=apply)
    all_sessions = firestore_client.list_calibration_sessions()
    report.scanned = len(all_sessions)

    orphans = find_orphans(now=now)
    report.orphans = len(orphans)
    report.orphan_ids = [o["id"] for o in orphans]

    if not apply:
        for orphan in orphans:
            logger.info(
                "[cleanup] Would delete %s (queued since %s)",
                orphan["id"], orphan.get("created_at"),
            )
        return report

    for orphan in orphans:
        try:
            firestore_client.delete_calibration_session(orphan["id"])
            report.deleted += 1
            logger.info("[cleanup] Deleted orphan %s", orphan["id"])
        except Exception as exc:  # noqa: BLE001 — one failure must not stop the sweep
            report.errors += 1
            logger.error("[cleanup] Failed to delete %s: %s", orphan["id"], exc)

    return report
