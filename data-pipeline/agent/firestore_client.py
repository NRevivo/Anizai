"""
Firebase Admin SDK wrapper for the Agentic Hub.

Centralizes all Firestore access for the worker (Sections 8.7, 8.8). Other
hub modules MUST read/write Firestore exclusively through this module — no
`firebase_admin` imports outside this file. Keeps auth, transactions, and
collection paths in one place; makes mocking trivial in Gate 1 tests.

Lifecycle this wrapper supports:

    forecastQueries/{id}.status:  pending -> claimed -> done | failed
                                  (claim_query)         (update_query_status)

    sessions/{id}.status:         queued -> claimed -> running ->
                                  done | failed | awaiting_clarification
                                  (update_session_status, all transitions)

    sessionResults/{id}:          written once at end of work
                                  (write_session_result)

The doc shape this wrapper reads against is the server-side write at
server/src/repositories/session.repository.ts:335-347 (verified during
Sprint 18 T4 spot-check):

    forecastQueries/{sessionId} = {
        queryId, sessionId, userId, question,
        status: 'pending', createdAt,
        claimedAt: null, claimedBy: null,
    }

Spec references:
    - data-pipeline/docs/agentic_hub_spec.md §8.7.1 (Firestore document model)
    - data-pipeline/docs/agentic_hub_spec.md §8.8.1 (worker pattern, atomic claim)
    - data-pipeline/docs/anizai_handoff_consolidated.md §5.1 (SessionResult shape)
"""

import logging
import os
from typing import Optional

import firebase_admin
from firebase_admin import credentials, firestore
from google.cloud.firestore_v1 import Client, Transaction
from google.cloud.firestore_v1.base_query import FieldFilter

from agent.config import settings

logger = logging.getLogger(__name__)

# Re-export so other hub modules can stamp Firestore server-side timestamps
# without importing firebase_admin directly (CLAUDE.md §3.2 — Firestore
# access is centralized through this module).
SERVER_TIMESTAMP = firestore.SERVER_TIMESTAMP

# firestore.client() caches per-app internally; this module-level cache
# is just an explicit guard against repeated lookups.
_db: Optional[Client] = None


# ==========================================================
# Initialization
# ==========================================================

def init_app() -> None:
    """
    Idempotent Firebase Admin SDK init.

    Auth modes (mutually exclusive):
      - GOOGLE_APPLICATION_CREDENTIALS set → load service-account JSON
      - GOOGLE_APPLICATION_CREDENTIALS unset → Application Default
        Credentials (gcloud auth application-default login in dev)

    Safe to call multiple times — second and subsequent calls are no-ops.

    Raises:
        ValueError: FIREBASE_PROJECT_ID is missing or empty.
        FileNotFoundError: GOOGLE_APPLICATION_CREDENTIALS path does not exist.
        ValueError: service-account JSON is malformed or missing required
                    fields (private_key, client_email, etc.).
    """
    # firebase_admin._apps is the idiomatic idempotency check used in
    # Firebase Admin Python examples. Underscored but stable across
    # the 6.x line.
    if firebase_admin._apps:
        return

    project_id = settings.FIREBASE_PROJECT_ID
    if not project_id:
        raise ValueError(
            "FIREBASE_PROJECT_ID is required but missing or empty. "
            "Set it in data-pipeline/.env."
        )

    cred_path = settings.GOOGLE_APPLICATION_CREDENTIALS
    if cred_path:
        if not os.path.exists(cred_path):
            raise FileNotFoundError(
                f"GOOGLE_APPLICATION_CREDENTIALS is set to '{cred_path}' "
                "but no file exists at that path."
            )
        try:
            cred = credentials.Certificate(cred_path)
        except ValueError as e:
            raise ValueError(
                f"Service-account JSON at '{cred_path}' is malformed "
                f"or missing required fields: {e}"
            ) from e
        logger.info(
            "Initializing Firebase Admin with service-account JSON "
            "(project=%s, path=%s)",
            project_id, cred_path,
        )
    else:
        cred = credentials.ApplicationDefault()
        logger.info(
            "Initializing Firebase Admin with ADC (project=%s). "
            "Run 'gcloud auth application-default login' if this fails.",
            project_id,
        )

    firebase_admin.initialize_app(cred, {"projectId": project_id})


def get_db() -> Client:
    """Return the singleton Firestore client. Calls init_app() if needed."""
    global _db
    if _db is None:
        init_app()
        _db = firestore.client()
    return _db


# ==========================================================
# Atomic Claim — forecastQueries/{id}: pending -> claimed
# ==========================================================

@firestore.transactional
def _claim_query_txn(
    transaction: Transaction,
    query_ref,
    worker_id: str,
) -> Optional[dict]:
    """
    Inside a Firestore transaction:
      1. Re-read forecastQueries/{id}
      2. If status is still 'pending': flip to 'claimed', stamp claimedAt
         (server time) and claimedBy (worker_id), return the original
         payload as it was BEFORE the update.
      3. Otherwise (already claimed by another worker, or doc missing):
         return None.

    The transaction guarantees that two concurrent workers cannot both
    succeed on the same doc — one will see status != 'pending' on its
    re-read and back off.
    """
    snapshot = query_ref.get(transaction=transaction)
    if not snapshot.exists:
        return None
    data = snapshot.to_dict()
    if data.get("status") != "pending":
        return None
    transaction.update(query_ref, {
        "status": "claimed",
        "claimedAt": firestore.SERVER_TIMESTAMP,
        "claimedBy": worker_id,
    })
    return data


def claim_query(query_doc_id: str, worker_id: str) -> Optional[dict]:
    """
    Atomically claim a forecastQueries doc.

    Args:
        query_doc_id: doc id under forecastQueries/.
                      Equals sessionId per the server-side write at
                      session.repository.ts:347.
        worker_id:    Hub's AGENT_WORKER_ID — written into claimedBy.

    Returns:
        The original doc payload (queryId, sessionId, userId, question,
        status='pending', createdAt, claimedAt=None, claimedBy=None) on
        successful claim. Caller uses sessionId/userId/question to drive
        the rest of the pipeline.

        None if the doc was already claimed by another worker (race lost)
        or the doc no longer exists.
    """
    db = get_db()
    query_ref = db.collection("forecastQueries").document(query_doc_id)
    transaction = db.transaction()
    result = _claim_query_txn(transaction, query_ref, worker_id)
    if result is None:
        logger.info(
            "claim_query: race lost or doc missing (query_doc_id=%s)",
            query_doc_id,
        )
    else:
        logger.info(
            "claim_query: claimed query_doc_id=%s queryId=%s worker=%s",
            query_doc_id, result.get("queryId"), worker_id,
        )
    return result


# ==========================================================
# Session Status Updates — sessions/{id}.status
# ==========================================================
#
# Sprint 21 forward-flag (clarification flow):
# update_session_status writes ONLY to sessions/{id}. When Sprint 21
# implements the clarification flow ('awaiting_clarification' status),
# the corresponding forecastQueries queue state will likely need its
# own transition — e.g., the worker may release its claim or move the
# query to a 'pending_clarification' queue state so it can be re-picked
# after the user resolves the clarification. Decide queue-side semantics
# at Sprint 21 kickoff and add a sibling function (e.g.
# release_query_for_clarification) rather than overloading this one.

def update_session_status(
    session_id: str,
    status: str,
    error_code: Optional[str] = None,
    error_message: Optional[str] = None,
    clarification_candidates: Optional[list[dict]] = None,
    tier: Optional[str] = None,
) -> None:
    """
    Update sessions/{session_id} for a status transition.

    Status values (6-value SessionStatus enum, matches
    client/src/services/session.service.ts:3):
        'queued' | 'claimed' | 'running' | 'done' | 'failed' |
        'awaiting_clarification'

    Always stamps updatedAt and lastActivityAt with server time so the
    frontend's onSnapshot listener re-renders.

    Optional fields write only when explicitly provided — passing None
    does NOT clear an existing value (uses dict-conditional, not update
    with None). This lets each transition write only what's relevant
    without nuking prior state.

    Args:
        session_id: the doc id under sessions/.
        status:     one of the 6 SessionStatus values (caller validates).
        error_code: written to errorCode on 'failed'.
        error_message: written to errorMessage on 'failed'.
        clarification_candidates: written to clarificationCandidates on
                                  'awaiting_clarification' (Sprint 21).
                                  Field name camelCase to match the
                                  server-side schema.
        tier: written to tier on 'done' (Sprint 21 T21.8). Matches the
              session doc schema (frontend-integration skill): 'tier_1' |
              'tier_2' | null.
    """
    db = get_db()
    session_ref = db.collection("sessions").document(session_id)

    update_data: dict = {
        "status": status,
        "updatedAt": firestore.SERVER_TIMESTAMP,
        "lastActivityAt": firestore.SERVER_TIMESTAMP,
    }
    if error_code is not None:
        update_data["errorCode"] = error_code
    if error_message is not None:
        update_data["errorMessage"] = error_message
    if clarification_candidates is not None:
        update_data["clarificationCandidates"] = clarification_candidates
    if tier is not None:
        update_data["tier"] = tier

    session_ref.update(update_data)
    logger.info(
        "update_session_status: session_id=%s status=%s",
        session_id, status,
    )


# ==========================================================
# Worker Queue Status — forecastQueries/{id}.status
# ==========================================================

def update_query_status(
    query_doc_id: str,
    status: str,
    error_message: Optional[str] = None,
) -> None:
    """
    Update forecastQueries/{query_doc_id}.status to mark the work as
    finished. Distinct from update_session_status — that's the user-
    facing state under sessions/; this is the worker queue under
    forecastQueries/.

    Called by process_query at the end of work to clear the doc out of
    the 'claimed' state. Without this, claimed docs sit at 'claimed'
    forever after work completes.

    The listener (T6) filters on status=='pending', so dangling 'claimed'
    docs don't break re-processing — but cleaner to mark explicitly.

    Field-name choice: the Firestore field is `errorMessage` (camelCase),
    matching the same field name on the sessions schema. One concept,
    one name across both doc types.

    Args:
        query_doc_id:  doc id under forecastQueries/ (== sessionId).
        status:        'done' or 'failed' (caller validates).
        error_message: optional human-readable error text on 'failed'.
                       Written to the `errorMessage` field.
    """
    db = get_db()
    query_ref = db.collection("forecastQueries").document(query_doc_id)

    update_data: dict = {"status": status}
    if error_message is not None:
        update_data["errorMessage"] = error_message

    query_ref.update(update_data)
    logger.info(
        "update_query_status: query_doc_id=%s status=%s",
        query_doc_id, status,
    )


# ==========================================================
# Result Persistence — sessionResults/{id}
# ==========================================================

def write_session_result(session_id: str, result: dict) -> None:
    """
    Write sessionResults/{session_id} with the SessionResult shape per
    §8.7.2 + handoff §5.1.

    Uses set() (not update()) — the doc doesn't exist before this call.
    Adds sessionId, createdAt, updatedAt to whatever the caller passes;
    the caller owns finalProbability, confidence, label fields, etc.

    Sprint 18 stub contract: caller passes finalProbability=0.5,
    confidence=0.5, derived label fields per §8.7.2, empty list fields,
    and a whatIDidntFind explaining stub status.

    Note: if called twice for the same session (retry scenario), the
    second call overwrites including createdAt. Acceptable for Sprint 18
    stub; later sprints can switch to set(merge=True) with conditional
    createdAt if retry semantics matter.
    """
    db = get_db()
    result_ref = db.collection("sessionResults").document(session_id)

    payload = {
        **result,
        "sessionId": session_id,
        "createdAt": firestore.SERVER_TIMESTAMP,
        "updatedAt": firestore.SERVER_TIMESTAMP,
    }
    result_ref.set(payload)
    logger.info("write_session_result: session_id=%s", session_id)


# ==========================================================
# Subcollection Writes — Sprint 20 T20.5 (write_to_firestore node)
# ==========================================================

# Firestore WriteBatch caps at 500 ops per commit. For Sprint 20 the
# realistic evidence count per session is ~30-50 so one batch suffices,
# but the helper slices into 500-doc chunks defensively for forward
# compatibility.
FIRESTORE_BATCH_MAX_DOCS = 500


def write_evidence_batch(session_id: str, evidence_items: list[dict]) -> int:
    """
    Write the per-session evidence subcollection at
    sessions/{session_id}/evidence/{evidence_id} in batched transactions.

    Each item dict is expected to be a fully-rated EvidenceItem
    (post-rate_evidence + post-synthesize-overlay) plus a frontend
    `type` field — the caller (write_to_firestore node) does the
    source_type → frontend type mapping before passing here.

    Args:
        session_id:      doc id under sessions/.
        evidence_items:  list of dicts to write under
                         sessions/{session_id}/evidence/. Each item MUST
                         have an `evidence_id` key — that becomes the
                         subcollection doc id (so reruns overwrite the
                         same row deterministically).

    Returns:
        Count of items written. Zero is acceptable (no evidence retrieved
        is a valid cold-start outcome).

    Why batched commits, not one .set() per doc:
        Per-doc writes round-trip to Firestore. A 30-item evidence
        subcollection at 30 RTTs vs 1 batched commit is a ~30x latency
        difference. The 500-cap is a Firestore hard limit, not advisory.

    Why explicit doc id from evidence_id:
        Auto-id would make reruns produce duplicate evidence rows.
        Using evidence_id as the doc id makes the write idempotent —
        a retry overwrites the same row.
    """
    if not evidence_items:
        return 0

    db = get_db()
    evidence_collection = (
        db.collection("sessions").document(session_id).collection("evidence")
    )

    written = 0
    for chunk_start in range(0, len(evidence_items), FIRESTORE_BATCH_MAX_DOCS):
        chunk = evidence_items[chunk_start: chunk_start + FIRESTORE_BATCH_MAX_DOCS]
        batch = db.batch()
        for item in chunk:
            evidence_id = item.get("evidence_id")
            if not evidence_id:
                logger.warning(
                    "write_evidence_batch: skipping item with missing "
                    "evidence_id (session_id=%s)", session_id,
                )
                continue
            ref = evidence_collection.document(evidence_id)
            batch.set(ref, item)
            written += 1
        batch.commit()

    logger.info(
        "write_evidence_batch: session_id=%s items=%d batches=%d",
        session_id, written,
        (len(evidence_items) + FIRESTORE_BATCH_MAX_DOCS - 1) // FIRESTORE_BATCH_MAX_DOCS,
    )
    return written


def write_prediction_series(session_id: str, points: list[dict]) -> int:
    """
    Write the per-session predictionSeries subcollection at
    sessions/{session_id}/predictionSeries/{auto_id}.

    Sprint 20 contract: empty list is the typical case. With KG-PHASE8-12
    deferring the polymarket auto-pick resolver, no time-series data is
    available — the synthesis node leaves `predictionSeries` empty in
    its output. The PredictionOverview BI card renders an empty state.
    Sprint 22+ may populate per-day points from reactive search.

    Each point is an arbitrary dict the frontend's `mapPredictionDoc`
    decoder accepts — shape is the partner's schema, not specified here.

    Args:
        session_id: doc id under sessions/.
        points:     list of point dicts. Empty list = no writes.

    Returns:
        Count of points written. Zero is the Sprint 20 default.

    Why auto-id (not explicit):
        Time-series points are append-only and don't have a natural
        idempotency key. Auto-id is fine — reruns overwrite the
        sessionResults doc, and a partial subcollection is cleared by
        operations team if it ever matters.
    """
    if not points:
        return 0

    db = get_db()
    prediction_collection = (
        db.collection("sessions").document(session_id).collection("predictionSeries")
    )

    written = 0
    for chunk_start in range(0, len(points), FIRESTORE_BATCH_MAX_DOCS):
        chunk = points[chunk_start: chunk_start + FIRESTORE_BATCH_MAX_DOCS]
        batch = db.batch()
        for point in chunk:
            ref = prediction_collection.document()  # auto-id
            batch.set(ref, point)
            written += 1
        batch.commit()

    logger.info(
        "write_prediction_series: session_id=%s points=%d",
        session_id, written,
    )
    return written


def write_sentiment_time_series(session_id: str, points: list[dict]) -> int:
    """
    Write the per-session sentimentTimeSeries subcollection at
    sessions/{session_id}/sentimentTimeSeries/{auto_id}.

    Sprint 20 contract: empty list per Q5 of the implementation plan
    (sentiment time series isn't derivable from current Sprint 19/20
    evidence packages). The frontend's SentimentAnalysis BI card
    renders an empty state. Sprint 22+ reactive search may add points.

    Args:
        session_id: doc id under sessions/.
        points:     list of point dicts. Empty list = no writes.

    Returns:
        Count of points written.
    """
    if not points:
        return 0

    db = get_db()
    sentiment_collection = (
        db.collection("sessions").document(session_id).collection("sentimentTimeSeries")
    )

    written = 0
    for chunk_start in range(0, len(points), FIRESTORE_BATCH_MAX_DOCS):
        chunk = points[chunk_start: chunk_start + FIRESTORE_BATCH_MAX_DOCS]
        batch = db.batch()
        for point in chunk:
            ref = sentiment_collection.document()  # auto-id
            batch.set(ref, point)
            written += 1
        batch.commit()

    logger.info(
        "write_sentiment_time_series: session_id=%s points=%d",
        session_id, written,
    )
    return written


# ==========================================================
# Document Reads — for pre-graph state setup (Sprint 21 T21.4)
# ==========================================================

def get_query_doc(query_doc_id: str) -> Optional[dict]:
    """
    Read a forecastQueries/{query_doc_id} document and return its data dict.

    Used by process_query.py (T21.4) before graph.invoke() to check for a
    `chosenCandidateId` field on resume-on-clarify runs. The doc is also
    claimed transactionally inside claim_session (Node 0); this read is a
    separate pre-flight call that does NOT alter the doc's status.

    Returns:
        dict of field values if the document exists, else None.
    """
    db = get_db()
    snap = db.collection("forecastQueries").document(query_doc_id).get()
    if not snap.exists:
        return None
    return snap.to_dict()


def get_session_doc(session_id: str) -> Optional[dict]:
    """
    Read a sessions/{session_id} document and return its data dict.

    Used by process_query.py (T21.4) on resume-on-clarify runs to fetch the
    stored `clarificationCandidates` array so the chosen candidate's
    polymarket_search_terms and entities can be recovered for
    structured_intent pre-population.

    Returns:
        dict of field values if the document exists, else None.
    """
    db = get_db()
    snap = db.collection("sessions").document(session_id).get()
    if not snap.exists:
        return None
    return snap.to_dict()


# ==========================================================
# Snapshot Listener — forecastQueries where status == 'pending'
# ==========================================================

def subscribe_pending_queries(callback) -> object:
    """
    Register a snapshot listener on forecastQueries where status=='pending'.

    The Firestore SDK delivers snapshot batches to `callback` on a dedicated
    background thread (Watch class internals). Multiple deliveries are
    serialized on that thread — two callback invocations cannot execute
    concurrently for the same Watch.

    The initial delivery contains all currently-matching docs as ADDED.
    After that:
      - ADDED fires when a new doc enters the filter set.
      - MODIFIED fires when a doc's fields change while still matching.
      - REMOVED fires when a doc leaves the filter set — including when
        our own claim_query flips status to 'claimed' for the doc we
        just took. Callers typically only care about ADDED.

    Args:
        callback: invoked with (col_snapshot, changes, read_time). Caller
                  must wrap processing logic in try/except — the SDK's
                  public on_snapshot takes a single callback only; there
                  is no separate error channel, so uncaught exceptions
                  inside the callback are the listener's only failure
                  signal. Caller is responsible for treating those as
                  fatal and triggering a clean shutdown.

    Returns:
        Watch handle. Caller MUST call .unsubscribe() during shutdown to
        join the watch thread and close the gRPC stream cleanly.
    """
    db = get_db()
    query = db.collection("forecastQueries").where(
        filter=FieldFilter("status", "==", "pending")
    )
    return query.on_snapshot(callback)
