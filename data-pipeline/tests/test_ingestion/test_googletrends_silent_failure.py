"""
Unit test for googletrends producer "raise on 0% success" — Phase 9.5 Stage B Item 5.

Previously: pytrends.trending_searches() returning ResponseError 404 on
every geo (KG-PHASE-C-7, Google moved its unofficial Trends endpoint)
was caught + logged + skipped, and the producer returned 0 cleanly.
Stage B Item 4 raises a RuntimeError when 0 geos succeeded.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def _make_producer_instance():
    """Build a minimal producer instance with the bits run_static touches."""
    from ingestion.googletrends_producer import GoogleTrendsProducer

    inst = GoogleTrendsProducer.__new__(GoogleTrendsProducer)
    inst._emitted = 0
    inst._pytrends = MagicMock()
    inst._producer = MagicMock()
    inst._build_raw_payload = MagicMock()
    inst._emit = MagicMock()
    return inst


def test_run_static_raises_when_every_geo_throws():
    """
    pytrends.trending_searches() raises for every geo → 0 Bronze
    messages → raise RuntimeError so Airflow marks the task failed.
    """
    inst = _make_producer_instance()
    # Make trending_searches raise for every call.
    inst._pytrends.trending_searches.side_effect = Exception(
        "ResponseError: The request failed: Google returned 404"
    )

    with pytest.raises(RuntimeError, match="All .* geo fetches failed"):
        inst.run_static()

    from ingestion.googletrends_producer import GEO_MATRIX
    assert inst._pytrends.trending_searches.call_count == len(GEO_MATRIX)


def test_run_static_does_not_raise_when_at_least_one_geo_succeeds():
    """
    If even one geo returns a usable DataFrame → run_static returns
    the count without raising.
    """
    import pandas as pd

    inst = _make_producer_instance()
    from ingestion.googletrends_producer import GEO_MATRIX

    # First geo returns a single trending keyword; rest raise.
    call_count = {"n": 0}

    def trending_side_effect(pn):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return pd.DataFrame({"keyword": ["test_keyword"]})
        raise Exception("404")

    inst._pytrends.trending_searches.side_effect = trending_side_effect
    inst._build_raw_payload.return_value = {"keyword": "test_keyword"}

    def emit_side_effect(_payload):
        inst._emitted += 1
    inst._emit.side_effect = emit_side_effect

    result = inst.run_static()
    assert result >= 1
    assert inst._pytrends.trending_searches.call_count == len(GEO_MATRIX)
