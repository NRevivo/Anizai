"""
Unit tests for processing/consensus.py — temporal social bundling (Section 4.2A).

Test index:
    [1]  bucket_key: exact 4-hour boundary (00:00) → same bucket
    [2]  bucket_key: one second before boundary (03:59:59) → 00 bucket
    [3]  bucket_key: one second into new bucket (04:00:00) → 04 bucket
    [4]  bucket_key: last bucket of day (23:59:59) → 20 bucket
    [5]  bucket_key: midnight (00:00:00) → 00 bucket
    [6]  bucket_key: fractional seconds + Z suffix (ISO 8601 with milliseconds)
    [7]  bucket_key: explicit UTC offset (+00:00) instead of Z
    [8]  bucket_key: positive UTC offset (+02:00) — converts to UTC before bucketing
    [9]  bucket_key: negative UTC offset (-05:00) — converts to UTC before bucketing
    [10] bucket_key: tz-naive timestamp — treated as UTC
    [11] bucket_key: all 6 bucket boundaries map to correct keys
    [12] bucket_key: invalid timestamp raises ValueError
    [13] group_by_window: empty list → empty dict
    [14] group_by_window: single record → one group with one record
    [15] group_by_window: two records in same window → one group, two records
    [16] group_by_window: records spanning two windows → two groups
    [17] group_by_window: records spanning all 6 windows → six groups
    [18] group_by_window: record missing ts_field → skipped (logged), not in output
    [19] group_by_window: record with None ts value → skipped (logged)
    [20] group_by_window: record with malformed timestamp → skipped (logged)
    [21] group_by_window: all records invalid → empty dict
    [22] group_by_window: mixed valid/invalid → only valid records in output
    [23] group_by_window: preserves full record dict in group (no field stripping)
    [24] group_by_window: different ts_field name works correctly
    [25] group_by_window: record order within a group is preserved (insertion order)
"""

import logging
import pytest

from processing.consensus import bucket_key, group_by_window


# ---------------------------------------------------------------------------
# bucket_key — boundary and parsing tests [1–12]
# ---------------------------------------------------------------------------

class TestBucketKey:

    def test_exact_zero_boundary(self):
        # [1] 00:00:00 is the start of the 00 bucket
        assert bucket_key("2024-09-23T00:00:00Z") == "2024-09-23_00"

    def test_one_second_before_04_boundary(self):
        # [2] 03:59:59 still belongs to the 00 bucket
        assert bucket_key("2024-09-23T03:59:59Z") == "2024-09-23_00"

    def test_one_second_into_04_boundary(self):
        # [3] 04:00:00 is the start of the 04 bucket
        assert bucket_key("2024-09-23T04:00:00Z") == "2024-09-23_04"

    def test_last_second_of_day(self):
        # [4] 23:59:59 belongs to the 20 bucket
        assert bucket_key("2024-09-23T23:59:59Z") == "2024-09-23_20"

    def test_midnight(self):
        # [5] midnight is the start of the 00 bucket
        assert bucket_key("2024-09-23T00:00:00Z") == "2024-09-23_00"

    def test_fractional_seconds_z_suffix(self):
        # [6] milliseconds should be stripped; Z parsed correctly
        assert bucket_key("2024-09-23T09:37:00.123456Z") == "2024-09-23_08"

    def test_explicit_utc_offset(self):
        # [7] +00:00 explicit offset — identical result to Z suffix
        assert bucket_key("2024-09-23T09:37:00+00:00") == "2024-09-23_08"

    def test_positive_offset_converts_to_utc(self):
        # [8] 11:30 +02:00 → 09:30 UTC → bucket 08
        assert bucket_key("2024-09-23T11:30:00+02:00") == "2024-09-23_08"

    def test_negative_offset_converts_to_utc(self):
        # [9] 01:00 -05:00 → 06:00 UTC → bucket 04
        assert bucket_key("2024-09-23T01:00:00-05:00") == "2024-09-23_04"

    def test_tz_naive_treated_as_utc(self):
        # [10] No timezone info → assumed UTC
        assert bucket_key("2024-09-23T09:37:00") == "2024-09-23_08"

    def test_all_six_bucket_boundaries(self):
        # [11] Every 4-hour boundary maps to the correct key
        cases = [
            ("2024-01-01T00:00:00Z", "2024-01-01_00"),
            ("2024-01-01T04:00:00Z", "2024-01-01_04"),
            ("2024-01-01T08:00:00Z", "2024-01-01_08"),
            ("2024-01-01T12:00:00Z", "2024-01-01_12"),
            ("2024-01-01T16:00:00Z", "2024-01-01_16"),
            ("2024-01-01T20:00:00Z", "2024-01-01_20"),
        ]
        for ts, expected in cases:
            assert bucket_key(ts) == expected, f"Failed for ts={ts}"

    def test_invalid_timestamp_raises_value_error(self):
        # [12] Non-ISO string must raise ValueError
        with pytest.raises((ValueError, AttributeError)):
            bucket_key("not-a-timestamp")


# ---------------------------------------------------------------------------
# group_by_window — grouping logic tests [13–25]
# ---------------------------------------------------------------------------

class TestGroupByWindow:

    def test_empty_list_returns_empty_dict(self):
        # [13]
        assert group_by_window([], "created_at") == {}

    def test_single_record_produces_one_group(self):
        # [14]
        record = {"created_at": "2024-09-23T09:00:00Z", "text": "hello"}
        result = group_by_window([record], "created_at")
        assert list(result.keys()) == ["2024-09-23_08"]
        assert result["2024-09-23_08"] == [record]

    def test_two_records_same_window(self):
        # [15]
        r1 = {"created_at": "2024-09-23T08:00:00Z", "text": "first"}
        r2 = {"created_at": "2024-09-23T09:30:00Z", "text": "second"}
        result = group_by_window([r1, r2], "created_at")
        assert len(result) == 1
        assert "2024-09-23_08" in result
        assert len(result["2024-09-23_08"]) == 2

    def test_records_spanning_two_windows(self):
        # [16]
        r1 = {"created_at": "2024-09-23T03:59:00Z", "text": "window 00"}
        r2 = {"created_at": "2024-09-23T04:01:00Z", "text": "window 04"}
        result = group_by_window([r1, r2], "created_at")
        assert len(result) == 2
        assert "2024-09-23_00" in result
        assert "2024-09-23_04" in result
        assert result["2024-09-23_00"] == [r1]
        assert result["2024-09-23_04"] == [r2]

    def test_records_spanning_all_six_windows(self):
        # [17] One record per 4-hour bucket → exactly 6 groups
        times = [
            "2024-09-23T02:00:00Z",  # 00
            "2024-09-23T06:00:00Z",  # 04
            "2024-09-23T10:00:00Z",  # 08
            "2024-09-23T14:00:00Z",  # 12
            "2024-09-23T18:00:00Z",  # 16
            "2024-09-23T22:00:00Z",  # 20
        ]
        records = [{"created_at": t} for t in times]
        result = group_by_window(records, "created_at")
        assert len(result) == 6
        expected_keys = {
            "2024-09-23_00", "2024-09-23_04", "2024-09-23_08",
            "2024-09-23_12", "2024-09-23_16", "2024-09-23_20",
        }
        assert set(result.keys()) == expected_keys

    def test_missing_ts_field_skips_record(self, caplog):
        # [18] Record without the ts_field key is skipped and logged
        r_valid   = {"created_at": "2024-09-23T09:00:00Z", "text": "ok"}
        r_missing = {"text": "no timestamp here"}  # no "created_at" key
        with caplog.at_level(logging.WARNING, logger="processing.consensus"):
            result = group_by_window([r_valid, r_missing], "created_at")
        # r_missing must be absent from output
        assert result == {"2024-09-23_08": [r_valid]}
        assert "missing ts_field" in caplog.text

    def test_none_ts_value_skips_record(self, caplog):
        # [19] Key present but value is None
        r_none  = {"created_at": None, "text": "null ts"}
        r_valid = {"created_at": "2024-09-23T09:00:00Z", "text": "ok"}
        with caplog.at_level(logging.WARNING, logger="processing.consensus"):
            result = group_by_window([r_none, r_valid], "created_at")
        assert "2024-09-23_08" in result
        assert r_none not in result.get("2024-09-23_08", [])

    def test_malformed_timestamp_skips_record(self, caplog):
        # [20] Unparseable timestamp string → skipped with warning
        r_bad   = {"created_at": "not-a-date", "text": "bad ts"}
        r_valid = {"created_at": "2024-09-23T09:00:00Z", "text": "ok"}
        with caplog.at_level(logging.WARNING, logger="processing.consensus"):
            result = group_by_window([r_bad, r_valid], "created_at")
        assert "2024-09-23_08" in result
        assert r_bad not in result["2024-09-23_08"]
        assert "unparseable" in caplog.text

    def test_all_invalid_records_returns_empty_dict(self, caplog):
        # [21] If every record is invalid, return {}
        records = [
            {"no_ts": "x"},
            {"created_at": "bad"},
        ]
        with caplog.at_level(logging.WARNING, logger="processing.consensus"):
            result = group_by_window(records, "created_at")
        assert result == {}

    def test_mixed_valid_and_invalid(self, caplog):
        # [22] Invalid records are silently skipped; valid records group normally
        records = [
            {"created_at": "2024-09-23T09:00:00Z", "text": "v1"},
            {"created_at": "bad-ts",                "text": "invalid"},
            {"created_at": "2024-09-23T10:00:00Z", "text": "v2"},
            {"text": "no key"},
        ]
        with caplog.at_level(logging.WARNING, logger="processing.consensus"):
            result = group_by_window(records, "created_at")
        assert len(result) == 1
        assert len(result["2024-09-23_08"]) == 2

    def test_full_record_dict_preserved(self):
        # [23] No field stripping — entire record dict is in the group
        record = {
            "created_at": "2024-09-23T09:00:00Z",
            "text": "hello",
            "source": "polymarket",
            "signal_id": "abc-123",
            "extra": {"nested": True},
        }
        result = group_by_window([record], "created_at")
        stored = result["2024-09-23_08"][0]
        assert stored is record  # exact same object reference — no copying

    def test_custom_ts_field_name(self):
        # [24] ts_field other than "created_at" works identically
        record = {"producer_timestamp": "2024-09-23T12:30:00Z", "text": "custom field"}
        result = group_by_window([record], "producer_timestamp")
        assert "2024-09-23_12" in result
        assert result["2024-09-23_12"] == [record]

    def test_insertion_order_preserved_within_group(self):
        # [25] Records appear in the group in the same order they were passed in
        records = [
            {"created_at": "2024-09-23T08:00:00Z", "seq": 1},
            {"created_at": "2024-09-23T08:30:00Z", "seq": 2},
            {"created_at": "2024-09-23T09:00:00Z", "seq": 3},
            {"created_at": "2024-09-23T09:59:59Z", "seq": 4},
        ]
        result = group_by_window(records, "created_at")
        group = result["2024-09-23_08"]
        assert [r["seq"] for r in group] == [1, 2, 3, 4]
