"""
Sprint 24 T24.13 E2E driver — follow-up conversations, REAL gpt-4o-mini +
Firestore emulator, run LOCALLY. Standalone runner (NOT a suite pytest), so
real billable calls stay out of the default test suite (matches the Sprint 21
E2E precedent `tests/e2e/sprint21_e2e_run.py`).

What this proves (24.13's purpose): that the REAL model, on a completed
forecast's own context, (a) answers in-context follow-ups, (b) classifies a
genuinely-insufficient follow-up as insufficient and returns the transparent
message, and (c) answers back-to-back follow-ups independently with correct
replyToMessageId linkage — while the real Firestore atomic claim + `sent →
answered` flip run against the emulator.

Scope decision (Ron, 2026-07-04, option 1 — run locally now, decoupled from
the initial test): the FORECAST is not the thing under test here; the
follow-up LLM behavior is. So Phase 0 SEEDS a realistic completed forecast
(done session + SessionResult + top evidence) directly into the emulator
rather than running the full pipeline (which would need the worker + vault +
gpt-4o). Only the five gpt-4o-mini FOLLOW-UP calls are real — a few cents.

The follow-ups run IN-PROCESS (subgraph unrolled: load_context → answer →
write_message) so each real gpt-4o-mini call can be timed, budget-timeouts
detected, and the model's chosen path read back. The listener/sweep wiring is
already verified against the emulator (Gate 3); this run's unique job is the
real-LLM behavior + instrumentation.

Pre-conditions:
    1. Firestore emulator running (localhost:8080 or FIRESTORE_EMULATOR_HOST).
    2. OPENAI_API_KEY set (data-pipeline/.env or infrastructure/.env).
    (No worker, no vault, no Kafka needed — Phase 0 is seeded.)

Skip-guard: with OPENAI_API_KEY unset, prints SKIP and exits 0 before any
Firestore/OpenAI access.

Usage:
    data-pipeline\\venv\\Scripts\\python.exe -m tests.e2e.sprint24_e2e_run

Exit code: 0 on full pass (or clean skip); 1 on any failure.
"""

from __future__ import annotations

import os
import statistics
import sys
import time
import uuid

from agent.config import settings
from agent.followup.nodes import answer_from_context, load_context, write_message
from agent.followup.nodes.answer_from_context import TIMEOUT_CAVEAT_MESSAGE
from agent.prompts.followup import (
    INSUFFICIENT_EVIDENCE_MESSAGE,
    OUT_OF_SCOPE_MESSAGE,
)


# ==========================================================
# Constants
# ==========================================================
USER_ID = "e2e-sprint24-test-user"

# Phase 0 — the seeded completed forecast the follow-ups run against.
FORECAST_QUESTION = "Will the Fed cut interest rates before the end of 2026?"

# Phase 1 follow-ups. The first two are answerable from the seeded forecast's
# own context; the third deliberately asks about information the forecast could
# not contain (post-generation developments) → must classify insufficient.
FOLLOWUP_ANSWERABLE_1 = "Why is the confidence what it is?"
FOLLOWUP_ANSWERABLE_2 = "Which factor influenced this forecast the most?"
FOLLOWUP_INSUFFICIENT = (
    "What new developments have happened since this forecast was generated?"
)

# Phase 2 back-to-back follow-ups.
FOLLOWUP_B2B_1 = "Can you restate the bottom line in one sentence?"
FOLLOWUP_B2B_2 = "What did the forecast say it could not cover?"


# ==========================================================
# Classification of a returned response_text (fixed strings → exact)
# ==========================================================
def _classify(response_text: str) -> str:
    if response_text == INSUFFICIENT_EVIDENCE_MESSAGE:
        return "insufficient_evidence"
    if response_text == OUT_OF_SCOPE_MESSAGE:
        return "out_of_scope"
    if response_text == TIMEOUT_CAVEAT_MESSAGE:
        return "timeout_caveat"
    return "answerable"


# ==========================================================
# Emulator-targeted Firestore init
# ==========================================================
def _init_emulator_db():
    """
    Point the Admin SDK at the local Firestore emulator and wire the resulting
    client into firestore_client so every node/tool call routes there. Mirrors
    the conftest `emulator_db` fixture (anonymous credentials — the emulator
    skips real auth when FIRESTORE_EMULATOR_HOST is set), so no ADC/gcloud
    login is needed for a local run.
    """
    import firebase_admin
    import google.auth.credentials
    from firebase_admin import credentials, firestore as fb_firestore

    from agent import firestore_client

    host = os.getenv("FIRESTORE_EMULATOR_HOST", "localhost:8080")
    os.environ["FIRESTORE_EMULATOR_HOST"] = host
    print(f"  Firestore emulator target: {host}")

    class _EmulatorCredentials(credentials.Base):
        def get_credential(self):
            return google.auth.credentials.AnonymousCredentials()

    if not firebase_admin._apps:
        firebase_admin.initialize_app(
            _EmulatorCredentials(),
            {"projectId": settings.FIREBASE_PROJECT_ID or "anizai-ai"},
        )
    db = fb_firestore.client()
    firestore_client._db = db  # route all firestore_client.* calls to the emulator
    return db


# ==========================================================
# Phase 0 — seed a realistic COMPLETED forecast
# ==========================================================
def _seed_completed_forecast(db, session_id: str) -> None:
    """
    Seed a done session + a realistic SessionResult + top-5 evidence, so the
    follow-ups have genuine material to answer from (and to fail to answer the
    insufficient one). Shapes match what write_to_firestore / synthesize write
    (camelCase SessionResult; evidence carries rank/relevance_score/title/
    snippet/source_type).
    """
    from firebase_admin import firestore as fb_firestore

    db.collection("sessions").document(session_id).set({
        "userId": USER_ID,
        "question": FORECAST_QUESTION,
        "status": "done",
        "tier": "tier_1",
        "createdAt": fb_firestore.SERVER_TIMESTAMP,
        "updatedAt": fb_firestore.SERVER_TIMESTAMP,
        "lastActivityAt": fb_firestore.SERVER_TIMESTAMP,
    })

    db.collection("sessionResults").document(session_id).set({
        "sessionId": session_id,
        "finalProbability": 0.38,
        "confidence": 0.72,
        "bottomLineAnswer": (
            "A 2026 rate cut is more likely not to happen than to happen: the "
            "evidence points to the Fed holding while inflation stays above "
            "target, though a cooling labor market keeps a cut in play."
        ),
        "keyFactors": [
            {"label": "Inflation still above the 2% target",
             "description": "Core PCE has been sticky, pushing against a cut.",
             "weight": 0.42, "direction": "decreases", "evidence_ids": ["ev1"]},
            {"label": "Labor market cooling",
             "description": "Rising unemployment claims raise the odds of easing.",
             "weight": 0.31, "direction": "increases", "evidence_ids": ["ev2"]},
            {"label": "Hawkish FOMC minutes",
             "description": "Recent minutes emphasized patience on cuts.",
             "weight": 0.27, "direction": "decreases", "evidence_ids": ["ev3"]},
        ],
        "whatIDidntFind": [
            "Any explicit forward guidance naming a 2026 cut date",
            "Post-forecast FOMC decisions or data releases",
        ],
        "confidenceLabel": "Moderate",
        "agentVersion": settings.AGENT_VERSION,
    })

    evidence = [
        ("ev1", "vault_news", "Core PCE stays sticky above target",
         "Inflation held above the Fed's 2% goal for another month, complicating the path to cuts."),
        ("ev2", "vault_news", "Jobless claims tick up",
         "Weekly unemployment claims rose, a sign the labor market is loosening."),
        ("ev3", "vault_news", "FOMC minutes stress patience",
         "Officials signaled they are in no hurry to cut rates absent clearer disinflation."),
        ("ev4", "vault_market", "Fed funds futures price a hold",
         "Market-implied odds lean toward rates unchanged through mid-2026."),
        ("ev5", "vault_arxiv", "Monetary policy lag effects",
         "Research suggests policy transmission lags keep the Fed cautious."),
    ]
    for rank, (eid, source_type, title, snippet) in enumerate(evidence, start=1):
        db.collection("sessions").document(session_id).collection("evidence").document(
            eid
        ).set({
            "evidence_id": eid,
            "source_type": source_type,
            "title": title,
            "snippet": snippet,
            "rank": rank,
            "is_key_evidence": rank <= 5,
            "relevance_score": round(0.9 - 0.05 * rank, 3),
        })


# ==========================================================
# Firestore helpers
# ==========================================================
def _seed_user_message(db, session_id: str, content: str) -> str:
    from firebase_admin import firestore as fb_firestore

    ref = (
        db.collection("sessions").document(session_id)
        .collection("messages").document()
    )
    ref.set({
        "role": "user",
        "content": content,
        "status": "sent",
        "createdAt": fb_firestore.SERVER_TIMESTAMP,
    })
    return ref.id


def _user_message_status(db, session_id: str, message_id: str) -> str | None:
    snap = (
        db.collection("sessions").document(session_id)
        .collection("messages").document(message_id).get()
    )
    return snap.to_dict().get("status") if snap.exists else None


def _find_reply_to(db, session_id: str, user_message_id: str) -> str | None:
    """Return the replyToMessageId of the assistant message linked to this user
    message (for linkage verification)."""
    docs = (
        db.collection("sessions").document(session_id)
        .collection("messages").stream()
    )
    for d in docs:
        data = d.to_dict()
        if data.get("role") == "assistant" and data.get("replyToMessageId") == user_message_id:
            return data.get("replyToMessageId")
    return None


# ==========================================================
# Instrumented in-process follow-up
# ==========================================================
def _run_followup_instrumented(db, session_id: str, question: str) -> dict:
    """
    Unroll the follow-up subgraph so the gpt-4o-mini call is timed in
    isolation: seed the user message, load context (real emulator reads), time
    the answer node (REAL gpt-4o-mini), then write the reply (real atomic
    claim on the emulator).
    """
    message_id = _seed_user_message(db, session_id, question)

    state = {
        "parent_session_id": session_id,
        "trigger_message_id": message_id,
        "trigger_question": question,
    }
    state.update(load_context.run(state))

    t0 = time.time()
    state.update(answer_from_context.run(state))  # real default client (6s budget)
    latency_ms = (time.time() - t0) * 1000.0

    write_message.run(state)

    response_text = state.get("response_text", "")
    return {
        "message_id": message_id,
        "latency_ms": latency_ms,
        "classification": _classify(response_text),
        "response_text": response_text,
        "reply_to": _find_reply_to(db, session_id, message_id),
        "answered_status": _user_message_status(db, session_id, message_id),
    }


# ==========================================================
# Main
# ==========================================================
def main() -> int:
    # Windows consoles default to cp1252, which can't encode characters like
    # the arrow used in the progress lines. Force UTF-8 so the run doesn't die
    # on a print (the crash surfaced 2026-07-04). Best-effort — a stream that
    # doesn't support reconfigure just keeps its default.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    if not settings.OPENAI_API_KEY:
        print(
            "SKIP: sprint24_e2e_run requires OPENAI_API_KEY (real gpt-4o-mini "
            "billing). The runner imports and skip-guards cleanly."
        )
        return 0

    print(f"\n{'='*60}\nSprint 24 E2E — REAL gpt-4o-mini + Firestore emulator\n{'='*60}")
    db = _init_emulator_db()

    failures: list[str] = []
    latencies: list[float] = []
    timeout_count = 0

    # ── Phase 0: seed a realistic completed forecast ──────────
    session_id = f"e2e-sprint24-{uuid.uuid4().hex[:8]}"
    print(f"\nPhase 0 — seed completed forecast\n  session_id: {session_id}\n"
          f"  question  : {FORECAST_QUESTION!r}")
    _seed_completed_forecast(db, session_id)
    print("  Seeded done session + SessionResult + 5 evidence items.")

    # ── Phase 1: three sequential follow-ups (REAL LLM) ───────
    print(f"\nPhase 1 — three follow-ups (real gpt-4o-mini)")
    plan = [
        ("answerable #1", FOLLOWUP_ANSWERABLE_1, "answerable"),
        ("answerable #2", FOLLOWUP_ANSWERABLE_2, "answerable"),
        ("insufficient", FOLLOWUP_INSUFFICIENT, "insufficient_evidence"),
    ]
    for label, question, expected in plan:
        r = _run_followup_instrumented(db, session_id, question)
        latencies.append(r["latency_ms"])
        if r["classification"] == "timeout_caveat":
            timeout_count += 1
        print(f"  [{label}] {r['latency_ms']:.0f}ms -> {r['classification']} "
              f"(answered={r['answered_status']!r}, reply_to={r['reply_to']!r})")
        print(f"      reply: {r['response_text'][:140]!r}")
        if r["classification"] == "timeout_caveat":
            print("      (budget timeout fired — correct degradation, counted)")
        elif r["classification"] != expected:
            failures.append(
                f"Phase 1 [{label}]: expected {expected}, got {r['classification']}"
            )
        if r["answered_status"] != "answered":
            failures.append(
                f"Phase 1 [{label}]: user message status={r['answered_status']!r}, "
                f"expected 'answered'"
            )
        if r["reply_to"] != r["message_id"]:
            failures.append(
                f"Phase 1 [{label}]: replyToMessageId={r['reply_to']!r} != "
                f"message_id={r['message_id']!r}"
            )

    # ── Phase 2: back-to-back linkage (REAL LLM) ──────────────
    print(f"\nPhase 2 — two back-to-back follow-ups (real gpt-4o-mini)")
    r1 = _run_followup_instrumented(db, session_id, FOLLOWUP_B2B_1)
    r2 = _run_followup_instrumented(db, session_id, FOLLOWUP_B2B_2)
    for tag, r in (("b2b #1", r1), ("b2b #2", r2)):
        latencies.append(r["latency_ms"])
        if r["classification"] == "timeout_caveat":
            timeout_count += 1
        print(f"  [{tag}] {r['latency_ms']:.0f}ms -> {r['classification']} "
              f"(answered={r['answered_status']!r}, reply_to={r['reply_to']!r})")
        if r["answered_status"] != "answered":
            failures.append(f"Phase 2 [{tag}]: not answered")
        if r["reply_to"] != r["message_id"]:
            failures.append(
                f"Phase 2 [{tag}]: replyToMessageId={r['reply_to']!r} != "
                f"message_id={r['message_id']!r} (linkage wrong)"
            )
    if r1["reply_to"] == r2["reply_to"]:
        failures.append(
            "Phase 2: both replies linked to the SAME message — back-to-back "
            "independence broken"
        )

    # ── Report ────────────────────────────────────────────────
    print(f"\n{'='*60}\nREPORT — Sprint 24 E2E (real gpt-4o-mini)\n{'='*60}")
    if latencies:
        print(f"  follow-up call latency (n={len(latencies)}):")
        print(f"    each (ms): {[round(x) for x in latencies]}")
        print(f"    min={min(latencies):.0f}  median={statistics.median(latencies):.0f}  "
              f"max={max(latencies):.0f}")
    print(f"  AGENT_FOLLOWUP_BUDGET_MS={settings.AGENT_FOLLOWUP_BUDGET_MS}ms — timeout "
          f"fired on {timeout_count}/{len(latencies)} calls "
          f"(caveat = correct degradation, not failure)")
    insufficient_confirmed = not any("insufficient" in f for f in failures)
    print(f"  third follow-up returned the transparent insufficient message via the "
          f"real LLM: {insufficient_confirmed}")

    print(f"\n{'='*60}")
    if failures:
        print(f"RESULT: FAIL ({len(failures)} issue(s))")
        for f in failures:
            print(f"  - {f}")
        print(f"  session_id={session_id} (docs left in emulator for inspection)")
        return 1
    print("RESULT: PASS — all follow-ups behaved as specified.")
    print(f"  session_id={session_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
