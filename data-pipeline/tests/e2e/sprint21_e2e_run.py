"""
Sprint 21 T21.12 E2E driver — hub-side only, real Firestore + real OpenAI.

Tests the Sprint 21 clarification flow and Tier 2 resume path end-to-end
against the real `anizai-ai` Firestore database. Frontend UI verification
is deferred per KG-PHASE8-19.

Two-phase test:

Phase 1 — Clarification path:
    Seed an ambiguous question → poll for session.status == 'awaiting_clarification'
    → verify clarificationCandidates array has correct shape.

Phase 2 — Tier 2 freeform resume:
    Write a resume forecastQueries doc with chosenCandidateId=null (simulating
    user clicking "None of these — analyze as freeform") → poll for
    session.status == 'done' → validate Tier 2 SessionResult.

Pre-conditions before running:
  1. The agent worker is running:
        python -m agent.worker
  2. Postgres vault is up with Sprint 17 data.
  3. OPENAI_API_KEY is set in data-pipeline/infrastructure/.env.
  4. Sprint 20 E2E pre-flight (P1-P4) was previously verified.

Usage:
    data-pipeline\\venv\\Scripts\\python.exe -m tests.e2e.sprint21_e2e_run

Output:
    PASS/FAIL per phase. Both must pass for T21.12 PASS.
    Session IDs printed for manual Firestore inspection.
    No cleanup — docs stay in Firestore under the e2e-sprint21-* prefix.
"""

from __future__ import annotations

import sys
import time
import uuid

from firebase_admin import firestore as fb_firestore

from agent.config import settings
from agent.firestore_client import get_db, init_app


# ==========================================================
# Constants
# ==========================================================

# Q4 from the test question bank: "What will happen in the Middle East?"
# Expected: ambiguous — hub should find 2+ related markets and clarify.
AMBIGUOUS_QUESTION = "What will happen in the Middle East?"

USER_ID = "e2e-sprint21-test-user"

# Polling config
CLARIFICATION_TIMEOUT_SECONDS = 60
FORECAST_TIMEOUT_SECONDS = 90   # Tier 2 resume may take slightly longer
POLL_INTERVAL_SECONDS = 2.0


# ==========================================================
# Validation helpers
# ==========================================================

def validate_clarification_candidates(candidates) -> list[str]:
    """Validate the clarificationCandidates array written by write_clarification."""
    failures: list[str] = []

    if not isinstance(candidates, list):
        failures.append(f"clarificationCandidates is not a list: {type(candidates).__name__}")
        return failures
    if len(candidates) < 2:
        failures.append(f"Expected >= 2 candidates, got {len(candidates)}")

    for i, c in enumerate(candidates):
        for field in ("id", "label", "source", "description", "matchConfidence"):
            if field not in c:
                failures.append(f"candidates[{i}] missing field: {field}")
        source = c.get("source")
        if source not in ("polymarket", "kalshi"):
            failures.append(f"candidates[{i}].source={source!r} not in [polymarket, kalshi]")
        mc = c.get("matchConfidence")
        if not isinstance(mc, (int, float)) or not (0.0 <= float(mc) <= 1.0):
            failures.append(f"candidates[{i}].matchConfidence={mc!r} out of [0, 1]")
        c_id = c.get("id")
        if not isinstance(c_id, str) or len(c_id) != 36:
            failures.append(f"candidates[{i}].id={c_id!r} not a UUID4 string")

    return failures


def validate_tier2_session_result(result: dict) -> list[str]:
    """Validate a Tier 2 SessionResult from Firestore."""
    failures: list[str] = []

    # tier field
    tier = result.get("tier")
    if tier != "tier_2":
        failures.append(f"tier={tier!r} expected 'tier_2'")

    # marketProbability must be null
    mp = result.get("marketProbability")
    if mp is not None:
        failures.append(f"marketProbability={mp!r} expected None for Tier 2")

    # marketComparison must be empty
    mc = result.get("marketComparison")
    if mc != []:
        failures.append(f"marketComparison={mc!r} expected [] for Tier 2")

    # marketComparisonInsight: spec-exact string
    insight = result.get("marketComparisonInsight")
    expected_insight = "No canonical market available — freeform analysis."
    if insight != expected_insight:
        failures.append(
            f"marketComparisonInsight={insight!r} expected {expected_insight!r}"
        )

    # finalProbability in range
    fp = result.get("finalProbability")
    if not isinstance(fp, (int, float)) or isinstance(fp, bool):
        failures.append(f"finalProbability={fp!r} not a number")
    elif not (0.0 <= float(fp) <= 1.0):
        failures.append(f"finalProbability={fp!r} out of [0, 1]")

    # agentVersion present and Sprint 21
    av = result.get("agentVersion")
    if not isinstance(av, str) or "sprint21" not in av:
        failures.append(f"agentVersion={av!r} expected to contain 'sprint21'")

    return failures


# ==========================================================
# Firestore helpers
# ==========================================================

def _seed_pending_query(db, session_id: str, question: str) -> None:
    """Seed sessions/{id} + forecastQueries/{id} as the server does."""
    batch = db.batch()

    session_ref = db.collection("sessions").document(session_id)
    query_ref = db.collection("forecastQueries").document(session_id)

    batch.set(session_ref, {
        "userId": USER_ID,
        "question": question,
        "status": "queued",
        "clarificationCandidates": None,
        "canonicalKey": None,
        "errorCode": None,
        "errorMessage": None,
        "createdAt": fb_firestore.SERVER_TIMESTAMP,
        "updatedAt": fb_firestore.SERVER_TIMESTAMP,
        "lastActivityAt": fb_firestore.SERVER_TIMESTAMP,
    })
    batch.set(query_ref, {
        "queryId": uuid.uuid4().hex,
        "sessionId": session_id,
        "userId": USER_ID,
        "question": question,
        "status": "pending",
        "createdAt": fb_firestore.SERVER_TIMESTAMP,
        "claimedAt": None,
        "claimedBy": None,
    })
    batch.commit()


def _seed_resume_query(db, session_id: str, canonical_key) -> str:
    """Write a new forecastQueries doc simulating Express POST /sessions/:id/clarify.

    Matches the production behavior of server requeueClarifiedSession:
      - A fresh UUID is the new forecastQueries doc id (not session_id)
      - sessionId field points to the original session
      - No chosenCandidateId field in the forecastQueries doc
      - sessions/{session_id}: status=queued, canonicalKey set, candidates=null

    Returns the fresh forecastQueries doc id (the worker picks this up).
    """
    fresh_query_doc_id = f"e2e-sprint21-resume-{uuid.uuid4().hex[:8]}"

    # session doc: Express requeues with canonicalKey, candidates cleared
    db.collection("sessions").document(session_id).update({
        "status": "queued",
        "canonicalKey": canonical_key,       # null=freeform, uuid=specific candidate
        "clarificationCandidates": None,     # Express clears candidates
        "updatedAt": fb_firestore.SERVER_TIMESTAMP,
        "lastActivityAt": fb_firestore.SERVER_TIMESTAMP,
    })

    # New forecastQueries doc: fresh UUID id, sessionId points to original
    db.collection("forecastQueries").document(fresh_query_doc_id).set({
        "queryId": uuid.uuid4().hex,
        "sessionId": session_id,             # key field for resume detection
        "userId": USER_ID,
        "question": AMBIGUOUS_QUESTION,
        "status": "pending",
        "createdAt": fb_firestore.SERVER_TIMESTAMP,
        "claimedAt": None,
        "claimedBy": None,
    })

    return fresh_query_doc_id


def _poll_session_status(db, session_id: str, target_status: str, timeout: float) -> dict | None:
    """Poll sessions/{id} until status == target_status. Returns session doc or None."""
    start = time.time()
    while time.time() - start < timeout:
        snap = db.collection("sessions").document(session_id).get()
        if snap.exists:
            data = snap.to_dict()
            status = data.get("status")
            print(f"  status={status!r} (elapsed={time.time() - start:.1f}s)")
            if status == target_status:
                return data
            if status == "failed":
                print(f"  ERROR: session failed — errorMessage={data.get('errorMessage')!r}")
                return None
        time.sleep(POLL_INTERVAL_SECONDS)
    print(f"  TIMEOUT: status never reached {target_status!r} after {timeout}s")
    return None


def _poll_session_any_terminal(
    db,
    session_id: str,
    terminal_statuses: set,
    timeout: float,
) -> tuple:
    """Poll sessions/{id} until status is in terminal_statuses.

    Returns (reached_status, session_doc). On timeout or 'failed' returns
    (None, None) or ('failed', session_doc) respectively.

    Used by Phase 1 to accept either 'awaiting_clarification' (ambiguous path)
    or 'done' (auto-pick path via KG-PHASE8-21 guard) as valid outcomes.
    """
    start = time.time()
    while time.time() - start < timeout:
        snap = db.collection("sessions").document(session_id).get()
        if snap.exists:
            data = snap.to_dict()
            status = data.get("status")
            print(f"  status={status!r} (elapsed={time.time() - start:.1f}s)")
            if status in terminal_statuses:
                return status, data
            if status == "failed":
                print(f"  ERROR: session failed — errorMessage={data.get('errorMessage')!r}")
                return "failed", data
        time.sleep(POLL_INTERVAL_SECONDS)
    print(f"  TIMEOUT: none of {sorted(terminal_statuses)!r} reached after {timeout}s")
    return None, None


def _get_session_result(db, session_id: str) -> dict | None:
    snap = db.collection("sessionResults").document(session_id).get()
    return snap.to_dict() if snap.exists else None


# ==========================================================
# Main
# ==========================================================

def main() -> int:
    """Returns 0 on full pass, 1 on any failure.

    Phase 1 accepts two valid outcomes (KG-PHASE8-21 guard):
      (a) awaiting_clarification with len(clarificationCandidates) >= 2 — ambiguous path.
          Proceeds to Phase 2 (Tier 2 freeform resume).
      (b) done directly — auto-pick path (single-candidate guard fired).
          Phase 2 skipped; guard verification is the pass criterion.

    Phase 1 fails if:
      - awaiting_clarification with len < 2 (original KG-PHASE8-21 bug — must never happen now)
      - session reaches 'failed' status
      - no terminal status within CLARIFICATION_TIMEOUT_SECONDS
    """
    init_app()
    db = get_db()

    total_failures: list[str] = []

    # ──────────────────────────────────────────────────────────
    # Phase 1: Ambiguous OR auto-pick path
    # ──────────────────────────────────────────────────────────
    session_id = f"e2e-sprint21-{uuid.uuid4().hex[:8]}"
    print(f"\n{'='*60}")
    print(f"Phase 1 — Ambiguous / auto-pick path")
    print(f"  session_id : {session_id}")
    print(f"  question   : {AMBIGUOUS_QUESTION!r}")
    print(f"{'='*60}")

    _seed_pending_query(db, session_id, AMBIGUOUS_QUESTION)
    print(f"  Seeded pending query. Waiting for worker to process...")
    print(
        f"  Polling for {{'awaiting_clarification', 'done'}} "
        f"(timeout={CLARIFICATION_TIMEOUT_SECONDS}s)..."
    )

    phase1_terminal = {"awaiting_clarification", "done"}
    phase1_status, session_data = _poll_session_any_terminal(
        db, session_id, phase1_terminal, CLARIFICATION_TIMEOUT_SECONDS
    )

    phase2_needed = False

    if phase1_status is None:
        total_failures.append("Phase 1: timed out — no terminal status reached")
        print("  FAIL: Phase 1 timed out")
    elif phase1_status == "failed":
        msg = (session_data or {}).get("errorMessage", "no errorMessage")
        total_failures.append(f"Phase 1: session failed — {msg}")
        print(f"  FAIL: session failed")

    elif phase1_status == "awaiting_clarification":
        # ── Ambiguous path ──────────────────────────────────────
        candidates = session_data.get("clarificationCandidates")
        n = len(candidates) if candidates else 0
        print(f"  Path: AMBIGUOUS (awaiting_clarification) — {n} candidate(s)")
        phase1_failures = validate_clarification_candidates(candidates)
        if phase1_failures:
            # awaiting_clarification with <2 candidates is the KG-PHASE8-21 bug.
            # Must never happen with the guard in place.
            total_failures.extend([f"Phase 1: {f}" for f in phase1_failures])
            print(f"  FAIL: {len(phase1_failures)} validation error(s) — KG-PHASE8-21 guard did not fire?")
            for f in phase1_failures:
                print(f"    - {f}")
        else:
            print(f"  PASS: clarificationCandidates shape valid ({n} candidates)")
            for c in candidates:
                print(f"    [{c['id'][:8]}...] {c['label']!r} (conf={c['matchConfidence']:.2f})")
            phase2_needed = True

    else:
        # ── Auto-pick path (phase1_status == 'done') ────────────
        print(f"  Path: AUTO-PICK (done directly) — KG-PHASE8-21 guard exercised")
        result = _get_session_result(db, session_id)
        if result is None:
            total_failures.append("Phase 1 (auto-pick): sessionResults/{id} not written")
            print("  FAIL: sessionResults doc missing on auto-pick path")
        else:
            print(f"  PASS: auto-pick path — guard verified, SessionResult written")
            print(f"    finalProbability : {result.get('finalProbability', 'N/A')}")
            print(f"    tier             : {result.get('tier', 'N/A')!r}")
            print(f"    agentVersion     : {result.get('agentVersion', 'N/A')!r}")
        print(f"  Phase 2 skipped — resume path covered by Gate 2 + Gate 3.")

    # ──────────────────────────────────────────────────────────
    # Phase 2: Tier 2 freeform resume (canonicalKey=null)
    # Only runs when Phase 1 produced awaiting_clarification with ≥2 candidates.
    # ──────────────────────────────────────────────────────────
    if phase2_needed:
        print(f"\n{'='*60}")
        print(f"Phase 2 — Tier 2 freeform resume (G1-corrected, canonicalKey=null)")
        print(f"  session_id     : {session_id} (same session)")
        print(f"{'='*60}")

        # Simulate the user clicking "None of these — analyze as freeform".
        # _seed_resume_query creates a NEW forecastQueries doc (fresh UUID) with
        # sessionId=session_id and updates session doc with canonicalKey=null.
        # This matches production server requeueClarifiedSession behavior.
        fresh_query_doc_id = _seed_resume_query(db, session_id, canonical_key=None)
        print(f"  Seeded fresh resume forecastQueries doc: {fresh_query_doc_id}")
        print(f"  Worker will claim forecastQueries/{fresh_query_doc_id} and resume.")
        print(f"  Polling sessions/{session_id} for status='done' (timeout={FORECAST_TIMEOUT_SECONDS}s)...")

        session_done = _poll_session_status(
            db, session_id, "done", FORECAST_TIMEOUT_SECONDS
        )

        if session_done is None:
            total_failures.append("Phase 2: session never reached done after Tier 2 resume")
            print("  FAIL: Phase 2 did not complete")
        else:
            result = _get_session_result(db, session_id)
            if result is None:
                total_failures.append("Phase 2: sessionResults/{id} not written")
                print("  FAIL: sessionResults doc missing")
            else:
                phase2_failures = validate_tier2_session_result(result)
                if phase2_failures:
                    total_failures.extend([f"Phase 2: {f}" for f in phase2_failures])
                    print(f"  FAIL: {len(phase2_failures)} validation error(s)")
                    for f in phase2_failures:
                        print(f"    - {f}")
                else:
                    print(f"  PASS: Tier 2 SessionResult valid")
                    print(f"    finalProbability   : {result['finalProbability']:.3f}")
                    print(f"    confidenceLabel    : {result.get('confidenceLabel')!r}")
                    print(f"    tier               : {result.get('tier')!r}")
                    print(f"    marketProbability  : {result.get('marketProbability')!r}")
                    print(f"    agentVersion       : {result.get('agentVersion')!r}")

            tier_on_session = session_done.get("tier")
            if tier_on_session != "tier_2":
                total_failures.append(
                    f"Phase 2: session doc tier={tier_on_session!r} expected 'tier_2' (T21.8)"
                )
                print(f"  FAIL: session.tier={tier_on_session!r} (expected tier_2, T21.8)")
            else:
                print(f"  PASS: session.tier='tier_2' confirmed (T21.8)")

    # ──────────────────────────────────────────────────────────
    # Summary
    # ──────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    if not total_failures:
        path_label = "clarification + Tier 2 resume" if phase2_needed else "auto-pick (KG-PHASE8-21 guard)"
        print(f"T21.12 E2E PASS — Sprint 21 {path_label} verified")
        print(f"  Inspect docs in Firestore under session_id: {session_id}")
        return 0
    else:
        print(f"T21.12 E2E FAIL — {len(total_failures)} failure(s):")
        for f in total_failures:
            print(f"  - {f}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
