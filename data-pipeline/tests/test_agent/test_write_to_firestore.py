"""
Gate 1 tests for `agent/nodes/write_to_firestore.py` (Sprint 20 T20.5).

Strategy: pure-mock at the firestore_client function boundary. The
node never touches Firestore directly — every write goes through a
named helper in agent.firestore_client, so patching those is
sufficient.

Coverage:
- write order: subcollections → sessionResults → status='done'
- session.status='done' fires AFTER subcollections + sessionResults
- frontend `type` field added to evidence dicts via SOURCE_TYPE mapping
- evidence with unknown source_type is dropped (defensive)
- empty evidence_trail still issues the (zero-doc) batch + status writes
- predictionSeries / sentimentTimeSeries are empty lists for Sprint 20
- raw_question NOT required (synthesize already produced result)
- missing session_id → AgentProcessingError
- missing synthesis_result → AgentProcessingError
- mid-batch failure (write_evidence_batch raises) → propagates to runner
- session_id == query_doc_id contract (one id used for both writes)
"""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from agent.errors import AgentProcessingError
from agent.nodes import write_to_firestore


# ==========================================================
# Helpers
# ==========================================================
def _evidence_item(
    *,
    evidence_id: str = "ev-1",
    source_type: str = "vault_news",
) -> dict:
    """Minimal post-synthesis EvidenceItem dict shape."""
    return {
        "evidence_id": evidence_id,
        "source_type": source_type,
        "origin": "knowledge_vault",
        "title": "Title",
        "snippet": "snippet",
        "url": None,
        "source_domain": "reuters.com",
        "published_at": None,
        "fetched_at": None,
        "relevance_score": 0.7,
        "credibility_tier": "tier_1",
        "recency_weight": 0.9,
        "used_in_answer": True,
        "impact_on_forecast": "increases",
        "impact_magnitude": 0.6,
        "is_key_evidence": True,
        "rank": 1,
        "justification": "ok",
    }


def _state(**overrides) -> dict:
    base = {
        "session_id": "s1",
        "synthesis_result": {"finalProbability": 0.7, "tier": "tier_1"},
    }
    base.update(overrides)
    return base


# ==========================================================
# Shared fixture — patches every firestore_client call site at
# `agent.firestore_client.<name>` (where the node looks them up
# via the module import).
# ==========================================================
@pytest.fixture
def mocked_firestore():
    """Yields a manager mock + per-function child mocks. The manager's
    .method_calls list gives a single ordered cross-mock call sequence
    so tests can assert write order across multiple firestore helpers."""
    with (
        patch("agent.nodes.write_to_firestore.firestore_client.write_evidence_batch") as ev,
        patch("agent.nodes.write_to_firestore.firestore_client.write_prediction_series") as ps,
        patch("agent.nodes.write_to_firestore.firestore_client.write_sentiment_time_series") as sts,
        patch("agent.nodes.write_to_firestore.firestore_client.write_session_result") as sr,
        patch("agent.nodes.write_to_firestore.firestore_client.update_session_status") as ss,
        patch("agent.nodes.write_to_firestore.firestore_client.update_query_status") as qs,
    ):
        # Default returns: helpers report counts of items written
        ev.return_value = 0
        ps.return_value = 0
        sts.return_value = 0

        manager = MagicMock()
        manager.attach_mock(ev, "evidence")
        manager.attach_mock(ps, "prediction_series")
        manager.attach_mock(sts, "sentiment_time_series")
        manager.attach_mock(sr, "session_result")
        manager.attach_mock(ss, "session_status")
        manager.attach_mock(qs, "query_status")
        yield manager, ev, ps, sts, sr, ss, qs


# ==========================================================
# Happy path — write order
# ==========================================================
def test_writes_subcollections_before_session_result_before_done(mocked_firestore):
    """frontend-integration skill: status='done' is the frontend's
    'render now' signal. Subcollections must land first, then
    sessionResults, then status='done'. Pin the order."""
    manager, *_ = mocked_firestore
    write_to_firestore.run(_state(evidence_trail=[_evidence_item()]))

    # Expected order: evidence → predictionSeries → sentimentTimeSeries
    # → sessionResults → session.status=done → forecastQueries.status=done
    # Sprint 21 T21.8: session_status receives tier=None since the test
    # fixture has no top-level tier field in state (write_to_firestore
    # reads state.get("tier"), which is None in _state() with no tier
    # override).
    # Sprint 22 T22.7: session_status now also receives canonical_key=None
    # since the test fixture has no market_evidence.polymarket payload
    # (Tier 2 path → explicit None per Convention B).
    assert manager.method_calls == [
        call.evidence("s1", [{**_evidence_item(), "type": "news"}]),
        call.prediction_series("s1", []),
        call.sentiment_time_series("s1", []),
        call.session_result("s1", {"finalProbability": 0.7, "tier": "tier_1"}),
        call.session_status("s1", "done", tier=None, canonical_key=None),
        call.query_status("s1", "done"),
    ]


def test_returns_identity_echo_of_errors_field(mocked_firestore):
    """write_to_firestore is conceptually a sink (its product is the
    Firestore writes), but LangGraph requires every node to write at
    least one state field. The node echoes `errors` as an identity
    write — satisfies the framework without mutating state."""
    out = write_to_firestore.run(_state(evidence_trail=[_evidence_item()]))
    # Only the errors field is written; it's an identity copy of state.errors
    assert out == {"errors": []}


def test_identity_echo_preserves_existing_errors(mocked_firestore):
    """If state.errors has accumulated entries from upstream nodes, the
    echo must preserve them (not clobber to empty)."""
    out = write_to_firestore.run(_state(
        evidence_trail=[_evidence_item()],
        errors=["upstream warning"],
    ))
    assert out == {"errors": ["upstream warning"]}


# ==========================================================
# Frontend `type` mapping
# ==========================================================
@pytest.mark.parametrize("source_type,expected_type", [
    ("vault_news", "news"),
    ("vault_telegram", "social"),
    ("vault_market", "market"),
    ("vault_arxiv", "expert"),
    ("vault_hackernews", "social"),
    ("vault_fred", "market"),
])
def test_frontend_type_added_per_source_type(mocked_firestore, source_type, expected_type):
    """Each EvidenceItem's source_type is mapped to the frontend's
    filter-tab vocab via SOURCE_TYPE_TO_FRONTEND_TYPE. Pin every value."""
    _, ev, *_ = mocked_firestore
    item = _evidence_item(source_type=source_type)
    write_to_firestore.run(_state(evidence_trail=[item]))

    written = ev.call_args.args[1]
    assert written[0]["type"] == expected_type
    # Original fields preserved
    assert written[0]["evidence_id"] == item["evidence_id"]


def test_unknown_source_type_dropped_with_warning(mocked_firestore, caplog):
    """A source_type that's not in the mapping → log + drop the item.
    Avoids writing a doc with no `type` field that breaks filter UI."""
    _, ev, *_ = mocked_firestore
    bad_item = _evidence_item(source_type="vault_unknown_kind")
    good_item = _evidence_item(evidence_id="ev-good", source_type="vault_news")

    write_to_firestore.run(_state(evidence_trail=[bad_item, good_item]))

    # Only the good item lands in the batch
    written = ev.call_args.args[1]
    assert len(written) == 1
    assert written[0]["evidence_id"] == "ev-good"


# ==========================================================
# Sprint 20 empty subcollections
# ==========================================================
def test_prediction_series_is_empty_for_sprint_20(mocked_firestore):
    """KG-PHASE8-12: no polymarket data → no time series → write [].
    Frontend renders empty state for PredictionOverview BI card."""
    _, _, ps, *_ = mocked_firestore
    write_to_firestore.run(_state())
    assert ps.call_args == call("s1", [])


def test_sentiment_time_series_is_empty_for_sprint_20(mocked_firestore):
    """Q5 default: empty for Sprint 20. Sprint 22+ may add points."""
    _, _, _, sts, *_ = mocked_firestore
    write_to_firestore.run(_state())
    assert sts.call_args == call("s1", [])


def test_empty_evidence_trail_still_runs_full_write_sequence(mocked_firestore):
    """Cold-start: vault returned nothing → evidence_trail empty.
    The node still issues all 6 helper calls (with empty lists) so
    the session lifecycle completes correctly."""
    manager, *_ = mocked_firestore
    write_to_firestore.run(_state(evidence_trail=[]))

    method_names = [c[0] for c in manager.method_calls]
    assert method_names == [
        "evidence", "prediction_series", "sentiment_time_series",
        "session_result", "session_status", "query_status",
    ]


# ==========================================================
# session_id usage
# ==========================================================
def test_session_id_used_for_both_session_and_query_status(mocked_firestore):
    """Per server contract (session.repository.ts:347), session_id ==
    query_doc_id. write_to_firestore uses state.session_id for BOTH
    sessions/{id} and forecastQueries/{id} writes — pin this."""
    _, _, _, _, _, ss, qs = mocked_firestore
    write_to_firestore.run(_state(session_id="abc-123"))

    # Sprint 22 T22.7: success-path now also passes canonical_key
    # (None here because the test state has no market_evidence.polymarket).
    ss.assert_called_once_with(
        "abc-123", "done", tier=None, canonical_key=None,
    )
    qs.assert_called_once_with("abc-123", "done")


def test_session_id_used_for_subcollection_paths(mocked_firestore):
    """All three subcollection writes are scoped to the same session_id."""
    _, ev, ps, sts, *_ = mocked_firestore
    write_to_firestore.run(_state(session_id="xyz-789", evidence_trail=[_evidence_item()]))

    assert ev.call_args.args[0] == "xyz-789"
    assert ps.call_args.args[0] == "xyz-789"
    assert sts.call_args.args[0] == "xyz-789"


# ==========================================================
# Input validation
# ==========================================================
def test_missing_session_id_raises(mocked_firestore):
    _, *firestore_mocks = mocked_firestore
    with pytest.raises(AgentProcessingError, match="session_id"):
        write_to_firestore.run({"synthesis_result": {"finalProbability": 0.5}})

    # No firestore writes attempted on validation failure
    for mock in firestore_mocks:
        mock.assert_not_called()


def test_missing_synthesis_result_raises(mocked_firestore):
    _, *firestore_mocks = mocked_firestore
    with pytest.raises(AgentProcessingError, match="synthesis_result"):
        write_to_firestore.run({"session_id": "s1"})

    for mock in firestore_mocks:
        mock.assert_not_called()


def test_empty_synthesis_result_dict_raises(mocked_firestore):
    """A dict that exists but is empty (falsy) is treated the same as
    missing — synthesize should never produce an empty result."""
    _, *firestore_mocks = mocked_firestore
    with pytest.raises(AgentProcessingError, match="synthesis_result"):
        write_to_firestore.run({"session_id": "s1", "synthesis_result": {}})


# ==========================================================
# Mid-batch failure — propagates to runner
# ==========================================================
def test_evidence_batch_failure_propagates(mocked_firestore):
    """Per the user's added Gate 1 requirement: write_to_firestore
    raising mid-batch must propagate so process_query._mark_failed
    can clean up. Pin that the failure short-circuits — later writes
    don't happen."""
    _, ev, ps, sts, sr, ss, qs = mocked_firestore
    ev.side_effect = RuntimeError("firestore unavailable")

    with pytest.raises(RuntimeError, match="firestore unavailable"):
        write_to_firestore.run(_state(evidence_trail=[_evidence_item()]))

    # Subsequent writes never fire
    ps.assert_not_called()
    sts.assert_not_called()
    sr.assert_not_called()
    ss.assert_not_called()
    qs.assert_not_called()


def test_session_result_failure_propagates_with_no_done_status(mocked_firestore):
    """If sessionResults write fails, the status='done' transitions
    must NOT happen — frontend would render 'done' against an absent
    sessionResults doc otherwise."""
    _, _, _, _, sr, ss, qs = mocked_firestore
    sr.side_effect = RuntimeError("write failed")

    with pytest.raises(RuntimeError, match="write failed"):
        write_to_firestore.run(_state(evidence_trail=[_evidence_item()]))

    ss.assert_not_called()
    qs.assert_not_called()


def test_session_status_failure_short_circuits_query_status(mocked_firestore):
    """If session.status='done' write fails, forecastQueries 'done'
    write must NOT happen. Both docs landing in inconsistent state is
    cleaner than one done + one failed-mid-status."""
    _, _, _, _, _, ss, qs = mocked_firestore
    ss.side_effect = RuntimeError("status flip failed")

    with pytest.raises(RuntimeError, match="status flip failed"):
        write_to_firestore.run(_state(evidence_trail=[_evidence_item()]))

    qs.assert_not_called()


# ==========================================================
# Sprint 22 T22.4 — predictionSeries wiring
# ==========================================================

from datetime import datetime, timezone


def _in_state_price_history_point(
    *,
    timestamp: str = "2026-05-23T10:00:00+00:00",
    value: float = 0.62,
) -> dict:
    """
    Match the shape produced by market_bridge._price_history_point —
    ISO 8601 string + numeric value. Sprint 22 D1/D3: upstream shape is
    deliberately platform-agnostic; the Firestore adapter renames at the
    write site.
    """
    return {"timestamp": timestamp, "value": value}


def _market_evidence_with_history(
    *, price_history: list[dict] | None = None,
) -> dict:
    """Match the polymarket_payload shape produced by T22.2's
    `_pack_polymarket_payload` — agent-state representation."""
    return {
        "polymarket": {
            "current_odds": 0.62,
            "momentum": {"change_24h": 0.0, "change_7d": 0.0, "change_30d": 0.0},
            "price_history": price_history if price_history is not None else [],
            "whale_alerts": [],
            "market_slug": "0xfixture_condition_id",
        }
    }


class TestPredictionSeriesWiring:
    """
    Sprint 22 T22.4: write_to_firestore must shape
    `market_evidence.polymarket.price_history` into the PredictionPoint
    schema the Express BFF reads, and pass it to
    `firestore_client.write_prediction_series`. Tier 2 (no polymarket
    payload) preserves the hardcoded empty-list behavior.
    """

    def test_tier_1_happy_path_shapes_three_points_through_adapter(
        self, mocked_firestore,
    ):
        """
        3 in-state points → 3 shaped docs with the correct field names,
        order preserved, all schema fields populated.
        """
        _, _, ps, *_ = mocked_firestore
        history = [
            _in_state_price_history_point(
                timestamp="2026-05-21T10:00:00+00:00", value=0.55,
            ),
            _in_state_price_history_point(
                timestamp="2026-05-22T10:00:00+00:00", value=0.60,
            ),
            _in_state_price_history_point(
                timestamp="2026-05-23T10:00:00+00:00", value=0.62,
            ),
        ]
        write_to_firestore.run(_state(
            evidence_trail=[_evidence_item()],
            market_evidence=_market_evidence_with_history(price_history=history),
        ))

        assert ps.call_count == 1
        positional_args = ps.call_args.args
        assert positional_args[0] == "s1"
        shaped = positional_args[1]
        assert len(shaped) == 3
        # First doc — field-by-field
        assert shaped[0]["ts"] == datetime(2026, 5, 21, 10, 0, 0, tzinfo=timezone.utc)
        assert shaped[0]["probability"] == 0.55
        assert shaped[0]["confidence"] == 1.0
        assert shaped[0]["reasonType"] == "market"
        assert shaped[0]["evidenceIds"] == []
        # Order preserved
        assert [p["probability"] for p in shaped] == [0.55, 0.60, 0.62]

    def test_tier_2_no_market_evidence_passes_empty_list(self, mocked_firestore):
        """
        No `market_evidence` in state → adapter never runs →
        `write_prediction_series` called with []. Regression on existing
        Sprint 20 behavior.
        """
        _, _, ps, *_ = mocked_firestore
        write_to_firestore.run(_state(evidence_trail=[_evidence_item()]))
        ps.assert_called_once_with("s1", [])

    def test_polymarket_present_but_history_empty_passes_empty_list(
        self, mocked_firestore,
    ):
        """
        Polymarket payload exists but price_history=[] (e.g., a market
        with no observations in the 720-hour window). Adapter receives
        empty list, produces empty list, helper writes zero docs.
        """
        _, _, ps, *_ = mocked_firestore
        write_to_firestore.run(_state(
            evidence_trail=[_evidence_item()],
            market_evidence=_market_evidence_with_history(price_history=[]),
        ))
        ps.assert_called_once_with("s1", [])

    def test_polymarket_payload_missing_price_history_key_defaults_to_empty(
        self, mocked_firestore,
    ):
        """
        Defensive: polymarket payload exists but lacks the
        `price_history` key entirely (contract violation since
        _pack_polymarket_payload always sets it). Must not crash; emit
        empty list.
        """
        _, _, ps, *_ = mocked_firestore
        market_evidence = {
            "polymarket": {
                "current_odds": 0.62,
                "momentum": {},
                # price_history omitted intentionally
                "whale_alerts": [],
                "market_slug": "0xabc",
            }
        }
        write_to_firestore.run(_state(
            evidence_trail=[_evidence_item()],
            market_evidence=market_evidence,
        ))
        ps.assert_called_once_with("s1", [])

    def test_write_order_predictionseries_after_evidence_before_status(
        self, mocked_firestore,
    ):
        """
        T22.4 regression: predictionSeries write must still fire AFTER
        evidence and BEFORE sessionResults/status=done. Protects the
        frontend's "render now" contract — status='done' triggers reads
        of subcollections, so subcollections must land first.
        """
        manager, *_ = mocked_firestore
        history = [_in_state_price_history_point()]
        write_to_firestore.run(_state(
            evidence_trail=[_evidence_item()],
            market_evidence=_market_evidence_with_history(price_history=history),
        ))

        method_names = [c[0] for c in manager.method_calls]
        # Order: evidence → prediction_series → sentiment_time_series
        #        → session_result → session_status → query_status
        assert method_names == [
            "evidence",
            "prediction_series",
            "sentiment_time_series",
            "session_result",
            "session_status",
            "query_status",
        ]

    def test_adapter_drops_points_with_empty_or_unparseable_timestamp(
        self, mocked_firestore,
    ):
        """
        Defensive: malformed entries (empty `timestamp` field,
        unparseable ISO string) are dropped silently rather than
        crashing the whole subcollection write. The remaining
        well-formed points still land. Production path produces
        well-formed strings; this guards against future callers.
        """
        _, _, ps, *_ = mocked_firestore
        history = [
            _in_state_price_history_point(
                timestamp="", value=0.50,                              # empty → drop
            ),
            _in_state_price_history_point(
                timestamp="2026-05-23T10:00:00+00:00", value=0.62,     # ok → keep
            ),
            _in_state_price_history_point(
                timestamp="not-an-iso-string", value=0.70,             # unparseable → drop
            ),
            _in_state_price_history_point(
                timestamp="2026-05-24T10:00:00+00:00", value=0.65,     # ok → keep
            ),
        ]
        write_to_firestore.run(_state(
            evidence_trail=[_evidence_item()],
            market_evidence=_market_evidence_with_history(price_history=history),
        ))

        shaped = ps.call_args.args[1]
        assert len(shaped) == 2
        assert [p["probability"] for p in shaped] == [0.62, 0.65]

    def test_adapter_preserves_tz_aware_datetime_via_utc_fallback(
        self, mocked_firestore,
    ):
        """
        Critical: `ts` must arrive at Firestore as a tz-aware datetime.
        Naive datetimes get silently treated as local time by some
        Firestore SDK versions, which corrupts the timeline. Production
        path (psycopg2 TIMESTAMPTZ → tz-aware datetime → .isoformat())
        always includes the offset, but the adapter has an explicit UTC
        fallback for any future caller that bypasses _price_history_point.

        Pin: ISO string WITHOUT offset → adapter attaches timezone.utc.
        """
        _, _, ps, *_ = mocked_firestore
        history = [
            _in_state_price_history_point(
                timestamp="2026-05-23T10:00:00",       # naive (no offset)
                value=0.62,
            ),
            _in_state_price_history_point(
                timestamp="2026-05-23T11:00:00+00:00",  # tz-aware with offset
                value=0.63,
            ),
            _in_state_price_history_point(
                timestamp="2026-05-23T12:00:00Z",       # Z-suffixed tz-aware
                value=0.64,
            ),
        ]
        write_to_firestore.run(_state(
            evidence_trail=[_evidence_item()],
            market_evidence=_market_evidence_with_history(price_history=history),
        ))

        shaped = ps.call_args.args[1]
        assert len(shaped) == 3
        # Every entry's ts must be tz-aware
        for entry in shaped:
            assert entry["ts"].tzinfo is not None, (
                f"naive datetime would corrupt the Firestore timeline: {entry}"
            )
        # All three should equal the same wall-clock moments in UTC
        assert shaped[0]["ts"] == datetime(2026, 5, 23, 10, 0, 0, tzinfo=timezone.utc)
        assert shaped[1]["ts"] == datetime(2026, 5, 23, 11, 0, 0, tzinfo=timezone.utc)
        assert shaped[2]["ts"] == datetime(2026, 5, 23, 12, 0, 0, tzinfo=timezone.utc)


# ==========================================================
# Sprint 22 T22.6 — sentimentTimeSeries wiring
# ==========================================================

# Fixed reference instant for direct-helper tests — chosen UTC afternoon
# so today_start is unambiguously the same calendar date.
SENTIMENT_NOW = datetime(2026, 5, 23, 14, 0, 0, tzinfo=timezone.utc)


def _researcher_article(
    *,
    published_at: str,
    sentiment_score: float,
) -> dict:
    """Mirror the shape `researcher.run()` produces for one article."""
    return {
        "signal_id": "sig-r1",
        "source_platform": "newsapi",
        "title": "T",
        "published_at": published_at,
        "executive_summary": "",
        "key_findings": [],
        "full_text_snippet": "",
        "impact_level": 0,
        "reliability_score": 0.5,
        "sentiment_score": sentiment_score,
        "similarity": 0.5,
        "evidence_weight": 0.5,
        "canonical_event_id": "",
    }


def _hackernews_discussion(
    *,
    published_at: str,
    community_sentiment: float,
) -> dict:
    """Mirror the shape `pulse_analyst._pack_hackernews()` produces post-D5."""
    return {
        "signal_id": "sig-hn1",
        "platform": "hackernews",
        "title": "T",
        "points": 10,
        "top_technical_insights": [],
        "community_sentiment": community_sentiment,
        "published_at": published_at,
        "similarity": 0.5,
        "evidence_weight": 0.3,
    }


class TestSentimentTimeSeriesWiring:
    """
    Sprint 22 T22.6: write_to_firestore must bucket Expert + Public
    sentiment streams, merge per bucket-date, normalize [-1, 1] → [0, 1],
    and emit docs the Express BFF can decode. Per D6 (a1): emit doc when
    EITHER source has data; missing side written as None.

    Most tests call _shape_sentiment_time_series directly with a fixed
    `now` for reproducibility. The order-regression test goes through
    write_to_firestore.run.
    """

    # 1
    def test_both_sources_populated_overlapping_bucket(self):
        """
        Same date has both Expert and Public items → one doc with both
        sentiments populated, ISO `date` field, tz-aware `ts`.
        """
        researcher_evidence = {
            "articles": [
                _researcher_article(
                    published_at="2026-05-21T10:00:00+00:00",
                    sentiment_score=0.4,   # → normalized (0.4 + 1) / 2 = 0.7
                ),
            ],
        }
        pulse_evidence = {
            "community_discussion": [
                _hackernews_discussion(
                    published_at="2026-05-21T12:00:00+00:00",
                    community_sentiment=-0.6,  # → normalized (-0.6 + 1) / 2 = 0.2
                ),
            ],
        }
        docs = write_to_firestore._shape_sentiment_time_series(
            researcher_evidence=researcher_evidence,
            pulse_evidence=pulse_evidence,
            now=SENTIMENT_NOW,
        )
        assert len(docs) == 1
        doc = docs[0]
        assert doc["ts"] == datetime(2026, 5, 21, 0, 0, 0, tzinfo=timezone.utc)
        assert doc["date"] == "2026-05-21"
        assert doc["expertSentiment"] == pytest.approx(0.7)
        assert doc["publicSentiment"] == pytest.approx(0.2)
        assert doc["expertUpper"] is None
        assert doc["expertLower"] is None

    # 2 (FLIPPED per Ron's a1 decision)
    def test_expert_only_emits_docs_with_public_none(self):
        """
        Per D6 (a1): Expert has data, Public empty → emit doc for each
        Expert-populated bucket; publicSentiment=None. Documents that
        the (a3) strict-both behavior was rejected.
        """
        researcher_evidence = {
            "articles": [
                _researcher_article(
                    published_at="2026-05-20T10:00:00+00:00",
                    sentiment_score=0.0,    # neutral → 0.5
                ),
                _researcher_article(
                    published_at="2026-05-22T10:00:00+00:00",
                    sentiment_score=0.5,    # → 0.75
                ),
            ],
        }
        pulse_evidence = {"community_discussion": []}

        docs = write_to_firestore._shape_sentiment_time_series(
            researcher_evidence=researcher_evidence,
            pulse_evidence=pulse_evidence,
            now=SENTIMENT_NOW,
        )
        assert len(docs) == 2
        dates = [d["date"] for d in docs]
        assert dates == ["2026-05-20", "2026-05-22"]
        for d in docs:
            assert d["expertSentiment"] is not None
            assert d["publicSentiment"] is None

    # 3 (FLIPPED per Ron's a1 decision)
    def test_public_only_emits_docs_with_expert_none(self):
        """Mirror of test 2 for the Public-only path."""
        researcher_evidence = {"articles": []}
        pulse_evidence = {
            "community_discussion": [
                _hackernews_discussion(
                    published_at="2026-05-20T10:00:00+00:00",
                    community_sentiment=-0.8,   # → 0.1
                ),
                _hackernews_discussion(
                    published_at="2026-05-22T10:00:00+00:00",
                    community_sentiment=0.4,    # → 0.7
                ),
            ],
        }
        docs = write_to_firestore._shape_sentiment_time_series(
            researcher_evidence=researcher_evidence,
            pulse_evidence=pulse_evidence,
            now=SENTIMENT_NOW,
        )
        assert len(docs) == 2
        for d in docs:
            assert d["expertSentiment"] is None
            assert d["publicSentiment"] is not None

    # 4
    def test_both_empty_emits_zero_docs(self):
        """No data on either source → no docs emitted at all."""
        docs = write_to_firestore._shape_sentiment_time_series(
            researcher_evidence={"articles": []},
            pulse_evidence={"community_discussion": []},
            now=SENTIMENT_NOW,
        )
        assert docs == []

    # 5
    def test_scale_normalization_formula(self):
        """
        Pin the `(x + 1) / 2` formula explicitly. Three reference points:
        -1.0 → 0.0, 0.0 → 0.5, +1.0 → 1.0. Both Expert and Public sides
        use the same formula.
        """
        researcher_evidence = {
            "articles": [
                _researcher_article(
                    published_at="2026-05-20T10:00:00+00:00",
                    sentiment_score=-1.0,
                ),
                _researcher_article(
                    published_at="2026-05-21T10:00:00+00:00",
                    sentiment_score=0.0,
                ),
                _researcher_article(
                    published_at="2026-05-22T10:00:00+00:00",
                    sentiment_score=1.0,
                ),
            ],
        }
        pulse_evidence = {
            "community_discussion": [
                _hackernews_discussion(
                    published_at="2026-05-20T10:00:00+00:00",
                    community_sentiment=-1.0,
                ),
                _hackernews_discussion(
                    published_at="2026-05-21T10:00:00+00:00",
                    community_sentiment=0.0,
                ),
                _hackernews_discussion(
                    published_at="2026-05-22T10:00:00+00:00",
                    community_sentiment=1.0,
                ),
            ],
        }
        docs = write_to_firestore._shape_sentiment_time_series(
            researcher_evidence=researcher_evidence,
            pulse_evidence=pulse_evidence,
            now=SENTIMENT_NOW,
        )
        by_date = {d["date"]: d for d in docs}
        assert by_date["2026-05-20"]["expertSentiment"] == pytest.approx(0.0)
        assert by_date["2026-05-20"]["publicSentiment"] == pytest.approx(0.0)
        assert by_date["2026-05-21"]["expertSentiment"] == pytest.approx(0.5)
        assert by_date["2026-05-21"]["publicSentiment"] == pytest.approx(0.5)
        assert by_date["2026-05-22"]["expertSentiment"] == pytest.approx(1.0)
        assert by_date["2026-05-22"]["publicSentiment"] == pytest.approx(1.0)

    # 6
    def test_write_order_sentimenttimeseries_after_prediction_before_status(
        self, mocked_firestore,
    ):
        """
        T22.6 regression: sentimentTimeSeries write fires AFTER
        evidence + predictionSeries and BEFORE sessionResults/status=done.
        Protects the frontend "render now" contract per the
        write-order pin from T22.4.
        """
        manager, *_ = mocked_firestore
        # Use _state's default (no market_evidence, no researcher/pulse) so
        # sentimentTimeSeries is empty — we're testing call ORDER, not content.
        write_to_firestore.run(_state(evidence_trail=[_evidence_item()]))
        method_names = [c[0] for c in manager.method_calls]
        assert method_names == [
            "evidence",
            "prediction_series",
            "sentiment_time_series",
            "session_result",
            "session_status",
            "query_status",
        ]

    # 7
    def test_doc_shape_pin_fields_present(self):
        """
        Every emitted doc carries exactly six fields (ts, date,
        expertSentiment, publicSentiment, expertUpper, expertLower).
        Confidence bands deferred to Future Enhancement 5 — explicit
        `None` honored.
        """
        researcher_evidence = {
            "articles": [
                _researcher_article(
                    published_at="2026-05-21T10:00:00+00:00",
                    sentiment_score=0.2,
                ),
            ],
        }
        docs = write_to_firestore._shape_sentiment_time_series(
            researcher_evidence=researcher_evidence,
            pulse_evidence={"community_discussion": []},
            now=SENTIMENT_NOW,
        )
        assert len(docs) == 1
        doc = docs[0]
        expected_keys = {
            "ts", "date",
            "expertSentiment", "publicSentiment",
            "expertUpper", "expertLower",
        }
        assert set(doc.keys()) == expected_keys
        # ts is tz-aware UTC
        assert isinstance(doc["ts"], datetime)
        assert doc["ts"].tzinfo is not None
        # date is YYYY-MM-DD
        assert doc["date"] == "2026-05-21"
        # Confidence bands stay None in V1
        assert doc["expertUpper"] is None
        assert doc["expertLower"] is None

    # 8
    def test_bucket_order_is_ascending_by_ts(self):
        """
        Output is ts-ascending so the server's `.orderBy('ts', 'asc')`
        query returns docs in chronological order without re-sort.
        """
        researcher_evidence = {
            "articles": [
                _researcher_article(
                    published_at="2026-05-22T10:00:00+00:00",   # later
                    sentiment_score=0.3,
                ),
                _researcher_article(
                    published_at="2026-05-19T10:00:00+00:00",   # earliest
                    sentiment_score=0.2,
                ),
                _researcher_article(
                    published_at="2026-05-21T10:00:00+00:00",   # middle
                    sentiment_score=0.1,
                ),
            ],
        }
        docs = write_to_firestore._shape_sentiment_time_series(
            researcher_evidence=researcher_evidence,
            pulse_evidence={"community_discussion": []},
            now=SENTIMENT_NOW,
        )
        timestamps = [d["ts"] for d in docs]
        assert timestamps == sorted(timestamps)
        assert [d["date"] for d in docs] == [
            "2026-05-19", "2026-05-21", "2026-05-22",
        ]

    # 9 (NEW per Ron's a1 update — Expert-only-emits-doc on a specific date)
    def test_bucket_with_only_expert_data_emits_doc_with_public_none(self):
        """
        Per D6 (a1): a bucket where Expert has data and Public has none
        emits a doc with expertSentiment populated and publicSentiment
        explicitly None. This is the per-bucket behavior that
        differentiates (a1) from the rejected (a3) strict-both-sources
        gate.

        Note that the server's mapSentimentDoc applies `?? 0` on read —
        the FE will see publicSentiment as 0.0. That's the documented
        D6 limitation. The agent stores `None` to preserve the truth
        in Firestore for any future analytics (or FE-side null
        discrimination upgrade).
        """
        # Expert has data on 2026-05-21 only.
        researcher_evidence = {
            "articles": [
                _researcher_article(
                    published_at="2026-05-21T10:00:00+00:00",
                    sentiment_score=0.6,
                ),
            ],
        }
        # Public has data on a DIFFERENT day (2026-05-22) — so 2026-05-21
        # is "Expert-only", and 2026-05-22 is "Public-only".
        pulse_evidence = {
            "community_discussion": [
                _hackernews_discussion(
                    published_at="2026-05-22T10:00:00+00:00",
                    community_sentiment=-0.4,
                ),
            ],
        }
        docs = write_to_firestore._shape_sentiment_time_series(
            researcher_evidence=researcher_evidence,
            pulse_evidence=pulse_evidence,
            now=SENTIMENT_NOW,
        )
        assert len(docs) == 2

        by_date = {d["date"]: d for d in docs}
        expert_only_doc = by_date["2026-05-21"]
        assert expert_only_doc["expertSentiment"] == pytest.approx(0.8)
        assert expert_only_doc["publicSentiment"] is None

        public_only_doc = by_date["2026-05-22"]
        assert public_only_doc["expertSentiment"] is None
        assert public_only_doc["publicSentiment"] == pytest.approx(0.3)


# ==========================================================
# Sprint 22 T22.7 — canonicalKey on session doc
# ==========================================================

def _market_evidence_with_market_slug(slug: str) -> dict:
    """
    Minimal market_evidence shape carrying a Polymarket payload —
    mirrors what T22.2's `_pack_polymarket_payload` produces.
    """
    return {
        "polymarket": {
            "current_odds": 0.62,
            "momentum": {"change_24h": 0.0, "change_7d": 0.0, "change_30d": 0.0},
            "price_history": [],
            "whale_alerts": [],
            "market_slug": slug,
        }
    }


class TestCanonicalKeyOnSessionDoc:
    """
    Sprint 22 T22.7: write_to_firestore must pass `canonical_key` to
    `update_session_status` on the success transition. Tier 1 → string;
    Tier 2 → explicit None (clears any prior Express-written candidate
    UUID). UNSET sentinel semantics are tested at the firestore_client
    level — these tests verify only the node-level wiring.
    """

    def test_tier_1_passes_market_slug_as_canonical_key(self, mocked_firestore):
        """
        State carries a resolved polymarket payload with `market_slug` →
        `update_session_status` receives that exact string as
        canonical_key.
        """
        _, _, _, _, _, ss, _ = mocked_firestore
        write_to_firestore.run(_state(
            evidence_trail=[_evidence_item()],
            market_evidence=_market_evidence_with_market_slug(
                "0xfed_condition_id",
            ),
        ))
        ss.assert_called_once_with(
            "s1", "done",
            tier=None,
            canonical_key="0xfed_condition_id",
        )

    def test_tier_2_passes_explicit_none_as_canonical_key(self, mocked_firestore):
        """
        Tier 2 (no market_evidence in state) → `canonical_key=None`
        explicitly. This is the *write Firestore null* path (not the
        UNSET-omit path); the helper must include `canonicalKey: null`
        in the update payload, clearing any prior candidate UUID.
        """
        _, _, _, _, _, ss, _ = mocked_firestore
        write_to_firestore.run(_state(evidence_trail=[_evidence_item()]))
        ss.assert_called_once_with(
            "s1", "done",
            tier=None,
            canonical_key=None,
        )

    def test_clarification_reentry_overwrites_candidate_uuid_with_market_slug(
        self, mocked_firestore,
    ):
        """
        Clarification-resume scenario (Ron's pin): Express previously
        wrote `canonicalKey = "candidate-uuid-123"` on the session doc
        when requeueing after the user picked a clarification candidate
        (server.session.repository.ts:466). After the agent re-runs and
        Tier 1 resolves a real market, the final write must
        OVERWRITE the candidate UUID with the resolved market_slug —
        because the future cross-user cache (FE3) queries against the
        canonical market identifier, not session-specific candidate IDs.

        This test verifies the agent passes the RESOLVED market_slug,
        not whatever Express wrote earlier. The mocked firestore_client
        doesn't track the prior Firestore state — what matters is the
        call payload from this node.
        """
        _, _, _, _, _, ss, _ = mocked_firestore
        # Resume-on-clarify state hint — present in real sessions to
        # signal the agent went through resume path. Not asserted; the
        # final write is what matters.
        write_to_firestore.run(_state(
            evidence_trail=[_evidence_item()],
            chosen_candidate_id="candidate-uuid-123",
            market_evidence=_market_evidence_with_market_slug(
                "0xreal_market_after_resume",
            ),
        ))
        ss.assert_called_once_with(
            "s1", "done",
            tier=None,
            canonical_key="0xreal_market_after_resume",
        )
