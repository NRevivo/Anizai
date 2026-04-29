"""
Gate 1 unit tests for agent.firestore_client (Sprint 18 T10).

Coverage:
    - init_app() — auth-mode validation and idempotency
    - claim_query() inner transaction — pending/non-pending/missing branches
    - update_session_status() — field-conditional write logic

Mocking strategy:
    - init_app: patch settings + firebase_admin.initialize_app + credentials.
    - claim_query: bypass the @firestore.transactional decorator by calling
      the wrapped function directly (decorator's retry logic is Google's
      code, exercised against the real emulator at Gate 3 / T12).
    - update_session_status: patch get_db and assert the dict passed to
      session_ref.update().
"""

from unittest.mock import MagicMock, patch

import firebase_admin
import pytest

from agent import firestore_client
from agent.firestore_client import _claim_query_txn, init_app

# All tests in this file need a clean firebase_admin._apps. The conftest
# fixture is no longer autouse (T12 emulator tests need to keep their
# session-scoped init), so request it at file scope.
pytestmark = pytest.mark.usefixtures("reset_firebase_state")


# ==========================================================
# Locate the undecorated inner function for claim_query tests.
# google-cloud-firestore wraps with @firestore.transactional which
# stores the original on `.to_wrap` (v2.x). functools-style decorators
# would expose `__wrapped__` instead. Try both so the tests keep working
# if Google switches conventions.
# ==========================================================

_INNER_TXN = (
    getattr(_claim_query_txn, "__wrapped__", None)
    or getattr(_claim_query_txn, "to_wrap", None)
)


# ==========================================================
# init_app — auth-mode validation and idempotency
# ==========================================================

def test_init_app_raises_when_project_id_empty():
    """FIREBASE_PROJECT_ID is required — empty string must fail loudly."""
    with patch.object(firestore_client.settings, "FIREBASE_PROJECT_ID", ""):
        with pytest.raises(ValueError, match="FIREBASE_PROJECT_ID"):
            init_app()


def test_init_app_raises_when_credentials_path_missing(tmp_path):
    """If GOOGLE_APPLICATION_CREDENTIALS points at a non-existent file,
    fail with FileNotFoundError before Firebase even gets called."""
    bogus = str(tmp_path / "nonexistent.json")
    with (
        patch.object(firestore_client.settings, "FIREBASE_PROJECT_ID", "p"),
        patch.object(
            firestore_client.settings,
            "GOOGLE_APPLICATION_CREDENTIALS",
            bogus,
        ),
    ):
        with pytest.raises(FileNotFoundError, match="GOOGLE_APPLICATION_CREDENTIALS"):
            init_app()


def test_init_app_raises_when_service_account_json_malformed(tmp_path):
    """A file that exists but isn't valid service-account JSON must surface
    as a wrapped ValueError naming the malformed-or-missing-fields cause."""
    bad_path = tmp_path / "bad.json"
    bad_path.write_text("{ this is not valid json")
    with (
        patch.object(firestore_client.settings, "FIREBASE_PROJECT_ID", "p"),
        patch.object(
            firestore_client.settings,
            "GOOGLE_APPLICATION_CREDENTIALS",
            str(bad_path),
        ),
    ):
        with pytest.raises(ValueError, match="malformed"):
            init_app()


def test_init_app_is_idempotent():
    """Two consecutive init_app() calls must result in exactly one
    firebase_admin.initialize_app() invocation. The conftest fixture
    cleared _apps; the side_effect simulates initialize_app's real
    behavior of populating _apps so the second call sees it non-empty."""

    def fake_initialize(_cred, _opts):
        firebase_admin._apps["[DEFAULT]"] = MagicMock()

    with (
        patch.object(firestore_client.settings, "FIREBASE_PROJECT_ID", "p"),
        patch.object(firestore_client.settings, "GOOGLE_APPLICATION_CREDENTIALS", ""),
        patch.object(firestore_client.credentials, "ApplicationDefault"),
        patch.object(
            firestore_client.firebase_admin,
            "initialize_app",
            side_effect=fake_initialize,
        ) as mock_init,
    ):
        init_app()
        assert mock_init.call_count == 1
        init_app()
        assert mock_init.call_count == 1


# ==========================================================
# claim_query — atomic-claim transactional logic
# ==========================================================

def _require_inner_txn():
    if _INNER_TXN is None:
        pytest.fail(
            "Could not access wrapped function on _claim_query_txn — "
            "neither __wrapped__ nor to_wrap is set. The "
            "@firestore.transactional decorator may have changed "
            "implementation. Refactor _claim_query_txn so the inner logic "
            "is a separate undecorated helper."
        )


def test_claim_query_succeeds_when_status_pending():
    """Happy path: snapshot is pending → flip to claimed with worker_id +
    SERVER_TIMESTAMP, return the original payload as captured pre-update."""
    _require_inner_txn()

    payload = {
        "queryId": "q1",
        "sessionId": "s1",
        "userId": "u1",
        "question": "will it rain?",
        "status": "pending",
        "createdAt": MagicMock(),
        "claimedAt": None,
        "claimedBy": None,
    }
    snapshot = MagicMock()
    snapshot.exists = True
    snapshot.to_dict.return_value = payload

    query_ref = MagicMock()
    query_ref.get.return_value = snapshot
    transaction = MagicMock()

    result = _INNER_TXN(transaction, query_ref, "worker-test")

    assert result == payload
    transaction.update.assert_called_once_with(
        query_ref,
        {
            "status": "claimed",
            "claimedAt": firestore_client.firestore.SERVER_TIMESTAMP,
            "claimedBy": "worker-test",
        },
    )
    # Sentinel identity check — the SERVER_TIMESTAMP is the literal Firestore
    # sentinel object, not a local datetime stamped client-side. This catches
    # accidental swaps to datetime.utcnow() during refactors.
    sent_dict = transaction.update.call_args[0][1]
    assert sent_dict["claimedAt"] is firestore_client.firestore.SERVER_TIMESTAMP


def test_claim_query_returns_none_when_doc_missing():
    """Snapshot.exists is False → return None, do not attempt update."""
    _require_inner_txn()

    snapshot = MagicMock()
    snapshot.exists = False
    query_ref = MagicMock()
    query_ref.get.return_value = snapshot
    transaction = MagicMock()

    result = _INNER_TXN(transaction, query_ref, "worker-test")

    assert result is None
    transaction.update.assert_not_called()


@pytest.mark.parametrize("status", ["claimed", "done", "failed"])
def test_claim_query_returns_none_for_non_pending_status(status):
    """Race-loss path: another worker (or a prior run) flipped status away
    from 'pending'. Must return None and never call transaction.update."""
    _require_inner_txn()

    snapshot = MagicMock()
    snapshot.exists = True
    snapshot.to_dict.return_value = {"status": status}
    query_ref = MagicMock()
    query_ref.get.return_value = snapshot
    transaction = MagicMock()

    result = _INNER_TXN(transaction, query_ref, "worker-test")

    assert result is None
    transaction.update.assert_not_called()


# ==========================================================
# update_session_status — field-conditional write logic
# ==========================================================

def _make_db_mock():
    """Build a get_db mock that returns a chained collection().document()
    chain ending in a MagicMock for the session_ref. Returns (mock_get_db,
    mock_session_ref) so tests can inspect the .update() call."""
    mock_db = MagicMock()
    mock_session_ref = MagicMock()
    mock_db.collection.return_value.document.return_value = mock_session_ref
    return mock_db, mock_session_ref


def test_update_session_status_default_writes_only_status_and_timestamps():
    """Default call (no error/clarification args) must NOT add errorCode,
    errorMessage, or clarificationCandidates keys — those would clobber
    whatever the doc already has when the next .update() runs."""
    mock_db, mock_session_ref = _make_db_mock()
    with patch.object(firestore_client, "get_db", return_value=mock_db):
        firestore_client.update_session_status("s1", "running")

    mock_session_ref.update.assert_called_once()
    sent = mock_session_ref.update.call_args[0][0]
    assert sent["status"] == "running"
    assert "updatedAt" in sent
    assert "lastActivityAt" in sent
    assert "errorCode" not in sent
    assert "errorMessage" not in sent
    assert "clarificationCandidates" not in sent


def test_update_session_status_writes_error_fields_when_provided():
    """error_code + error_message must land as errorCode + errorMessage
    (camelCase, matches the server-side schema)."""
    mock_db, mock_session_ref = _make_db_mock()
    with patch.object(firestore_client, "get_db", return_value=mock_db):
        firestore_client.update_session_status(
            "s1", "failed",
            error_code="hub_error",
            error_message="boom",
        )

    sent = mock_session_ref.update.call_args[0][0]
    assert sent["status"] == "failed"
    assert sent["errorCode"] == "hub_error"
    assert sent["errorMessage"] == "boom"


def test_update_session_status_writes_clarification_candidates_when_provided():
    """clarification_candidates must land as clarificationCandidates
    (Sprint 21 forward-flag — frontend reads this on awaiting_clarification)."""
    candidates = [{"id": "a", "text": "option A"}]
    mock_db, mock_session_ref = _make_db_mock()
    with patch.object(firestore_client, "get_db", return_value=mock_db):
        firestore_client.update_session_status(
            "s1", "awaiting_clarification",
            clarification_candidates=candidates,
        )

    sent = mock_session_ref.update.call_args[0][0]
    assert sent["status"] == "awaiting_clarification"
    assert sent["clarificationCandidates"] == candidates
