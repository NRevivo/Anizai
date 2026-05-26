"""
Persistence Gate (Gate 1 + Gate 3): reactive_triggers_log (Sprint 23 T23.4).

Verifies that the two public functions in persistence/reactive_triggers_log.py
correctly validate inputs (Gate 1, client-side, no DB roundtrip needed for the
ValueError tests) and interact correctly with a live PostgreSQL
`reactive_triggers_log` table (Gate 3).

Requires: PostgreSQL container running with tables initialised, including
the new section 7 (reactive_triggers_log) added in T23.3.
    docker compose -f infrastructure/docker-compose.yml up -d postgres

Test index:

insert() — validation [1–6]:
    [1]  insert() raises ValueError for empty session_id
    [2]  insert() raises ValueError for empty source
    [3]  insert() raises ValueError when keywords is not a list (str, dict, None)
    [4]  insert() raises ValueError for status not in {'emitted','failed'}
    [5]  insert() accepts an empty keywords list (failed-emit audit case)
    [6]  insert() accepts kafka_offset=None (mocked-producer audit case)

insert() — live DB [7–12]:
    [7]  insert() returns a UUID string and persists the row
    [8]  insert() persists keywords as a JSONB list (round-trip equality)
    [9]  insert() persists kafka_offset when supplied
    [10] insert() persists status='emitted' verbatim
    [11] insert() persists status='failed' verbatim
    [12] insert() generates distinct trigger_ids across two calls

list_by_session() — validation [13]:
    [13] list_by_session() raises ValueError for empty session_id

list_by_session() — live DB [14–17]:
    [14] list_by_session() returns [] for an unknown session_id
    [15] list_by_session() returns rows ordered by trigger_time DESC
    [16] list_by_session() returns only rows matching the requested session
    [17] list_by_session() returns row dicts with all expected fields

References:
    - data-pipeline/docs/agentic_hub_implementation_phase8_revised.md §Sprint 23 T23.4
    - data-pipeline/infrastructure/sql/init.sql §7 (reactive_triggers_log table)
    - data-pipeline/persistence/reactive_triggers_log.py
    - Section 9.3: Triple-Gate Test Matrix
"""

import time

import pytest

from persistence.reactive_triggers_log import insert, list_by_session

pytestmark = pytest.mark.usefixtures("db_available")


# ==========================================================
# Helpers
# ==========================================================

def _make_session_id(test_run_id: str, label: str) -> str:
    """Build a test-prefixed session_id so cleanup_reactive_triggers_log catches it."""
    return f"test_{test_run_id}_{label}"


# ==========================================================
# insert() — validation [1–6]
# ==========================================================

class TestInsertValidation:

    def test_raises_on_empty_session_id(self):
        # [1]
        with pytest.raises(ValueError, match="session_id"):
            insert(
                session_id="",
                source="newsapi",
                keywords=["iran"],
                kafka_offset=None,
                status="emitted",
            )

    def test_raises_on_empty_source(self, test_run_id):
        # [2]
        with pytest.raises(ValueError, match="source"):
            insert(
                session_id=_make_session_id(test_run_id, "v_empty_source"),
                source="",
                keywords=["iran"],
                kafka_offset=None,
                status="emitted",
            )

    @pytest.mark.parametrize("bad_keywords", ["iran", {"k": "v"}, None])
    def test_raises_on_non_list_keywords(self, test_run_id, bad_keywords):
        # [3]
        with pytest.raises(ValueError, match="keywords"):
            insert(
                session_id=_make_session_id(test_run_id, "v_bad_kw"),
                source="newsapi",
                keywords=bad_keywords,  # type: ignore[arg-type]
                kafka_offset=None,
                status="emitted",
            )

    def test_raises_on_invalid_status(self, test_run_id):
        # [4]
        with pytest.raises(ValueError, match="status"):
            insert(
                session_id=_make_session_id(test_run_id, "v_bad_status"),
                source="newsapi",
                keywords=["iran"],
                kafka_offset=None,
                status="pending",  # not in {'emitted','failed'}
            )

    def test_accepts_empty_keywords_list(
        self, test_run_id, cleanup_reactive_triggers_log
    ):
        # [5] Failed-emit audit may want to record an empty keyword list;
        # client-side validation enforces type=list but not non-emptiness.
        # The consumer's VALID_SOURCES / _REQUIRED_LIST_FIELDS is the gate
        # for live triggers — see test_trigger_consumer.py.
        trigger_id = insert(
            session_id=_make_session_id(test_run_id, "v_empty_kw"),
            source="newsapi",
            keywords=[],
            kafka_offset=None,
            status="failed",
        )
        assert isinstance(trigger_id, str)

    def test_accepts_null_kafka_offset(
        self, test_run_id, cleanup_reactive_triggers_log
    ):
        # [6] Mocked producers in Gate 1 / Gate 2 may not surface an offset.
        trigger_id = insert(
            session_id=_make_session_id(test_run_id, "v_null_offset"),
            source="newsapi",
            keywords=["iran"],
            kafka_offset=None,
            status="emitted",
        )
        assert isinstance(trigger_id, str)


# ==========================================================
# insert() — live DB [7–12]
# ==========================================================

class TestInsertLive:

    def test_returns_uuid_string_and_persists_row(
        self, test_run_id, cleanup_reactive_triggers_log
    ):
        # [7]
        session_id = _make_session_id(test_run_id, "ins_basic")
        trigger_id = insert(
            session_id=session_id,
            source="newsapi",
            keywords=["iran", "opec"],
            kafka_offset=12345,
            status="emitted",
        )
        assert isinstance(trigger_id, str)
        assert len(trigger_id) == 36  # UUID string length

        # Confirm persisted via list_by_session
        rows = list_by_session(session_id)
        assert len(rows) == 1
        assert rows[0]["trigger_id"] == trigger_id

    def test_persists_keywords_as_jsonb_list(
        self, test_run_id, cleanup_reactive_triggers_log
    ):
        # [8] Round-trip equality: list[str] → JSONB → list[str].
        session_id = _make_session_id(test_run_id, "ins_kw_jsonb")
        keywords_in = ["iran", "opec", "crude oil"]
        insert(session_id, "newsapi", keywords_in, None, "emitted")
        rows = list_by_session(session_id)
        assert rows[0]["keywords"] == keywords_in

    def test_persists_kafka_offset_when_supplied(
        self, test_run_id, cleanup_reactive_triggers_log
    ):
        # [9]
        session_id = _make_session_id(test_run_id, "ins_offset")
        insert(session_id, "newsapi", ["iran"], 98765, "emitted")
        rows = list_by_session(session_id)
        assert rows[0]["kafka_offset"] == 98765

    def test_persists_status_emitted(
        self, test_run_id, cleanup_reactive_triggers_log
    ):
        # [10]
        session_id = _make_session_id(test_run_id, "ins_emitted")
        insert(session_id, "newsapi", ["iran"], 1, "emitted")
        rows = list_by_session(session_id)
        assert rows[0]["status"] == "emitted"

    def test_persists_status_failed(
        self, test_run_id, cleanup_reactive_triggers_log
    ):
        # [11]
        session_id = _make_session_id(test_run_id, "ins_failed")
        insert(session_id, "newsapi", ["iran"], None, "failed")
        rows = list_by_session(session_id)
        assert rows[0]["status"] == "failed"

    def test_distinct_trigger_ids_across_calls(
        self, test_run_id, cleanup_reactive_triggers_log
    ):
        # [12] Each insert generates a fresh UUID4 — no collision.
        session_id = _make_session_id(test_run_id, "ins_distinct")
        id1 = insert(session_id, "newsapi", ["a"], None, "emitted")
        id2 = insert(session_id, "newsapi", ["b"], None, "emitted")
        assert id1 != id2


# ==========================================================
# list_by_session() — validation [13]
# ==========================================================

class TestListBySessionValidation:

    def test_raises_on_empty_session_id(self):
        # [13]
        with pytest.raises(ValueError, match="session_id"):
            list_by_session("")


# ==========================================================
# list_by_session() — live DB [14–17]
# ==========================================================

class TestListBySessionLive:

    def test_unknown_session_returns_empty(self, test_run_id):
        # [14] No cleanup fixture needed — nothing inserted.
        rows = list_by_session(
            _make_session_id(test_run_id, "lbs_unknown_xyz_9999")
        )
        assert rows == []

    def test_returns_rows_ordered_by_trigger_time_desc(
        self, test_run_id, cleanup_reactive_triggers_log
    ):
        # [15] Insert two rows for the same session with a small sleep between
        # them so trigger_time (server default NOW()) is distinguishable.
        session_id = _make_session_id(test_run_id, "lbs_order")
        insert(session_id, "newsapi", ["first"], None, "emitted")
        time.sleep(0.05)  # ensure NOW() ticks past microsecond resolution boundary
        insert(session_id, "newsapi", ["second"], None, "emitted")

        rows = list_by_session(session_id)
        assert len(rows) == 2
        # DESC ordering → most recently inserted appears first
        assert rows[0]["keywords"] == ["second"]
        assert rows[1]["keywords"] == ["first"]

    def test_only_returns_rows_for_requested_session(
        self, test_run_id, cleanup_reactive_triggers_log
    ):
        # [16] Insert one row per session; list_by_session(A) excludes B's row.
        session_a = _make_session_id(test_run_id, "lbs_sep_a")
        session_b = _make_session_id(test_run_id, "lbs_sep_b")
        insert(session_a, "newsapi", ["aaa"], None, "emitted")
        insert(session_b, "newsapi", ["bbb"], None, "emitted")

        rows_a = list_by_session(session_a)
        assert len(rows_a) == 1
        assert rows_a[0]["keywords"] == ["aaa"]
        assert rows_a[0]["session_id"] == session_a

    def test_row_dict_contains_all_expected_fields(
        self, test_run_id, cleanup_reactive_triggers_log
    ):
        # [17] Field-presence contract for downstream consumers (the agent
        # node or future debugging tools).
        session_id = _make_session_id(test_run_id, "lbs_fields")
        insert(session_id, "newsapi", ["iran"], 42, "emitted")
        rows = list_by_session(session_id)
        assert len(rows) == 1
        row = rows[0]
        expected_keys = {
            "trigger_id",
            "session_id",
            "trigger_time",
            "keywords",
            "source",
            "kafka_offset",
            "status",
        }
        assert set(row.keys()) == expected_keys
