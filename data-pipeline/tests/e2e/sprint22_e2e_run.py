"""
Sprint 22 T22.11 — End-to-End driver.

Real environment integration test for the full Sprint 22 wiring stack:
T22.1 (pg_trgm resolver + silver_job D1 patch) through T22.7
(canonicalKey on session doc). Runs the real agent against real
Postgres + real OpenAI + the local Firestore emulator.

Plan-of-record (per dual approval 2026-05-26):
  Step 0  — Pre-flight checks (Postgres, Kafka, emulator, OpenAI key).
  Step 1  — Option B prereq: one-shot Polymarket REST poll → standalone
            silver_job → gold_job → momentum_vault. Skips Flink (KG-PHASE-9.5-8
            avoidance); momentum-block deltas are 0.0 (out of T22.11 scope).
            Fallback (Option C): if Polymarket REST returns 0 markets,
            INSERT a synthetic-question polymarket row directly.
  Step 2  — Verify the new row carries `question` in metadata_extension.
            Log the row's external_reference_id + question.
  Step 3  — Sanity check the resolver match_score against the verbatim
            question. Expected: 1.0 on the happy path; any deviation
            surfaces in the run log.
  Step 4  — Initialise the local Firestore emulator client. Seed
            sessions/{id} (status='queued') + forecastQueries/{id}
            (status='pending').
  Step 5  — Call process_query(session_id) in-process. This is the
            first end-to-end run of the production agent code with
            real LLM + real vault + real Firestore against a question
            matching a real Polymarket market.
  Step 6  — Per-BI-card assertions (range/shape, not LLM-content pins
            since the LLM output isn't deterministic).
  Step 7  — Dump full Firestore state (session doc + every subcollection)
            to a JSON file for post-hoc inspection regardless of outcome.

Cost: one full agent run ≈ $0.03 (T22.3 estimate). Authorised for one
E2E per gate-by-gate approval.

Usage:
    From data-pipeline\\ with venv active:
        python -m tests.e2e.sprint22_e2e_run

Output:
    Console: pass/fail per assertion; resolver match_score; session_id.
    File:    tests/e2e/output/sprint22_e2e_<run_id>_firestore_dump.json
             — final Firestore state for inspection.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# ==========================================================
# Output directory + run id
# ==========================================================
OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)
RUN_ID = uuid.uuid4().hex[:8]
DUMP_PATH = OUTPUT_DIR / f"sprint22_e2e_{RUN_ID}_firestore_dump.json"

# ==========================================================
# Console helpers
# ==========================================================

def _header(label: str) -> None:
    print("\n" + "=" * 72)
    print(label)
    print("=" * 72)


def _check(label: str, ok: bool, detail: str = "") -> None:
    marker = "[ OK ]" if ok else "[FAIL]"
    suffix = f"  — {detail}" if detail else ""
    print(f"{marker} {label}{suffix}")


def _info(label: str, value: Any) -> None:
    print(f"       {label}: {value}")


def _stop(message: str) -> None:
    print("\n" + "!" * 72)
    print(f"STOP: {message}")
    print("!" * 72)
    sys.exit(1)


# ==========================================================
# Step 0 — Pre-flight
# ==========================================================

def pre_flight() -> None:
    _header("STEP 0 — Pre-flight checks")

    # Postgres
    try:
        from utils.db import get_cursor
        with get_cursor() as cur:
            cur.execute("SELECT 1;")
            cur.fetchone()
        _check("Postgres reachable", True)
    except Exception as exc:
        _check("Postgres reachable", False, str(exc))
        _stop("Postgres pre-flight failed")

    # Kafka (port 9092). Not strictly required for Option B since we
    # bypass the bus, but still report — its absence signals stack health.
    try:
        with socket.create_connection(("localhost", 9092), timeout=2):
            pass
        _check("Kafka reachable at localhost:9092", True)
    except OSError as exc:
        _check("Kafka reachable at localhost:9092", False, str(exc))
        # NOT fatal for Option B — log and continue.

    # Firestore emulator
    try:
        with socket.create_connection(("localhost", 8080), timeout=2):
            pass
        _check("Firestore emulator at localhost:8080", True)
    except OSError as exc:
        _check("Firestore emulator at localhost:8080", False, str(exc))
        _stop(
            "Firestore emulator not reachable. Start with: "
            "firebase emulators:start --only firestore"
        )

    # OPENAI_API_KEY
    try:
        from agent.config import settings as agent_settings
        key = os.getenv("OPENAI_API_KEY") or agent_settings.OPENAI_API_KEY or ""
        if not key:
            _check("OPENAI_API_KEY set", False, "key is empty")
            _stop("OpenAI key missing")
        _check("OPENAI_API_KEY set", True, f"prefix={key[:7]}")
    except Exception as exc:
        _check("OPENAI_API_KEY set", False, str(exc))
        _stop("OpenAI key resolution failed")


# ==========================================================
# Step 1 — Option B prereq (REST poll → standalone Silver/Gold →
# momentum_vault). Falls back to Option C on zero markets.
# ==========================================================

# Sentinel embedded in the synthetic question used by the Option C
# fallback so the run log can prove which path actually ran.
_OPTION_C_QUESTION = (
    "Will the Federal Reserve cut rates before June 2026? "
    f"(sprint22-e2e-fallback-{RUN_ID})"
)


def prereq_polymarket_row() -> tuple[str, str, str]:
    """
    Returns:
        (external_reference_id, question, path_taken)
        where path_taken ∈ {"option_b_rest_poll", "option_c_sql_insert"}.
    """
    _header("STEP 1 — Polymarket prereq (Option B / fallback to Option C)")
    print("NOTE: this run uses Option B (standalone silver/gold processing,")
    print("      no Flink). Momentum-block deltas will be 0.0 — that is")
    print("      EXPECTED for Sprint 22; T22.11 verifies the question")
    print("      field + agent wiring, not momentum math.\n")

    try:
        return _option_b_rest_poll()
    except _NoMarketsAvailable as exc:
        print(f"\n[FALLBACK] Polymarket REST returned no qualifying markets: {exc}")
        print("[FALLBACK] Switching to Option C (direct SQL insert with")
        print("[FALLBACK] synthetic question for resolver verification).\n")
        return _option_c_direct_insert()
    except Exception as exc:
        print(f"\n[FALLBACK] Option B failed with {type(exc).__name__}: {exc}")
        traceback.print_exc()
        print("\n[FALLBACK] Switching to Option C (direct SQL insert).\n")
        return _option_c_direct_insert()


class _NoMarketsAvailable(Exception):
    pass


def _option_b_rest_poll() -> tuple[str, str, str]:
    """Option B: real REST poll → standalone silver → standalone gold →
    momentum_vault insert. Returns the inserted row's external_reference_id
    and question."""
    from ingestion.polymarket_producer import PolymarketProducer
    from processing.silver_job import process_polymarket_message
    from processing.gold_job import process_structured_metrics_message
    from persistence.momentum_vault import insert as mv_insert
    from utils.kafka_utils import build_bronze_message

    producer = PolymarketProducer()
    try:
        markets = producer._fetch_active_markets()
        if not markets:
            raise _NoMarketsAvailable(
                "Producer reported 0 markets passing the $10k volume filter"
            )
        _check(f"Fetched {len(markets)} active markets", True)

        # Find a market that has both a question and produces a usable
        # REST snapshot. Loop because some markets' /clob endpoint may 404.
        chosen_market = None
        raw_payload = None
        for market in markets[:10]:
            raw = producer._fetch_market_prices(market)
            if raw and raw.get("question"):
                chosen_market = market
                raw_payload = raw
                break
        if not raw_payload:
            raise _NoMarketsAvailable(
                "None of the first 10 markets produced a usable REST snapshot"
            )
        _check("Polymarket REST snapshot fetched", True,
               f"market={chosen_market.get('id')}")
        _info("Question", raw_payload["question"])

        # Wrap in Bronze envelope (the same shape silver_job's
        # process_polymarket_message expects).
        envelope = build_bronze_message(
            source_name="polymarket",
            source_endpoint="https://clob.polymarket.com/markets/<conditionId>",
            raw_payload=raw_payload,
        )

        # Silver — exercises T22.1 D1 silver_job patch (question
        # propagation into metadata_extension).
        topic, silver_record = process_polymarket_message(envelope)
        if topic != "process.silver.structured_metrics":
            raise RuntimeError(
                f"Expected silver routing to process.silver.structured_metrics, "
                f"got topic={topic!r}"
            )
        _check("Silver routing", True, f"topic={topic}")
        sm_question = silver_record.get("metadata_extension", {}).get("question", "")
        _check("T22.1 D1 silver_job patch — question in metadata_extension",
               bool(sm_question),
               f"value={sm_question!r}")

        # Gold — no Flink keyed state; pass [] for price_history.
        _topic2, gold_record = process_structured_metrics_message(
            silver_record, price_history=[],
        )
        _check("Gold enrichment", True)
        _info("Gold metadata_extension keys", list(gold_record.get("metadata_extension", {}).keys()))

        # Persist — the row that the resolver will match against.
        metric_id = mv_insert(gold_record)
        _check("momentum_vault.insert", True, f"metric_id={metric_id}")

        external_reference_id = silver_record["core_identity"]["external_reference_id"]
        return external_reference_id, sm_question, "option_b_rest_poll"
    finally:
        try:
            producer.close()
        except Exception:
            pass


def _option_c_direct_insert() -> tuple[str, str, str]:
    """Option C: bypass the pipeline. INSERT a polymarket row with a
    synthetic question directly. Used when Option B fails (Polymarket
    API unreachable, no qualifying markets, etc.)."""
    from psycopg2.extras import Json
    from utils.db import get_cursor

    external_reference_id = f"0xe2e-fallback-{RUN_ID}"
    question = _OPTION_C_QUESTION
    timestamp = datetime.now(timezone.utc)
    metric_id = str(uuid.uuid4())
    canonical_event_id = f"e2e-fallback-{RUN_ID}"

    sql = """
        INSERT INTO momentum_vault (
            metric_id, canonical_event_id, source_name,
            external_reference_id, current_value, unit, status,
            timestamp_utc, change_24h, change_7d, change_30d,
            is_new_market, metadata_extension
        ) VALUES (
            %s, %s, 'polymarket', %s, 0.42, 'USD', 'active',
            %s, 0.0, 0.0, 0.0, false, %s
        );
    """
    meta_ext = {"question": question, "whale_alert": False, "fallback": True}
    with get_cursor() as cur:
        cur.execute(sql, (
            metric_id, canonical_event_id, external_reference_id,
            timestamp, Json(meta_ext),
        ))
    _check("Option C direct SQL insert", True, f"metric_id={metric_id}")
    _info("external_reference_id", external_reference_id)
    _info("question", question)
    return external_reference_id, question, "option_c_sql_insert"


# ==========================================================
# Step 2 + Step 3 — Verify row + resolver match_score
# ==========================================================

def verify_row_and_resolver(
    external_reference_id: str,
    question: str,
) -> None:
    _header("STEP 2 + 3 — Verify row + resolver match_score")
    from persistence.momentum_vault import find_polymarket_market_by_question

    result = find_polymarket_market_by_question(question)
    if result is None:
        _check("Resolver finds the inserted row", False, "returned None")
        _stop(
            f"T22.1 resolver returned None for the verbatim question. "
            f"external_reference_id={external_reference_id!r}; "
            f"question={question!r}"
        )

    match_score = result.get("match_score")
    _check("Resolver finds the inserted row", True)
    _info("returned external_reference_id", result.get("external_reference_id"))
    _info("match_score", match_score)

    if match_score is not None and match_score < 0.99:
        # Surface as a soft warning — verbatim should be 1.0 but pg_trgm
        # may normalise differently for whitespace etc.
        print(
            f"       NOTE: match_score={match_score} < 0.99 on a verbatim "
            f"query. This is unexpected and worth investigating."
        )


# ==========================================================
# Step 4 — Initialise the local Firestore emulator client
# ==========================================================

def init_emulator() -> Any:
    _header("STEP 4 — Initialise Firestore emulator")
    os.environ.setdefault("FIRESTORE_EMULATOR_HOST", "localhost:8080")

    # Mirror tests/test_agent/conftest.py:emulator_db pattern. The
    # firebase_admin.initialize_app contract requires a credentials.Base
    # instance (NOT a google.auth.credentials.Credentials directly).
    import firebase_admin
    from firebase_admin import credentials, firestore
    import google.auth.credentials

    class _EmulatorCredentials(credentials.Base):
        def get_credential(self):
            return google.auth.credentials.AnonymousCredentials()

    firebase_admin._apps.clear()

    # Reset agent.firestore_client cached connection too.
    from agent import firestore_client as fc
    fc._db = None

    firebase_admin.initialize_app(
        _EmulatorCredentials(),
        {"projectId": "anizai-ai"},
    )
    db = firestore.client()
    _check("Emulator client initialised", True)
    return db


# ==========================================================
# Step 5 — Seed forecastQueries + run process_query
# ==========================================================

def seed_and_run(db: Any, question: str) -> str:
    _header("STEP 5 — Seed emulator + run process_query")

    session_id = f"e2e-sprint22-{RUN_ID}"
    _info("session_id", session_id)
    _info("question", question)

    # Seed sessions/{id} + forecastQueries/{id} per the server-contract
    # shape (same _seed_pending_query pattern as test_emulator_integration.py).
    from firebase_admin import firestore as fb_firestore

    db.collection("sessions").document(session_id).set({
        "status": "queued",
        "userId": "e2e-test-user",
        "createdAt": fb_firestore.SERVER_TIMESTAMP,
    })
    db.collection("forecastQueries").document(session_id).set({
        "queryId": f"q_{session_id}",
        "sessionId": session_id,
        "userId": "e2e-test-user",
        "question": question,
        "status": "pending",
        "createdAt": fb_firestore.SERVER_TIMESTAMP,
        "claimedAt": None,
        "claimedBy": None,
    })
    _check("Seeded queued forecastQueries doc", True)

    # Tell claim_session which worker we are.
    from agent.config import settings
    settings.AGENT_WORKER_ID = "e2e-sprint22-worker"

    print("\n>>> Running process_query(session_id) — real LLM call inbound...\n")
    t_start = time.monotonic()
    from agent.process_query import process_query
    try:
        process_query(session_id)
    except Exception as exc:
        elapsed = time.monotonic() - t_start
        print(f"\n[FATAL] process_query raised after {elapsed:.1f}s: {exc}")
        traceback.print_exc()
        _stop("Agent run failed — see Firestore dump for partial state")
    elapsed = time.monotonic() - t_start
    _check(f"process_query completed in {elapsed:.1f}s", True)
    return session_id


# ==========================================================
# Step 6 — Per-BI-card assertions
# ==========================================================

def assert_bi_cards(db: Any, session_id: str, expected_canonical_key: str) -> list[str]:
    """Returns a list of failure messages. Empty list = all pass."""
    _header("STEP 6 — Per-BI-card assertions")
    failures: list[str] = []

    # --- sessionResults/{id} ---
    result_doc = db.collection("sessionResults").document(session_id).get()
    if not result_doc.exists:
        failures.append(f"sessionResults/{session_id} does not exist")
        _check("sessionResults doc exists", False)
        return failures
    result = result_doc.to_dict()
    _check("sessionResults doc exists", True)

    # PredictionOverview
    final_probability = result.get("finalProbability")
    if not isinstance(final_probability, float) or not (0.0 <= final_probability <= 1.0):
        failures.append(f"finalProbability not a float in [0,1]: {final_probability!r}")
    _check("PredictionOverview: finalProbability is float in [0, 1]",
           isinstance(final_probability, float) and 0.0 <= final_probability <= 1.0,
           f"={final_probability}")

    key_factors = result.get("keyFactors") or []
    kf_ok = isinstance(key_factors, list) and 3 <= len(key_factors) <= 5
    if not kf_ok:
        failures.append(f"keyFactors len {len(key_factors)} not in [3,5]")
    _check("PredictionOverview: keyFactors len 3-5", kf_ok,
           f"={len(key_factors)}")

    # MarketComparison (the headline T22.3 + T22.7 verification)
    market_probability = result.get("marketProbability")
    market_comparison = result.get("marketComparison")
    insight = result.get("marketComparisonInsight", "")
    from agent.nodes.synthesize import NO_MARKET_CAPTION

    mp_ok = isinstance(market_probability, float) and 0.0 <= market_probability <= 1.0
    if not mp_ok:
        failures.append(
            f"Tier 1 marketProbability not a float in [0,1]: {market_probability!r}"
        )
    _check("MarketComparison: marketProbability is float in [0, 1] (Tier 1)",
           mp_ok, f"={market_probability}")

    mc_ok = (
        isinstance(market_comparison, list)
        and len(market_comparison) == 2
        and market_comparison[0].get("label") == "Anizai"
        and market_comparison[1].get("label") == "Polymarket"
    )
    if not mc_ok:
        failures.append(f"marketComparison shape unexpected: {market_comparison!r}")
    _check("MarketComparison: 2 entries [Anizai, Polymarket]", mc_ok)

    insight_ok = insight != NO_MARKET_CAPTION
    if not insight_ok:
        failures.append("marketComparisonInsight is NO_MARKET_CAPTION on Tier 1")
    _check("MarketComparison: insight is LLM-written (not the empty caption)",
           insight_ok)

    tier_ok = result.get("tier") == "tier_1"
    if not tier_ok:
        failures.append(f"tier={result.get('tier')!r}, expected tier_1")
    _check("tier == 'tier_1'", tier_ok)

    # --- sessions/{id} (T22.7) ---
    session = db.collection("sessions").document(session_id).get().to_dict()
    session_tier_ok = session.get("tier") == "tier_1"
    if not session_tier_ok:
        failures.append(f"session.tier={session.get('tier')!r}, expected tier_1")
    _check("session doc: tier == 'tier_1'", session_tier_ok)

    canonical_key = session.get("canonicalKey")
    canonical_ok = canonical_key == expected_canonical_key
    if not canonical_ok:
        failures.append(
            f"canonicalKey={canonical_key!r}, expected "
            f"{expected_canonical_key!r}"
        )
    _check(
        "T22.7: session.canonicalKey matches resolved market_slug",
        canonical_ok, f"={canonical_key}",
    )

    # --- evidence subcollection ---
    evidence_docs = list(
        db.collection("sessions").document(session_id)
          .collection("evidence").stream()
    )
    ev_ok = len(evidence_docs) >= 1
    if not ev_ok:
        failures.append("evidence subcollection empty")
    _check("EvidenceTimeline: evidence subcollection non-empty",
           ev_ok, f"count={len(evidence_docs)}")

    # --- predictionSeries subcollection (T22.4) ---
    prediction_docs = list(
        db.collection("sessions").document(session_id)
          .collection("predictionSeries").stream()
    )
    # In Option B/C runs, our prereq inserted ONE polymarket row. The
    # T22.4 pipeline reads that row's external_reference_id and calls
    # fetch_time_series(hours=720). The same row will appear in the
    # time-series window → predictionSeries has at least 1 doc.
    ps_ok = len(prediction_docs) >= 1
    if not ps_ok:
        failures.append(
            "predictionSeries subcollection empty — expected at least 1 doc "
            "from the prereq row's price_history"
        )
    _check("PredictionSeries: subcollection non-empty",
           ps_ok, f"count={len(prediction_docs)}")
    for snap in prediction_docs[:1]:  # spot-check one doc's shape
        doc = snap.to_dict()
        ts_ok = isinstance(doc.get("ts"), datetime)
        if not ts_ok:
            failures.append(
                f"predictionSeries.ts not a datetime: {type(doc.get('ts')).__name__}"
            )
        _check("  PredictionSeries: ts is Firestore Timestamp (D3)",
               ts_ok, f"got {type(doc.get('ts')).__name__}")
        for fld, expected in (
            ("confidence", 1.0),
            ("reasonType", "market"),
            ("evidenceIds", []),
        ):
            v_ok = doc.get(fld) == expected
            if not v_ok:
                failures.append(
                    f"predictionSeries.{fld}={doc.get(fld)!r}, expected {expected!r}"
                )

    # --- sentimentTimeSeries subcollection (T22.6) ---
    sentiment_docs = list(
        db.collection("sessions").document(session_id)
          .collection("sentimentTimeSeries").stream()
    )
    # Real vault: researcher + pulse may or may not have items within
    # the bucketing window. Empty is acceptable (FE renders gap state).
    _check(
        "SentimentAnalysis: subcollection inspected",
        True, f"count={len(sentiment_docs)}",
    )
    for snap in sentiment_docs[:1]:
        doc = snap.to_dict()
        ts_ok = isinstance(doc.get("ts"), datetime)
        if not ts_ok:
            failures.append(
                f"sentimentTimeSeries.ts not a datetime: {type(doc.get('ts')).__name__}"
            )
        _check("  SentimentAnalysis: ts is Firestore Timestamp (D3)",
               ts_ok, f"got {type(doc.get('ts')).__name__}")
        for side in ("expertSentiment", "publicSentiment"):
            v = doc.get(side)
            if v is not None and not (0.0 <= float(v) <= 1.0):
                failures.append(
                    f"sentimentTimeSeries.{side}={v!r} not in [0, 1]"
                )

    return failures


# ==========================================================
# Step 7 — Dump full Firestore state to JSON
# ==========================================================

def _json_default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    try:
        return str(value)
    except Exception:
        return repr(value)


def dump_firestore_state(db: Any, session_id: str) -> None:
    _header("STEP 7 — Dump Firestore state")
    state: dict[str, Any] = {
        "session_id": session_id,
        "run_id": RUN_ID,
        "dumped_at": datetime.now(timezone.utc).isoformat(),
    }

    session_doc = db.collection("sessions").document(session_id).get()
    state["session"] = session_doc.to_dict() if session_doc.exists else None

    fq_doc = db.collection("forecastQueries").document(session_id).get()
    state["forecastQuery"] = fq_doc.to_dict() if fq_doc.exists else None

    sr_doc = db.collection("sessionResults").document(session_id).get()
    state["sessionResult"] = sr_doc.to_dict() if sr_doc.exists else None

    for sub in ("evidence", "predictionSeries", "sentimentTimeSeries"):
        docs = list(
            db.collection("sessions").document(session_id)
              .collection(sub).stream()
        )
        state[sub] = [{"id": d.id, **d.to_dict()} for d in docs]

    DUMP_PATH.write_text(
        json.dumps(state, indent=2, default=_json_default),
        encoding="utf-8",
    )
    _check("Wrote Firestore dump", True, str(DUMP_PATH))


# ==========================================================
# Main
# ==========================================================

def main() -> int:
    print(f"Sprint 22 T22.11 E2E — run_id={RUN_ID}")

    pre_flight()
    external_reference_id, question, path_taken = prereq_polymarket_row()
    _info("Path taken", path_taken)

    verify_row_and_resolver(external_reference_id, question)

    db = init_emulator()
    session_id = seed_and_run(db, question)
    failures = assert_bi_cards(db, session_id, expected_canonical_key=external_reference_id)
    dump_firestore_state(db, session_id)

    _header("RESULT")
    if failures:
        print(f"FAIL — {len(failures)} assertion(s) failed:")
        for f in failures:
            print(f"  - {f}")
        print(f"\nFirestore dump: {DUMP_PATH}")
        return 1
    print("PASS — all assertions satisfied.")
    print(f"\nFirestore dump: {DUMP_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
