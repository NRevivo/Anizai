"""
Sprint 20 T20.11 E2E driver — real Firestore + real OpenAI + real vault.

Single-shot script (not a pytest test). Mirrors the server-side
`createSession` write at `server/src/repositories/session.repository.ts:373-411`
against the real `anizai-ai` Firestore database. The worker (running in
another shell) picks up the resulting `forecastQueries` doc, runs the full
Sprint 19 + Sprint 20 graph, and writes back to `sessionResults/{id}` plus
the three subcollections under `sessions/{id}/`.

Test docs are deliberately left in Firestore after the run. The
session_id pattern `e2e-sprint20-<8hex>` is identifiable for later
deletion-by-prefix if cleanup is ever wanted.

Pre-conditions before running:
  1. Pre-flight P1-P4 green (run `python -m scripts.preflight_firestore`).
  2. The agent worker is running in another shell:
        python -m agent.worker
     and has logged "subscribed to forecastQueries pending listener".
  3. Postgres is up with Sprint 17 vault data.
  4. OPENAI_API_KEY is set in data-pipeline/infrastructure/.env.

Usage:
    data-pipeline\\venv\\Scripts\\python.exe -m tests.e2e.sprint20_e2e_run

Stop conditions (the script exits non-zero, surfaces the issue, no retry):
  - Driver fails to write the seed docs (auth issue surfaced through Admin SDK)
  - sessionResults/{id} doesn't appear within POLL_TIMEOUT_SECONDS
  - any §8.7.2 required field is missing or out-of-range
"""

from __future__ import annotations

import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from firebase_admin import firestore as fb_firestore

from agent.config import settings
from agent.firestore_client import get_db, init_app


# ==========================================================
# Constants
# ==========================================================

QUESTION = "Will the Fed cut rates by Q2 2026?"
USER_ID = "e2e-sprint20-test-user"
POLL_TIMEOUT_SECONDS = 60  # 2x NFR p95 of 30s — beyond this is investigation, not retry
POLL_INTERVAL_SECONDS = 1.0
FIXTURE_PATH = (
    Path(__file__).resolve().parents[1] / "fixtures" / "sprint20_e2e_sample.json"
)


# ==========================================================
# §8.7.2 contract validation
# ==========================================================

CONFIDENCE_LABELS = {"Low", "Moderate", "High"}


def validate_session_result(result: dict) -> list[str]:
    """Validate the SessionResult against §8.7.2 (Sprint 20 success path).

    Returns a list of human-readable failures; empty list means pass.
    """
    failures: list[str] = []

    # finalProbability ∈ [0, 1] float
    fp = result.get("finalProbability")
    if not isinstance(fp, (int, float)) or isinstance(fp, bool):
        failures.append(
            f"finalProbability is not a number: type={type(fp).__name__}, value={fp!r}"
        )
    elif not (0.0 <= float(fp) <= 1.0):
        failures.append(f"finalProbability={fp!r} is outside [0, 1]")

    # confidenceLabel ∈ {Low, Moderate, High}
    cl = result.get("confidenceLabel")
    if cl not in CONFIDENCE_LABELS:
        failures.append(
            f"confidenceLabel={cl!r} not in {sorted(CONFIDENCE_LABELS)}"
        )

    # reasoningChain length ≥ 4
    rc = result.get("reasoningChain")
    if not isinstance(rc, list):
        failures.append(f"reasoningChain is not a list: type={type(rc).__name__}")
    elif len(rc) < 4:
        failures.append(f"reasoningChain length={len(rc)} < 4")

    # keyFactors length ≥ 3
    kf = result.get("keyFactors")
    if not isinstance(kf, list):
        failures.append(f"keyFactors is not a list: type={type(kf).__name__}")
    elif len(kf) < 3:
        failures.append(f"keyFactors length={len(kf)} < 3")

    # marketProbability is None (KG-PHASE8-12 path)
    mp = result.get("marketProbability")
    if mp is not None:
        failures.append(
            f"marketProbability={mp!r} expected None (KG-PHASE8-12 deferral)"
        )

    # suggestedActions == [] (D8 — deferred to Sprint 25)
    sa = result.get("suggestedActions")
    if sa != []:
        failures.append(
            f"suggestedActions={sa!r} expected [] (D8 deferral)"
        )

    return failures


# ==========================================================
# Firestore helpers
# ==========================================================

def _seed_pending_query(db, session_id: str) -> None:
    """Mirror server.repositories.session.repository.ts:373-411 createSession."""
    batch = db.batch()
    session_ref = db.collection("sessions").document(session_id)
    query_ref = db.collection("forecastQueries").document(session_id)

    session_data = {
        "userId": USER_ID,
        "question": QUESTION,
        "title": None,
        "idempotencyKey": f"e2e-{uuid.uuid4().hex}",
        "status": "queued",
        "latestProbability": None,
        "latestConfidence": None,
        "followEnabled": False,
        "isFollowing": False,
        "canonicalKey": None,
        "errorCode": None,
        "errorMessage": None,
        "clarificationCandidates": None,
        "createdAt": fb_firestore.SERVER_TIMESTAMP,
        "updatedAt": fb_firestore.SERVER_TIMESTAMP,
        "lastActivityAt": fb_firestore.SERVER_TIMESTAMP,
    }
    forecast_query_data = {
        "queryId": uuid.uuid4().hex,
        "sessionId": session_id,
        "userId": USER_ID,
        "question": QUESTION,
        "status": "pending",
        "createdAt": fb_firestore.SERVER_TIMESTAMP,
        "claimedAt": None,
        "claimedBy": None,
    }
    batch.set(session_ref, session_data)
    batch.set(query_ref, forecast_query_data)
    batch.commit()


def _read_subcollection(db, session_id: str, name: str) -> list[dict]:
    """Read all docs in sessions/{session_id}/{name}; return list of dicts
    with the doc id appended as 'id'. Excludes Firestore sentinel/timestamp
    values that aren't JSON-serializable; rely on the SDK's standard
    DatetimeWithNanoseconds which converts via isoformat-able paths."""
    coll = (
        db.collection("sessions").document(session_id).collection(name)
    )
    out = []
    for snap in coll.stream():
        data = snap.to_dict() or {}
        data["__doc_id"] = snap.id
        out.append(data)
    return out


# ==========================================================
# JSON serialization helpers
# ==========================================================

def _json_default(obj):
    """Make Firestore Timestamps + sentinels JSON-serializable."""
    # google.cloud.firestore_v1._helpers.DatetimeWithNanoseconds is a
    # datetime subclass; isoformat() works.
    if isinstance(obj, datetime):
        return obj.astimezone(timezone.utc).isoformat()
    if hasattr(obj, "rfc3339"):
        return obj.rfc3339()
    if hasattr(obj, "__str__"):
        return str(obj)
    raise TypeError(f"Not JSON serializable: {type(obj).__name__}")


# ==========================================================
# Driver
# ==========================================================

def main() -> int:
    print(f"[INFO] Sprint 20 T20.11 E2E driver starting")
    print(f"[INFO] Project: {settings.FIREBASE_PROJECT_ID}")
    print(f"[INFO] Question: {QUESTION!r}")

    init_app()
    db = get_db()

    session_id = f"e2e-sprint20-{uuid.uuid4().hex[:8]}"
    print(f"[INFO] session_id: {session_id}")

    print(f"[STEP] Writing sessions/{session_id} + forecastQueries/{session_id}...")
    t0 = time.monotonic()
    _seed_pending_query(db, session_id)
    print(f"[OK]   Seed write succeeded at t0=monotonic()")

    print(
        f"[STEP] Polling sessionResults/{session_id} every "
        f"{POLL_INTERVAL_SECONDS}s (timeout {POLL_TIMEOUT_SECONDS}s)..."
    )
    result_ref = db.collection("sessionResults").document(session_id)
    deadline = t0 + POLL_TIMEOUT_SECONDS
    elapsed = 0.0
    snapshot = None
    while time.monotonic() < deadline:
        snapshot = result_ref.get()
        if snapshot.exists:
            break
        time.sleep(POLL_INTERVAL_SECONDS)
    t_end = time.monotonic()
    elapsed = t_end - t0

    if not snapshot or not snapshot.exists:
        # Read session doc to surface what went wrong (status, errorCode).
        session_snap = db.collection("sessions").document(session_id).get()
        session_state = session_snap.to_dict() if session_snap.exists else None
        print(
            f"[FAIL] sessionResults/{session_id} did not appear within "
            f"{POLL_TIMEOUT_SECONDS}s. Elapsed={elapsed:.1f}s"
        )
        print(f"       sessions/{session_id} state: {session_state!r}")
        print(
            "       Inspect the worker shell for tracebacks; check Postgres "
            "is reachable from the worker."
        )
        return 8

    print(f"[OK]   sessionResults/{session_id} appeared after {elapsed:.2f}s")

    result = snapshot.to_dict() or {}
    evidence = _read_subcollection(db, session_id, "evidence")
    prediction_series = _read_subcollection(db, session_id, "predictionSeries")
    sentiment_series = _read_subcollection(db, session_id, "sentimentTimeSeries")
    print(
        f"[INFO] subcollection counts: evidence={len(evidence)}, "
        f"predictionSeries={len(prediction_series)}, "
        f"sentimentTimeSeries={len(sentiment_series)}"
    )

    # §8.7.2 validation
    failures = validate_session_result(result)
    if failures:
        print("[FAIL] SessionResult §8.7.2 validation failed:")
        for f in failures:
            print(f"       - {f}")
        # Still write the fixture so we can inspect what we got.

    # Persist fixture regardless — the artifact is useful for triage too.
    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    capture = {
        "session_id": session_id,
        "question": QUESTION,
        "user_id": USER_ID,
        "latency_seconds": round(elapsed, 3),
        "fired_at": datetime.now(timezone.utc).isoformat(),
        "validation": {
            "passed": not failures,
            "failures": failures,
        },
        "session_result": result,
        "evidence": evidence,
        "prediction_series": prediction_series,
        "sentiment_time_series": sentiment_series,
    }
    with FIXTURE_PATH.open("w", encoding="utf-8") as fh:
        json.dump(capture, fh, indent=2, default=_json_default, ensure_ascii=False)
    print(f"[OK]   fixture written: {FIXTURE_PATH}")

    # Headline summary
    print()
    print("=" * 60)
    print(f"latency_seconds   : {elapsed:.2f}s  (NFR p95 cap = 30s)")
    print(f"finalProbability  : {result.get('finalProbability')}")
    print(f"confidenceLabel   : {result.get('confidenceLabel')!r}")
    print(f"keyFactors count  : {len(result.get('keyFactors') or [])}")
    print(f"reasoningChain ct : {len(result.get('reasoningChain') or [])}")
    print(f"evidence count    : {len(evidence)}")
    print(f"validation        : {'PASS' if not failures else 'FAIL'}")
    print("=" * 60)

    return 0 if not failures else 9


if __name__ == "__main__":
    sys.exit(main())
