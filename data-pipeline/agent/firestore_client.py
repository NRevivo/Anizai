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
from typing import Optional, Union

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


# ==========================================================
# UNSET sentinel for tri-state kwargs (Sprint 22 T22.7)
# ==========================================================
# Some update helpers need to distinguish three states for a single
# optional kwarg:
#   - kwarg omitted entirely        → don't write this field
#   - kwarg = None                  → write Firestore null (explicit)
#   - kwarg = some value            → write that value
#
# A plain `Optional[str] = None` default conflates the first two cases:
# you cannot tell "caller didn't pass anything" from "caller wants
# explicit null".
#
# The UNSET sentinel makes the three states distinct. Callers either
# omit the kwarg (default UNSET → no write), or pass an explicit value
# (string or None). The helper checks `value is not UNSET` to decide
# whether to include the field in the update payload.
#
# Pattern introduced for `update_session_status(canonical_key=...)` in
# Sprint 22 T22.7 — pre-emptive infrastructure for Future Enhancement 3
# (cross-user cache). Reusable for any future kwarg that needs the same
# write-explicit-null semantics.
class _UnsetType:
    """Distinct singleton type so `value is UNSET` checks are unambiguous."""

    _instance: "Optional[_UnsetType]" = None

    def __new__(cls) -> "_UnsetType":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "UNSET"

    def __bool__(self) -> bool:
        return False


UNSET: _UnsetType = _UnsetType()

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
    canonical_key: "Union[str, None, _UnsetType]" = UNSET,
) -> None:
    """
    Update sessions/{session_id} for a status transition.

    Status values (6-value SessionStatus enum, matches
    client/src/services/session.service.ts:3):
        'queued' | 'claimed' | 'running' | 'done' | 'failed' |
        'awaiting_clarification'

    Always stamps updatedAt and lastActivityAt with server time so the
    frontend's onSnapshot listener re-renders.

    Kwarg write semantics — TWO conventions in this signature, read
    carefully:

    Convention A (kwargs with `Optional[X] = None` default):
        `error_code`, `error_message`, `clarification_candidates`, `tier`.
        These use dict-conditional `if x is not None`. Passing `None`
        means "don't update this field" — preserves any existing
        Firestore value rather than clearing it. Each status transition
        writes only what's relevant.

    Convention B (kwargs with `UNSET` default — Sprint 22 T22.7):
        `canonical_key`. Uses the UNSET sentinel because Tier 2 success
        paths need to write **explicit Firestore null** (clearing any
        prior candidate UUID Express set during a clarification cycle),
        which Convention A cannot express. Three states:
            - omit kwarg (`UNSET` default) → don't update the field
            - pass `None`                  → write Firestore null
            - pass `"some-id"`             → write that string

    The asymmetry exists because most fields are "write or no-op";
    canonicalKey is "write-string OR write-explicit-null OR no-op". Any
    future kwarg needing the same write-explicit-null semantics can
    reuse the UNSET sentinel pattern from this module.

    Args:
        session_id: the doc id under sessions/.
        status:     one of the 6 SessionStatus values (caller validates).
        error_code: written to errorCode on 'failed'. Convention A.
        error_message: written to errorMessage on 'failed'. Convention A.
        clarification_candidates: written to clarificationCandidates on
                                  'awaiting_clarification' (Sprint 21).
                                  Field name camelCase to match the
                                  server-side schema. Convention A.
        tier: written to tier on 'done' (Sprint 21 T21.8). Matches the
              session doc schema (frontend-integration skill): 'tier_1' |
              'tier_2' | null. Convention A.
        canonical_key: written to canonicalKey on 'done' (Sprint 22 T22.7).
                       Convention B. The resolved Polymarket market
                       identifier on Tier 1; explicit `None` on Tier 2
                       (clears any prior candidate UUID Express set
                       during the clarification cycle so the future
                       cache lookup — Future Enhancement 3 —
                       correctly classifies the session as cacheless).
                       OMIT the kwarg on non-success transitions
                       (failed, awaiting_clarification) so Express's
                       prior write survives if the session is later
                       resumed.
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
    # Convention B: only write canonicalKey when the caller explicitly
    # provided a value (string OR None). UNSET preserves any prior write
    # so failed/awaiting_clarification transitions don't clobber the
    # candidate UUID Express may have already written during the
    # clarification cycle.
    if canonical_key is not UNSET:
        update_data["canonicalKey"] = canonical_key

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
# Follow-up context reads — Sprint 24 T24.3 (load_context node)
# ==========================================================
# The follow-up subgraph reasons over the PARENT forecast: its SessionResult,
# the evidence it used, and the recent chat. All three reads live here (not in
# the followup node) per hub-principles P2 — Firestore access is centralized in
# this module; nodes never touch firebase_admin directly.

def get_session_result(session_id: str) -> Optional[dict]:
    """
    Read the parent forecast's sessionResults/{session_id} document.

    Used by `agent/followup/nodes/load_context.py` (T24.3) to load the
    parent forecast a follow-up is about. sessionResults is a TOP-LEVEL
    collection (server contract D6 — see write_to_firestore docstring), not
    a subcollection under sessions/.

    Returns:
        The SessionResult dict if it exists, else None. None means the
        parent forecast has no persisted result yet — the follow-up node
        degrades to the transparent insufficient-evidence path rather than
        crashing.
    """
    db = get_db()
    snap = db.collection("sessionResults").document(session_id).get()
    if not snap.exists:
        return None
    return snap.to_dict()


def get_session_evidence(session_id: str) -> list[dict]:
    """
    Read every doc under sessions/{session_id}/evidence (T24.3).

    The caller (load_context) selects the top-N by `rank` in Python —
    evidence pools are ~30-50 items per session, so a full stream + in-memory
    sort is cheaper than composing an order_by/limit query (and needs no
    composite index).

    Returns:
        List of evidence dicts (each as written by write_evidence_batch,
        carrying `rank` / `is_key_evidence` / `relevance_score`). Empty list
        when the session retrieved no evidence — a valid cold-start outcome.
    """
    db = get_db()
    coll = (
        db.collection("sessions").document(session_id).collection("evidence")
    )
    return [doc.to_dict() for doc in coll.stream()]


def get_recent_messages(session_id: str, limit: int) -> list[dict]:
    """
    Read the most recent `limit` messages under sessions/{session_id}/messages,
    returned in chronological (oldest-first) order (T24.3).

    Ordered by `createdAt` (the server stamps it on every message via
    session.repository.ts `addMessage`). We fetch newest-first + limit, then
    reverse, so "last 10" is bounded server-side rather than streaming the
    whole thread.

    Each returned dict carries the message doc id under `messageId` (set from
    the doc id if the stored payload omits it) so callers can reference a
    specific message without a second read.

    Returns:
        List of up to `limit` message dicts, oldest-first. Empty list if the
        thread has no messages.
    """
    db = get_db()
    coll = (
        db.collection("sessions").document(session_id).collection("messages")
    )
    query = coll.order_by(
        "createdAt", direction=firestore.Query.DESCENDING
    ).limit(limit)
    docs = list(query.stream())
    docs.reverse()  # chronological (oldest-first) for prompt legibility

    out: list[dict] = []
    for doc in docs:
        data = doc.to_dict() or {}
        data.setdefault("messageId", doc.id)
        out.append(data)
    return out


# ==========================================================
# Follow-up answer — atomic claim + write (Sprint 24 T24.5)
# ==========================================================
# The follow-up listener (T24.1) and the done-transition sweep (T24.15) are
# TWO concurrent processors that can each read the same user message as
# `status=='sent'` and both try to answer it. A plain read-then-write is not
# race-safe against that. This mirrors the main path's atomic `claim_query`:
# a transactional conditional flip `sent -> answered` where only the winner
# writes the assistant reply (plan §3 "Build resolutions" — idempotency is an
# ATOMIC claim; no intermediate `processing` state). A crash before the
# transaction commits leaves the message `sent`, so it is re-answered on the
# next listener (re)attach — correct recovery, not a double-answer.

@firestore.transactional
def _claim_and_write_answer_txn(
    transaction: Transaction,
    user_msg_ref,
    assistant_msg_ref,
    assistant_payload: dict,
) -> bool:
    """
    Inside a Firestore transaction (reads before writes, per Firestore rules):
      1. Re-read the user message.
      2. If it is missing or no longer `status=='sent'` (the other processor
         already answered it, or the server never wrote it): return False —
         this processor lost the race and must NOT write a duplicate reply.
      3. Otherwise flip the user message `sent -> answered` AND set the
         assistant reply, atomically, and return True.

    The flip is what removes the user message from the listener's
    `status=='sent'` filter set, so it is never re-delivered (the follow-up
    analogue of the main path's `pending -> done`).
    """
    snapshot = user_msg_ref.get(transaction=transaction)
    if not snapshot.exists:
        return False
    data = snapshot.to_dict() or {}
    if data.get("status") != "sent":
        return False
    transaction.update(user_msg_ref, {"status": "answered"})
    transaction.set(assistant_msg_ref, assistant_payload)
    return True


def claim_and_write_followup_answer(
    session_id: str,
    user_message_id: str,
    assistant_message: dict,
) -> bool:
    """
    Atomically answer one follow-up message exactly once (T24.5 / T24.14).

    If sessions/{session_id}/messages/{user_message_id} is still
    `status=='sent'`, flip it to `'answered'` and write the assistant reply
    under a fresh auto-id doc in the same `messages` subcollection — both in
    a single transaction. If the message was already answered (the
    listener/sweep race), write nothing and return False.

    The assistant doc id and `createdAt`/`messageId` are stamped here (the
    firestore_client owns doc-id + server-timestamp stamping, mirroring
    write_session_result); the caller passes only the semantic payload
    (`role`, `content`, `replyToMessageId`, `agentVersion`, ...). Assistant
    messages carry NO frontend-facing status (plan §3 "Build resolutions").

    Args:
        session_id:       parent session id (== the messages' parent doc).
        user_message_id:  the specific user message being answered — the
                          claim target and the `replyToMessageId` value.
        assistant_message: the assistant reply payload (without id/createdAt).

    Returns:
        True if this processor won the claim and wrote the reply; False if
        another processor had already answered the message (no write done).
    """
    db = get_db()
    messages_coll = (
        db.collection("sessions").document(session_id).collection("messages")
    )
    user_msg_ref = messages_coll.document(user_message_id)
    assistant_msg_ref = messages_coll.document()  # fresh auto-id

    payload = {
        **assistant_message,
        "messageId": assistant_msg_ref.id,
        "sessionId": session_id,
        "createdAt": firestore.SERVER_TIMESTAMP,
    }

    transaction = db.transaction()
    won = _claim_and_write_answer_txn(
        transaction, user_msg_ref, assistant_msg_ref, payload
    )
    if won:
        logger.info(
            "claim_and_write_followup_answer: answered message_id=%s "
            "session_id=%s assistant_id=%s",
            user_message_id, session_id, assistant_msg_ref.id,
        )
    else:
        logger.info(
            "claim_and_write_followup_answer: race lost or not 'sent' — "
            "message_id=%s session_id=%s (already answered elsewhere)",
            user_message_id, session_id,
        )
    return won


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


# ==========================================================
# Follow-up message listener + sweep — Sprint 24 (T24.1 / T24.15)
# ==========================================================

def subscribe_followup_messages(callback) -> object:
    """
    Register a COLLECTION-GROUP snapshot listener on every `messages`
    subcollection where `role == 'user'` AND `status == 'sent'` (T24.1).

    Collection-group scope: this matches user messages across ALL sessions in
    one Watch — the follow-up analogue of the main path's forecastQueries
    listener. The parent `session.status == 'done'` condition is NOT
    expressible in the query (the parent doc is not in the messages
    collection group); the caller checks it per-message inside the callback
    via one parent-doc read (get_session_doc), and skips messages whose parent
    is not yet `done` (the T24.15 sweep catches those later).

    DEPLOYMENT NOTE (plan §3): the collection-group index / field-scope
    exemption this query needs is implicit on the local emulator but MUST be
    explicitly deployed to production Firestore before the initial test, or
    the query silently returns nothing there. The Firestore config lives in
    the partner/BFF file `server/firebase/firestore.indexes.json` (NOT under
    data-pipeline — this side does not edit it), which already declares a
    COLLECTION_GROUP index for `evidence` as the exact template. The needed
    entry:
        { "collectionGroup": "messages", "queryScope": "COLLECTION_GROUP",
          "fields": [ {"fieldPath": "role",   "order": "ASCENDING"},
                      {"fieldPath": "status", "order": "ASCENDING"} ] }

    Idempotency: comes from the `status == 'sent'` filter itself plus the
    atomic claim in `claim_and_write_followup_answer` — answering flips the
    message to `'answered'`, removing it from this filter set (it never
    re-delivers as ADDED). Mirrors the main path's `pending -> claimed`.

    Reconnects are not free: on any SDK reconnect the Watch redelivers EVERY
    currently-matching `sent` message as ADDED, each triggering a parent-doc
    read in the callback's done-guard. Idempotent (the atomic claim protects
    against double-answers), but the read cost scales with the outstanding
    `sent` backlog — worth noting beyond the low-volume initial test.

    Args:
        callback: invoked with (col_snapshot, changes, read_time). As with
                  subscribe_pending_queries, the SDK has no separate error
                  channel — the caller must wrap its body in try/except and
                  treat an uncaught exception as fatal.

    Returns:
        Watch handle. Caller MUST call .unsubscribe() during shutdown to join
        the watch thread and close the gRPC stream cleanly.
    """
    db = get_db()
    query = (
        db.collection_group("messages")
        .where(filter=FieldFilter("role", "==", "user"))
        .where(filter=FieldFilter("status", "==", "sent"))
    )
    return query.on_snapshot(callback)


def get_unanswered_user_messages(session_id: str) -> list[dict]:
    """
    Read the still-unanswered user messages for one session — the docs under
    sessions/{session_id}/messages where `role == 'user'` AND
    `status == 'sent'` (T24.15 done-transition sweep).

    This is a scoped subcollection query (single session), not the
    collection-group query above — so it needs no collection-group index.

    Each returned dict carries the message doc id under `messageId` so the
    sweep can build the follow-up initial state (trigger_message_id) without
    a second read.

    Returns:
        List of unanswered user-message dicts (empty if none). The sweep runs
        each through the follow-up subgraph; the atomic claim makes a race
        with the live listener safe (only one processor answers each).
    """
    db = get_db()
    coll = (
        db.collection("sessions").document(session_id).collection("messages")
    )
    query = coll.where(
        filter=FieldFilter("role", "==", "user")
    ).where(filter=FieldFilter("status", "==", "sent"))

    out: list[dict] = []
    for doc in query.stream():
        data = doc.to_dict() or {}
        data.setdefault("messageId", doc.id)
        out.append(data)
    return out
