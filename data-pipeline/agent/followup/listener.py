"""
agent/followup/listener.py — the second Firestore listener + the
done-transition sweep for the follow-up path (Sprint 24, T24.1 + T24.15).

Two entry points into the same follow-up subgraph, covering complementary
delivery windows so no follow-up message is lost:

    start_followup_listener(shutdown_event, on_fatal)  — T24.1
        A collection-group Watch on every `messages` subcollection where
        role=='user' AND status=='sent'. For each newly-arrived user message
        whose PARENT session is already `done`, it runs the follow-up
        subgraph. If the parent is not yet `done`, it skips (the sweep below
        catches it when the parent completes). Runs alongside the main
        forecastQueries listener in the same worker process.

    sweep_done_session(session_id)                      — T24.15
        Called by process_query.py right after a forecast completes. Confirms
        the session reached `done` (NOT awaiting_clarification), then runs any
        still-`sent` user messages for that session through the subgraph. This
        closes the one-time-delivery gap: a message written just before the
        `done` flip is delivered to the listener while the parent-done check
        fails, and the listener never re-delivers it — the sweep does.

Together: early messages (written before `done`) are caught by the sweep;
later messages (written after `done`) are caught by the listener; no gap.

Why the sweep lives here (not in write_to_firestore / the main graph):
    Ratified plan §3 — the sweep is called from process_query (the runner,
    not a graph node), so the main graph never imports the followup package
    (24.15 "without coupling the main graph to the followup package"). This
    module IS the followup package's seam; process_query depends on it, not
    the reverse.

Race-safety: the listener and the sweep can both see the same message as
`sent`. The atomic claim in `firestore_client.claim_and_write_followup_answer`
(via write_message) guarantees exactly one of them writes the reply — the
loser's run is a clean no-op. So overlap is safe, not a bug to prevent.

Concurrency: like the main worker, each message is processed synchronously
(Firestore reads + one gpt-4o-mini call) on the caller's thread — the watch
thread for the listener, the runner thread for the sweep. Sprint 24 volume
(3-4 testers) makes serial processing fine; a concurrency model is Sprint 26+.

No `agentEvents` emission (Sprint 25 / T25.7).

Spec references:
    - data-pipeline/docs/agentic_hub_spec.md §8.8.1 / §8.8.3 (revised)
    - data-pipeline/docs/B_hub/plans/sprint24_followups.md §3 + §4 T24.1/T24.15
    - .claude/skills/frontend-integration/SKILL.md (worker pattern; the
      messages subcollection contract; idempotency)
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

from agent import firestore_client
from agent.followup.graph import graph as followup_graph

logger = logging.getLogger(__name__)


# ==========================================================
# Shared helpers
# ==========================================================
def _session_is_done(session_id: str) -> bool:
    """
    True iff the parent session is confirmed `done` — the single place the
    follow-up path interprets "done".

    The parent-`done` condition cannot be expressed in the collection-group
    query (T24.1) and gates the sweep (T24.15), so both the listener and the
    sweep read the session doc and check `status == 'done'`. Centralized here
    (m1) so there is one done-check, and guarded (M1) so the read is safe:

    On a transient Firestore read error this returns False, which is correct
    for BOTH callers:
      - listener: skips the message (it stays `sent` and is caught on a later
        listener (re)delivery or the next sweep);
      - sweep: returns cleanly WITHOUT sweeping — critically, it never sweeps
        an `awaiting_clarification` (or otherwise not-confirmed-`done`) session,
        so `sweep_done_session`'s "never raises" contract is enforced at the
        function itself rather than by the worker's catch two frames up.
    Accepted consequence (Ron, 2026-07-04): on a transient read error the sweep
    skips that pass and the pre-`done` messages wait for the next listener
    reconnect — fine at current scale.
    """
    try:
        session_doc = firestore_client.get_session_doc(session_id)
    except Exception:
        logger.exception(
            "followup: session-status read failed for session %s; treating "
            "as not-done (skip this pass)", session_id,
        )
        return False
    return (session_doc or {}).get("status") == "done"


def _run_followup(session_id: str, message_id: str, content: str) -> None:
    """
    Run one user message through the follow-up subgraph.

    Builds the initial FollowupState with the three listener/sweep-seeded
    fields (parent_session_id, trigger_message_id, trigger_question) and
    invokes the compiled graph. The graph loads parent context, answers, and
    performs the atomic claim + assistant write.

    The triggering message is identified by `message_id` and its content is
    `content` (trigger_question) — never a positional pick (24.14).

    Errors are the caller's to contain: this raises whatever the subgraph
    raises (e.g. AgentProcessingError on a hard OpenAI failure). Both callers
    wrap per-message so one bad message never aborts the batch/listener.
    """
    initial_state = {
        "parent_session_id": session_id,
        "trigger_message_id": message_id,
        "trigger_question": content or "",
    }
    followup_graph.invoke(initial_state)


# ==========================================================
# Live listener — T24.1
# ==========================================================
def _process_message_change(change) -> None:
    """
    Handle one ADDED collection-group change (a `sent` user message).

    Resolves the parent session id from the collection-group doc reference,
    applies the parent-`done` guard (one parent-doc read — the condition the
    query cannot express), and runs the follow-up subgraph when the parent is
    `done`. Messages whose parent is not yet `done` are skipped; the sweep
    handles them when the forecast completes.
    """
    doc = change.document
    message_id = doc.id
    data = doc.to_dict() or {}

    # Collection-group doc lives at sessions/{sid}/messages/{mid}; walk the
    # reference up to the parent session doc: .parent = messages collection,
    # .parent.parent = sessions/{sid} document.
    session_ref = doc.reference.parent.parent
    if session_ref is None:
        logger.warning(
            "followup listener: message %s has no parent session ref; skipping",
            message_id,
        )
        return
    session_id = session_ref.id

    # Parent-done guard (the query cannot express it). If the parent forecast
    # is not done yet, skip — the T24.15 sweep will process this message when
    # the session transitions to 'done'.
    if not _session_is_done(session_id):
        logger.info(
            "followup listener: parent session %s not confirmed done; "
            "skipping message %s (sweep will catch it)",
            session_id, message_id,
        )
        return

    logger.info(
        "followup listener: processing message %s for done session %s",
        message_id, session_id,
    )
    _run_followup(session_id, message_id, data.get("content", ""))


def start_followup_listener(
    shutdown_event: threading.Event,
    on_fatal: Callable[[], None],
) -> object:
    """
    Register the collection-group follow-up listener and return its Watch.

    Args:
        shutdown_event: the worker's shutdown event. Checked at the top of
                        each change-loop iteration so a draining worker skips
                        remaining messages (they stay `sent` and are answered
                        by the next worker / the sweep) — symmetric with the
                        main listener's drain (T24.8).
        on_fatal:       invoked if the callback raises uncaught. The worker
                        wires this to set its fatal flag + shutdown event so
                        the container restart policy takes over (the SDK has
                        no separate error channel — mirrors the main
                        listener's fatal handling).

    Returns:
        Watch handle; the worker MUST .unsubscribe() it during shutdown.
    """

    def _on_message_snapshot(_col_snapshot, changes, _read_time) -> None:
        try:
            added = [c for c in changes if c.type.name == "ADDED"]
            if added:
                logger.info(
                    "followup listener: snapshot delivered %d sent user "
                    "message(s)", len(added),
                )
            for change in added:
                if shutdown_event.is_set():
                    logger.info(
                        "followup listener: shutdown in progress, skipping "
                        "remaining messages",
                    )
                    return
                message_id = change.document.id
                try:
                    _process_message_change(change)
                except Exception:
                    logger.exception(
                        "followup listener: processing raised for message "
                        "%s; continuing with next", message_id,
                    )
        except Exception:
            logger.exception(
                "followup listener: snapshot callback raised; treating as "
                "fatal and triggering shutdown",
            )
            on_fatal()

    watch = firestore_client.subscribe_followup_messages(_on_message_snapshot)
    logger.info("followup listener: subscribed to messages collection group")
    return watch


# ==========================================================
# Done-transition sweep — T24.15
# ==========================================================
def sweep_done_session(session_id: str) -> None:
    """
    Answer any still-unanswered user messages for a just-completed session.

    Called by process_query after a successful `graph.invoke()`. Guard
    (ratified §3): confirm the session reached `status == 'done'` before
    sweeping — a successful invoke can terminate in `awaiting_clarification`
    (the clarification branch), which has no forecast and must not trigger
    follow-up answers.

    For each `sent` user message, runs the follow-up subgraph. The atomic
    claim makes a race with the live listener safe. Per-message try/except so
    one bad message never aborts the sweep. Never raises — a sweep failure
    must not fail the parent forecast (which already succeeded).

    Args:
        session_id: the just-completed session to sweep.
    """
    if not _session_is_done(session_id):
        logger.info(
            "followup sweep: session %s not confirmed done; no sweep "
            "(e.g. awaiting_clarification has no forecast, or a transient "
            "read error — pre-done messages wait for the next reconnect)",
            session_id,
        )
        return

    try:
        pending = firestore_client.get_unanswered_user_messages(session_id)
    except Exception:
        logger.exception(
            "followup sweep: failed to read unanswered messages for session "
            "%s; skipping sweep", session_id,
        )
        return

    if not pending:
        return

    logger.info(
        "followup sweep: session %s has %d unanswered user message(s)",
        session_id, len(pending),
    )
    for msg in pending:
        message_id = msg.get("messageId")
        try:
            _run_followup(session_id, message_id, msg.get("content", ""))
        except Exception:
            logger.exception(
                "followup sweep: processing raised for message %s "
                "(session %s); continuing", message_id, session_id,
            )
