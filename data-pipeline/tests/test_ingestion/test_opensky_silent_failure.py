"""
Unit test for opensky producer "raise on 0% success" — Phase 9.5 Stage B Item 5.

Previously: if every bounding box's fetch failed (KG-PHASE-C-6, GKE cluster
cannot reach opensky-network.org), the producer returned 0 cleanly and
Airflow saw `task: success` despite zero Bronze messages emitted.
Stage B Item 4 raises a RuntimeError when emitted == 0 so the Airflow DAG
task is correctly marked `failed`.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _make_producer_instance():
    """Build a minimal producer instance with the bits run_static touches."""
    from ingestion.opensky_producer import OpenSkyProducer

    inst = OpenSkyProducer.__new__(OpenSkyProducer)
    inst._emitted = 0
    inst._request_delay = 0.0
    inst._producer = MagicMock()
    inst._ensure_token = MagicMock()
    inst._fetch_box = MagicMock(return_value=None)  # every box returns None
    inst._build_raw_payload = MagicMock()
    inst._emit = MagicMock()
    return inst


def test_run_static_raises_when_zero_boxes_succeed():
    """
    Every box's _fetch_box returns None → no Bronze messages emitted →
    raise RuntimeError. This is the Phase 9.5 Stage B Item 4 fix.
    """
    inst = _make_producer_instance()
    with pytest.raises(RuntimeError, match="All .* bounding-box fetches failed"):
        inst.run_static()
    # Confirm we did try every box (the loop ran to completion before
    # the raise).
    from ingestion.opensky_producer import BOUNDING_BOXES
    assert inst._fetch_box.call_count == len(BOUNDING_BOXES)


def test_run_static_does_not_raise_when_at_least_one_box_succeeds():
    """
    If even one box's fetch returns valid data → producer returns the
    emitted count without raising. Partial-success is preferred over a
    full failure.
    """
    inst = _make_producer_instance()
    from ingestion.opensky_producer import BOUNDING_BOXES

    # First box returns valid data; rest return None.
    def fetch_side_effect(box):
        if inst._fetch_box.call_count == 1:
            return {"states": []}  # any non-None
        return None

    inst._fetch_box.side_effect = fetch_side_effect
    inst._build_raw_payload.return_value = {"aircraft_count": 0}

    # The _emit call increments _emitted via the real producer — we
    # mock that side-effect manually.
    def emit_side_effect(_payload):
        inst._emitted += 1

    inst._emit.side_effect = emit_side_effect

    result = inst.run_static()
    assert result == 1  # one box succeeded
    assert inst._fetch_box.call_count == len(BOUNDING_BOXES)
