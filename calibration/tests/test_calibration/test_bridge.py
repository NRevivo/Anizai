"""
Gate 1/2 — the Firestore bridge, without Firestore.

Payload construction and the harvest state machine are pure functions, so the
parts of Phase 10B that carry judgement are testable with no emulator. The
round-trip against a real emulator lives in `test_emulator.py`.

The heaviest coverage is on the harvest state mapping, because it is the piece
that decides what a run *meant*, and every branch of it produces a number that
someone will later read as fact.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from calibration import evidence_projection, firestore_client
from calibration.services.harvest_service import classify_session

NOW = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)


# ==========================================================
# Session id minting
# ==========================================================

def test_session_ids_are_prefixed_and_unique():
    """
    The prefix makes a calibration session recognisable in the Firestore
    console without opening the document.
    """
    ids = {firestore_client.new_session_id() for _ in range(200)}
    assert len(ids) == 200
    assert all(i.startswith("cal_") for i in ids)


# ==========================================================
# The calibration marker
# ==========================================================

def test_marker_is_namespaced_under_metadata():
    """
    Nested rather than spread across top-level fields (plan A4), so a future
    strict-validation pass has one key to allow and cleanup queries have one
    stable path to filter on.
    """
    marker = firestore_client.calibration_metadata("q-1", "r-1", 2)
    assert marker == {
        "calibration": {
            "enabled": True,
            "questionId": "q-1",
            "runId": "r-1",
            "forecastRunIndex": 2,
        }
    }


# ==========================================================
# Dispatch payloads
# ==========================================================

@pytest.fixture
def payloads():
    session = firestore_client.build_session_payload(
        question_text="Will X happen?",
        idempotency_key="key-1",
        question_id="q-1",
        run_id="r-1",
        forecast_run_index=0,
    )
    query = firestore_client.build_query_payload(
        session_id="cal_abc",
        question_text="Will X happen?",
        question_id="q-1",
        run_id="r-1",
        forecast_run_index=0,
    )
    return session, query


def test_both_documents_carry_the_sentinel_owner(payloads):
    """
    Not a Firebase Auth UID, deliberately — nothing user-facing can enumerate,
    bill, or render a session owned by a user that does not exist (plan A7).
    """
    session, query = payloads
    assert session["userId"] == "calibration-runner"
    assert query["userId"] == "calibration-runner"


def test_both_documents_carry_the_calibration_marker(payloads):
    session, query = payloads
    assert session["metadata"]["calibration"]["enabled"] is True
    assert query["metadata"]["calibration"]["enabled"] is True
    assert session["metadata"] == query["metadata"]


def test_session_starts_queued_which_is_what_the_worker_transitions_from(payloads):
    session, _ = payloads
    assert session["status"] == "queued"


def test_query_starts_pending_and_unclaimed(payloads):
    _, query = payloads
    assert query["status"] == "pending"
    assert query["claimedAt"] is None
    assert query["claimedBy"] is None


def test_follow_up_is_disabled_on_calibration_sessions(payloads):
    """
    Keeps the session out of the Sprint 24 follow-up subgraph, which would
    otherwise spend tokens on a conversation nobody is having.
    """
    session, _ = payloads
    assert session["followEnabled"] is False
    assert session["isFollowing"] is False


def test_session_carries_every_field_the_bff_writes(payloads):
    """The worker must see nothing unusual about a calibration session."""
    session, _ = payloads
    for field in (
        "userId", "question", "title", "idempotencyKey", "status",
        "latestProbability", "latestConfidence", "followEnabled", "isFollowing",
        "canonicalKey", "errorCode", "errorMessage", "clarificationCandidates",
        "createdAt", "updatedAt", "lastActivityAt", "metadata",
    ):
        assert field in session, f"session payload is missing {field}"


def test_query_references_the_session_it_belongs_to(payloads):
    _, query = payloads
    assert query["sessionId"] == "cal_abc"
    assert query["queryId"] == "q_cal_abc"


def test_question_text_is_passed_through_verbatim():
    """
    The agent is asked exactly what the market asks. Any rewording here
    changes what is being measured.
    """
    text = "Will the Strait of Hormuz reopen by July 31?"
    session = firestore_client.build_session_payload(text, "k", "q", "r", 0)
    query = firestore_client.build_query_payload("cal_x", text, "q", "r", 0)
    assert session["question"] == text
    assert query["question"] == text


# ==========================================================
# Harvest state machine — every branch
# ==========================================================

def _classify(status, dispatched_minutes_ago=5, session_present=True, **extra):
    session = None if not session_present else {"status": status, **extra}
    dispatched_at = NOW - timedelta(minutes=dispatched_minutes_ago)
    return classify_session(session, dispatched_at, NOW, timeout_minutes=120)


@pytest.mark.parametrize("status", ["done", "complete", "completed"])
def test_done_maps_to_completed(status):
    assert _classify(status) == ("completed", None)


@pytest.mark.parametrize("status", ["failed", "error"])
def test_failure_maps_to_failed_with_a_message(status):
    result, message = _classify(status, errorMessage="vault timeout")
    assert result == "failed"
    assert message == "vault timeout"


def test_failure_without_a_message_still_records_one():
    result, message = _classify("failed")
    assert result == "failed"
    assert message


def test_error_code_is_used_when_no_message_is_present():
    _, message = _classify("failed", errorCode="E_VAULT")
    assert message == "E_VAULT"


@pytest.mark.parametrize("status", ["awaiting_clarification", "needs_clarification"])
def test_clarification_is_its_own_terminal_state_not_a_failure(status):
    """
    The branch the pre-revision plan had no slot for. Recording it as `failed`
    would inflate the failure rate with non-failures; leaving it `dispatched`
    would let it age into a 120-minute lie.
    """
    result, message = _classify(status)
    assert result == "needs_clarification"
    assert message is None


def test_a_running_session_inside_the_window_keeps_waiting():
    assert _classify("running", dispatched_minutes_ago=5)[0] == "dispatched"
    assert _classify("claimed", dispatched_minutes_ago=5)[0] == "dispatched"


def test_a_running_session_past_the_window_times_out():
    result, message = _classify("running", dispatched_minutes_ago=200)
    assert result == "timed_out"
    assert "no terminal state" in message


def test_a_missing_session_document_waits_before_timing_out():
    """
    A missing document is indistinguishable from a slow one. Guessing costs a
    forecast; waiting costs a polling cycle.
    """
    assert _classify("", session_present=False, dispatched_minutes_ago=5)[0] == "dispatched"

    result, message = _classify("", session_present=False, dispatched_minutes_ago=200)
    assert result == "timed_out"
    assert "never appeared" in message


def test_an_unknown_status_waits_rather_than_guessing():
    """
    An agent version that introduces a new status must not cause silent
    mislabelling. Fall through to wait, then time out.
    """
    assert _classify("some_future_state", dispatched_minutes_ago=5)[0] == "dispatched"


def test_status_matching_is_case_and_whitespace_insensitive():
    assert _classify("  DONE  ")[0] == "completed"


def test_a_forecast_with_no_dispatch_timestamp_never_times_out():
    """Without a dispatch time there is no age to compare — waiting is correct."""
    assert classify_session({"status": "running"}, None, NOW, 120)[0] == "dispatched"


# ==========================================================
# Evidence projection
# ==========================================================

def test_projection_counts_evidence_by_source_type():
    projection = evidence_projection.project_evidence(
        {"keyFactors": [{"title": "A"}, {"title": "B"}]},
        [
            {"sourceType": "news"},
            {"sourceType": "news"},
            {"sourceType": "social"},
            {"sourceType": "market"},
        ],
    )
    assert projection["evidence_count_total"] == 4
    assert projection["evidence_count_by_source_type"]["news"] == 2
    assert set(projection["vault_types_present"]) == {"knowledge", "social", "momentum"}


# Captured verbatim from the first live forecast, 2026-07-29
# (session cal_c17e5647cee1, agent 0.5.0-sprint26+55e8093). Every other
# fixture in this file uses source names invented from the contract, and the
# first version of the projection matched none of the real ones — producing a
# payload that counted 15 evidence documents and credited zero vaults at the
# same time. These are the strings the agent actually sends.
LIVE_EVIDENCE_2026_07_29 = (
    [{"sourceType": "vault_news"}] * 5 + [{"sourceType": "vault_hackernews"}] * 10
)


def test_projection_maps_the_source_names_the_live_agent_actually_sends():
    """
    The regression test for the defect the first live run exposed.

    The failure was not a crash. It was a well-formed, internally
    contradictory payload — the class of bug this whole system exists to
    catch — so it must not be possible to reintroduce it silently.
    """
    projection = evidence_projection.project_evidence(
        {"keyFactors": [{"title": "Reduced Shipping Activity"}]},
        LIVE_EVIDENCE_2026_07_29,
    )

    assert projection["evidence_count_total"] == 15
    assert projection["evidence_count_by_source_type"] == {
        "vault_news": 5,
        "vault_hackernews": 10,
    }
    # Both feed the Researcher — news and HackerNews are long-form text.
    assert projection["vault_types_present"] == ["knowledge"]
    assert projection["unmapped_source_types"] == []


@pytest.mark.parametrize(
    "source",
    [
        "vault_news", "vault_hackernews", "vault_arxiv", "vault_social",
        "vault_telegram", "vault_polymarket", "vault_momentum",
        "news", "hackernews", "arxiv", "market", "reactive",
    ],
)
def test_counted_evidence_always_credits_a_vault(source):
    """
    The invariant the defect violated: if evidence was counted, some vault
    contributed it.
    """
    projection = evidence_projection.project_evidence({}, [{"sourceType": source}])
    assert projection["evidence_count_total"] == 1
    assert projection["vault_types_present"], (
        f"{source!r} counted as evidence but credited no vault — the "
        "2026-07-29 defect"
    )


def test_an_unmapped_source_is_flagged_rather_than_silently_dropped(caplog):
    """
    A source name nobody anticipated must announce itself — in the log AND in
    the payload. Absorbing it silently is how the original defect survived.
    """
    with caplog.at_level("WARNING"):
        projection = evidence_projection.project_evidence(
            {}, [{"sourceType": "vault_something_new"}]
        )

    assert projection["evidence_count_total"] == 1
    assert projection["unmapped_source_types"] == ["vault_something_new"]
    assert any("does not map" in r.message for r in caplog.records)


def test_prefix_stripping_survives_a_naming_change():
    """
    The agent's naming convention already changed once without notice. A new
    prefix must cost one entry, not a duplicate of the whole table.
    """
    for name in ("news", "vault_news", "source_news", "evidence_news"):
        projection = evidence_projection.project_evidence({}, [{"sourceType": name}])
        assert projection["vault_types_present"] == ["knowledge"], name


def test_projection_reports_absent_vaults_explicitly():
    """
    Phase 10C's source-contribution metric compares present against absent and
    needs both sides.
    """
    projection = evidence_projection.project_evidence({}, [{"sourceType": "news"}])
    assert "knowledge" in projection["vault_types_present"]
    assert "social" in projection["vault_types_absent"]


def test_projection_carries_no_raw_text():
    """
    Counts and metadata only. A projection built from text breaks silently the
    moment the agent rewords anything — and source-contribution compares these
    across months of agent versions.
    """
    projection = evidence_projection.project_evidence(
        {}, [{"sourceType": "news", "content": "a very long article body"}]
    )
    assert "a very long article body" not in str(projection)


def test_projection_survives_missing_evidence():
    """A thin evidence trail must not lose an otherwise-good forecast."""
    projection = evidence_projection.project_evidence(None, None)
    assert projection["evidence_count_total"] == 0
    assert projection["vault_types_present"] == []
    assert projection["projection_version"] == evidence_projection.PROJECTION_VERSION


def test_evidence_with_no_source_field_is_counted_as_unknown():
    """
    A total that does not equal the sum of its parts is worse than an ugly
    bucket name.
    """
    projection = evidence_projection.project_evidence({}, [{}, {"irrelevant": 1}])
    assert projection["evidence_count_total"] == 2
    assert projection["evidence_count_by_source_type"]["unknown"] == 2


def test_reactive_search_usage_is_flagged():
    projection = evidence_projection.project_evidence({}, [{"sourceType": "reactive"}])
    assert projection["reactive_search_used"] is True
    assert projection["reactive_search_count"] == 1
    assert "reactive_search" in projection["vault_types_present"]


def test_key_factor_titles_are_capped_at_three():
    projection = evidence_projection.project_evidence(
        {"keyFactors": [{"title": f"F{i}"} for i in range(10)]}, []
    )
    assert len(projection["top_3_key_factor_titles"]) == 3


def test_key_factors_accept_strings_or_objects():
    assert evidence_projection.project_evidence(
        {"keyFactors": ["plain string"]}, []
    )["top_3_key_factor_titles"] == ["plain string"]


# ==========================================================
# SessionResult field extraction
# ==========================================================

@pytest.mark.parametrize("key", ["finalProbability", "probability", "final_probability"])
def test_probability_is_read_from_any_known_field(key):
    assert evidence_projection.extract_probability({key: 0.67}) == 0.67


def test_a_percentage_shaped_probability_is_converted_and_logged(caplog):
    """
    Converting rather than rejecting: a NULL probability is excluded from
    Brier entirely, so a unit mix-up would quietly shrink the sample instead
    of announcing itself.
    """
    with caplog.at_level("WARNING"):
        assert evidence_projection.extract_probability({"probability": 67}) == 0.67
    assert any("percentage" in r.message for r in caplog.records)


def test_a_wildly_out_of_range_probability_is_discarded(caplog):
    """Above 100 is not a unit problem, it is corruption."""
    with caplog.at_level("ERROR"):
        assert evidence_projection.extract_probability({"probability": 5000}) is None


def test_probability_boundaries_pass_through_untouched():
    assert evidence_projection.extract_probability({"probability": 0}) == 0.0
    assert evidence_projection.extract_probability({"probability": 1}) == 1.0


def test_missing_probability_is_none():
    assert evidence_projection.extract_probability({}) is None
    assert evidence_projection.extract_probability(None) is None


@pytest.mark.parametrize(
    "raw,expected",
    [("tier_1", "tier_1"), ("tier1", "tier_1"), ("2", "tier_2"), ("Tier 2", "tier_2")],
)
def test_tier_is_normalised_to_the_schema_values(raw, expected):
    assert evidence_projection.extract_tier({"tier": raw}) == expected


def test_an_unrecognised_tier_becomes_null_rather_than_failing_the_insert(caplog):
    with caplog.at_level("WARNING"):
        assert evidence_projection.extract_tier({"tier": "platinum"}) is None


def test_agent_version_is_recorded_verbatim():
    """Carries a git sha since Sprint 26 — never parsed, never compared."""
    version = "0.5.0-sprint26+55e8093"
    assert evidence_projection.extract_agent_version({"agentVersion": version}) == version
