"""
Sprint 26 T26.3 — per-node latency measurement driver (Postgres-free).

CONSCIOUS DEVIATION from the ratified "run T20.11 + T21.12" mechanism (Advisor↔Ron
2026-07-18). The literal E2E drivers (`sprint20_e2e_run.py` / `sprint21_e2e_run.py`)
require a running worker + REAL Postgres vault purely to time `vault_query` — the
low-seconds parallel-DB node that is NOT the bottleneck. Same NODE COVERAGE is
obtained here without Postgres: emulator (Firestore) + REAL OpenAI + realistic-volume
MOCKED retrieval agents. `vault_query` is therefore a MOCK FLOOR (understated); the
authoritative real-vault + cold/warm p95 is the cloud baseline day-run (real Postgres
= the true KG-B-5 measurement).

Why realistic-volume (not empty) mocks (Advisor↔Ron): rate_evidence / synthesize /
generate_suggested_actions are token-volume-bound — empty evidence understates exactly
the dominant nodes and would trip sufficiency_check into the insufficient branch
(different graph path). The mocks return calibrated-volume synthetic evidence (item
counts + snippet lengths from a real Sprint-20 forecast), and the market mock injects
every `structured_intent.entities` term into a fred anomaly's `anomaly_flags` so
`sufficiency_check` stays on the SUFFICIENT path regardless of what real query_understand
extracts (it concatenates fred indicator/series/flags text for entity coverage).

Scenarios (one Tier-1 + one Tier-2 broad — NOT a redundant cold/warm second pass;
cold/warm is a vault-cache effect erased by mocking, deferred to the day-run):
  - tier1        : representative Polymarket-backed forecast (~11 evidence items)
  - tier2_broad  : high-volume freeform forecast (~15 items, no market anchor)

Pre-conditions:
  1. Firestore emulator up (localhost:8080 or FIRESTORE_EMULATOR_HOST).
  2. OPENAI_API_KEY set (infrastructure/.env) — this driver makes REAL OpenAI calls.

Usage (from data-pipeline/):
    data-pipeline\\venv\\Scripts\\python.exe -m tests.e2e.sprint26_latency_run

Output: per-node duration table (agentEvents durationMs + histogram delta) per
scenario + totals + the graph path taken. Feeds docs/B_hub/sprint26_latency_report.md.
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
os.environ.setdefault("FIRESTORE_EMULATOR_HOST", "localhost:8080")

# Windows consoles default to cp1252; force UTF-8 so output never crashes on a
# stray non-ASCII char (and results are still dumped to JSON regardless).
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_RESULTS_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sprint26_latency_results.json")

import firebase_admin
import google.auth.credentials
from firebase_admin import credentials, firestore


# ==========================================================
# Emulator init (AnonymousCredentials — matches conftest / e2e_sprint25)
# ==========================================================
class _EmulatorCreds(credentials.Base):
    def get_credential(self):
        return google.auth.credentials.AnonymousCredentials()


def _init_emulator():
    if not firebase_admin._apps:
        firebase_admin.initialize_app(_EmulatorCreds(), {"projectId": "anizai-ai"})
    return firestore.client()


# ==========================================================
# Synthetic evidence builders (calibrated to a real Sprint-20 forecast)
# ==========================================================
def _snippet(topic: str, i: int) -> str:
    """~250-char coherent topical text. rate_evidence truncates to 200 for the
    LLM, so length ≥200 gives a full representative snippet; content is topical
    so the real gpt-4o-mini / gpt-4o calls rate + synthesize normally."""
    return (
        f"Analysts tracking {topic} note that recent indicators point to meaningful "
        f"uncertainty in the outlook (item {i}). Several data releases this quarter "
        f"suggest {topic} could shift as macro and policy conditions evolve, with "
        f"market participants pricing in a range of scenarios over the coming months "
        f"and weighing both upside and downside risks to the central case."
    )


def _researcher_evidence(topic: str, n_articles: int) -> dict:
    articles = [
        {
            "signal_id": f"r-{i}",
            "source_platform": ["newsapi", "arxiv", "telegram"][i % 3],
            "publisher": ["reuters.com", "ArXiv", "@macrochannel"][i % 3],
            "title": f"{topic.title()}: outlook and drivers heading into the next quarter ({i})",
            "published_at": "2026-07-10T12:00:00Z",
            "executive_summary": f"Summary of {topic} dynamics and near-term catalysts ({i}).",
            "key_findings": [f"finding {i}a on {topic}", f"finding {i}b on {topic}"],
            "full_text_snippet": _snippet(topic, i),
            "impact_level": 3 + (i % 3),
            "reliability_score": 0.6 + 0.03 * (i % 5),
            "sentiment_score": 0.1 + 0.05 * (i % 4),
            "similarity": 0.82 - 0.01 * i,
            "evidence_weight": 0.7,
            "canonical_event_id": f"evt-{topic[:6]}",
        }
        for i in range(n_articles)
    ]
    return {
        "articles": articles,
        "source_diversity": {"newsapi_count": n_articles, "arxiv_count": 0, "telegram_count": 0},
        "recency_range": {"oldest": "2026-07-01T00:00:00Z", "newest": "2026-07-15T00:00:00Z"},
        "empty": n_articles == 0,
    }


def _pulse_evidence(topic: str, n_consensus: int, n_discussion: int) -> dict:
    consensus = [
        {
            "signal_id": f"pc-{i}",
            "market_id_ref": f"{topic.replace(' ', '-')}-market-{i}",
            "consensus_rating": 0.45 + 0.05 * (i % 4),
            "comment_volume_analyzed": 80 + 20 * i,
            "aggregation_window_hours": 168,
            "executive_summary": f"Market consensus on {topic}: mixed positioning with a slight lean ({i}).",
            "key_arguments_pro": [],
            "key_arguments_con": [],
            "similarity": 0.78 - 0.01 * i,
            "evidence_weight": 0.65,
        }
        for i in range(n_consensus)
    ]
    discussion = [
        {
            "signal_id": f"pd-{i}",
            "platform": "hackernews",
            "title": f"Community thread on {topic} implications ({i})",
            "points": 40 + 15 * i,
            "top_technical_insights": [
                f"insight {i}: {topic} could be sensitive to the next data print",
                f"insight {i}b: second-order effects of {topic} on adjacent markets",
            ],
            "community_sentiment": 0.0 + 0.1 * (i % 3),
            "published_at": "2026-07-12T09:00:00Z",
            "similarity": 0.74 - 0.01 * i,
            "evidence_weight": 0.55,
        }
        for i in range(n_discussion)
    ]
    return {
        "market_consensus": consensus,
        "community_discussion": discussion,
        "overall_sentiment": 0.12,
        "empty": (n_consensus + n_discussion) == 0,
    }


def _market_evidence(topic: str, entities: list, tier: str, n_fred: int) -> dict:
    # Entity-coverage guarantee (sufficiency_check): every entity term goes into a
    # fred anomaly's indicator_name + anomaly_flags, which _build_evidence_text reads.
    ent = [str(e) for e in (entities or []) if str(e).strip()]
    coverage = {
        "series_id": "COVERAGE",
        "indicator_name": " | ".join(ent) if ent else "macro indicator",
        "current_value": 2.5,
        "anomaly_flags": ent + ["high_magnitude_move"],
        "impact_level": 4,
        "change_7d": 0.3,
    }
    extra_fred = [
        {
            "series_id": f"FRED-{i}",
            "indicator_name": f"{topic} indicator {i}",
            "current_value": 1.5 + 0.2 * i,
            "anomaly_flags": ["trend_break"],
            "impact_level": 3,
            "change_7d": 0.1 * i,
        }
        for i in range(max(0, n_fred - 1))
    ]
    polymarket = None
    if tier == "tier_1":
        polymarket = {
            "current_odds": 0.58,
            "momentum": {"change_24h": 0.01, "change_7d": 0.03, "change_30d": -0.02},
            "price_history": [
                {"timestamp": "2026-07-%02dT00:00:00Z" % ((i % 28) + 1), "value": 0.5 + 0.001 * i}
                for i in range(100)
            ],
            "whale_alerts": [{"timestamp": "2026-07-05T00:00:00Z", "current_value": 0.61}],
            "market_slug": f"{topic.replace(' ', '-')}-2026",
        }
    return {
        "polymarket": polymarket,
        "linked_sources": [],
        "fred_anomalies": [coverage] + extra_fred,
        "google_trends": [{"keyword": e, "current_score": 45, "trend_direction": "stable", "hype_alert": False} for e in ent[:3]],
        "empty": False,
    }


def _seed(db, session_id: str, question: str) -> None:
    db.collection("sessions").document(session_id).set(
        {"status": "queued", "createdAt": firestore.SERVER_TIMESTAMP}
    )
    db.collection("forecastQueries").document(session_id).set(
        {
            "queryId": f"q_{session_id}",
            "sessionId": session_id,
            "userId": "lat-user",
            "question": question,
            "status": "pending",
            "createdAt": firestore.SERVER_TIMESTAMP,
            "claimedAt": None,
            "claimedBy": None,
        }
    )


# ==========================================================
# Metrics snapshot (26.4 histogram — per-run per-node delta)
# ==========================================================
_HIST_NODES = [
    "query_understand", "build_embedding", "vault_query", "sufficiency_check",
    "trigger_reactive_ingestion", "rate_evidence", "synthesize",
    "generate_suggested_actions", "write_to_firestore",
]


def _hist_sums():
    from prometheus_client import REGISTRY
    out = {}
    for n in _HIST_NODES:
        v = REGISTRY.get_sample_value("agent_node_duration_seconds_sum", {"node_name": n})
        out[n] = v if v is not None else 0.0
    return out


def _read_events(db, session_id: str) -> list:
    coll = db.collection("sessions").document(session_id).collection("agentEvents")
    evs = [d.to_dict() for d in coll.stream()]
    evs.sort(key=lambda e: e.get("sequence") or 0)
    return evs


# ==========================================================
# Scenario runner
# ==========================================================
def run_scenario(db, label, question, tier, n_articles, n_consensus, n_discussion, n_fred, topic):
    from agent import events as events_mod
    from agent.process_query import process_query

    session_id = f"lat_{label}_{uuid.uuid4().hex[:8]}"
    _seed(db, session_id, question)

    def mock_researcher(query_embedding, now=None):
        return _researcher_evidence(topic, n_articles)

    def mock_pulse(query_embedding, now=None):
        return _pulse_evidence(topic, n_consensus, n_discussion)

    def mock_market(polymarket_slug=None, canonical_event_id=None, entities=None,
                    now=None, raw_question="", has_market_question_intent=False):
        return _market_evidence(topic, entities, tier, n_fred)

    fake_future = SimpleNamespace(get=lambda timeout=None: SimpleNamespace(offset=0, partition=0))
    fake_producer = SimpleNamespace(send=lambda *a, **k: fake_future)

    hist_before = _hist_sums()
    t0 = time.monotonic()
    with (
        patch("agent.agents.researcher.run", side_effect=mock_researcher),
        patch("agent.agents.pulse_analyst.run", side_effect=mock_pulse),
        patch("agent.agents.market_bridge.run", side_effect=mock_market),
        patch("agent.nodes.trigger_reactive_ingestion._get_producer", return_value=fake_producer),
        patch("agent.nodes.trigger_reactive_ingestion._log_attempt", lambda *a, **k: None),
    ):
        process_query(session_id)
    events_mod.drain(5.0)
    total_s = time.monotonic() - t0
    hist_after = _hist_sums()

    events = _read_events(db, session_id)
    result = db.collection("sessionResults").document(session_id).get().to_dict()
    session = db.collection("sessions").document(session_id).get().to_dict() or {}

    hist_delta_ms = {
        n: round((hist_after[n] - hist_before[n]) * 1000.0, 1)
        for n in _HIST_NODES if (hist_after[n] - hist_before[n]) > 1e-9
    }
    event_ms = {e.get("type"): e.get("durationMs") for e in events}
    path = [e.get("type") for e in events]

    return {
        "label": label,
        "question": question,
        "tier": tier,
        "session_id": session_id,
        "total_s": round(total_s, 2),
        "status": session.get("status"),
        "reached_synthesis": result is not None,
        "n_evidence": n_articles + n_consensus + n_discussion + n_fred,
        "sufficient_path": "trigger_reactive_ingestion" not in path,
        "path": path,
        "event_ms": event_ms,
        "hist_delta_ms": hist_delta_ms,
    }


def _print_scenario(r):
    print("\n" + "=" * 72)
    print(f"SCENARIO: {r['label']}  tier={r['tier']}  total={r['total_s']}s  "
          f"status={r['status']}  reached_synthesis={r['reached_synthesis']}")
    print(f"  question: {r['question']!r}")
    print(f"  evidence items: {r['n_evidence']}  sufficient_path={r['sufficient_path']}")
    print(f"  path: {' -> '.join(str(p) for p in r['path'])}")
    print(f"  {'node':<28}{'agentEvents ms':>16}{'histogram ms':>16}")
    print("  " + "-" * 58)
    all_nodes = list(dict.fromkeys(list(r["event_ms"].keys()) + list(r["hist_delta_ms"].keys())))
    for n in all_nodes:
        ev = r["event_ms"].get(n)
        hd = r["hist_delta_ms"].get(n)
        print(f"  {n:<28}{('' if ev is None else str(ev)):>16}{('' if hd is None else str(hd)):>16}")


def main() -> int:
    db = _init_emulator()

    scenarios = [
        # label, question, tier, n_articles, n_consensus, n_discussion, n_fred, topic
        ("tier1", "Will the Federal Reserve cut interest rates by Q2 2026?", "tier_1", 5, 3, 1, 2, "the Federal Reserve interest rate decision"),
        ("tier2_broad", "Will the United States enter a recession in 2026?", "tier_2", 6, 4, 3, 2, "the United States recession risk"),
    ]

    results = []
    for (label, q, tier, na, nc, nd, nf, topic) in scenarios:
        print(f"\n[lat] running {label}: {q!r} (REAL OpenAI, emulator, mocked agents) ...")
        r = run_scenario(db, label, q, tier, na, nc, nd, nf, topic)
        results.append(r)
        _print_scenario(r)
        if not r["reached_synthesis"]:
            print(f"  WARNING: {label} did NOT reach synthesis (status={r['status']}) - "
                  f"likely clarification; per-node token-bound timings are INVALID for this run.")
        # Durable dump after EACH scenario so a later crash never loses data.
        with open(_RESULTS_JSON, "w", encoding="utf-8") as fh:
            json.dump(results, fh, indent=2)

    print("\n" + "=" * 72)
    print("SUMMARY (totals):")
    for r in results:
        print(f"  {r['label']:<14} total={r['total_s']}s  reached_synthesis={r['reached_synthesis']}  "
              f"sufficient_path={r['sufficient_path']}")
    print(f"\n[lat] results written to {_RESULTS_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
