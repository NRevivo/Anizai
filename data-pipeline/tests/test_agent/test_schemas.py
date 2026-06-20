"""
Gate 1 tests for `agent/schemas.py` (Sprint 20 T20.1 supporting module).

Verifies the EvidenceItem Pydantic model enforces every field constraint
spec'd in §8.5.5: enum values, range bounds, default values, extra-field
rejection. Catches programming errors at the schema layer before they
reach LLM/Firestore code paths.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from agent.schemas import (
    SOURCE_TYPE_TO_FRONTEND_TYPE,
    EvidenceItem,
)


# ==========================================================
# Fixtures
# ==========================================================
def _minimum_valid_kwargs() -> dict:
    """The smallest payload that constructs a valid EvidenceItem."""
    now = datetime.now(tz=timezone.utc)
    return {
        "evidence_id": "ev-1",
        "source_type": "vault_news",
        "origin": "knowledge_vault",
        "title": "Test article",
        "source_domain": "reuters.com",
        "published_at": now,
        "fetched_at": now,
        "relevance_score": 0.7,
        "credibility_tier": "tier_1",
        "recency_weight": 0.5,
    }


# ==========================================================
# Happy path — minimum valid construction
# ==========================================================
def test_minimum_valid_construction_uses_defaults():
    item = EvidenceItem(**_minimum_valid_kwargs())
    # Defaults for influence fields
    assert item.used_in_answer is False
    assert item.impact_on_forecast == "context_only"
    assert item.impact_magnitude == 0.0
    assert item.is_key_evidence is False
    assert item.rank == 0
    # Defaults for content fields
    assert item.snippet == ""
    assert item.url is None
    assert item.justification == ""


def test_full_construction_round_trips_via_model_dump():
    payload = {
        **_minimum_valid_kwargs(),
        "snippet": "First 200 chars.",
        "url": "https://example.com/article",
        "used_in_answer": True,
        "impact_on_forecast": "increases",
        "impact_magnitude": 0.7,
        "is_key_evidence": True,
        "rank": 1,
        "justification": "Directly addresses the question.",
    }
    item = EvidenceItem(**payload)
    dumped = item.model_dump()
    # Critical fields preserved
    for key in ("evidence_id", "source_type", "title", "snippet", "url",
                "used_in_answer", "impact_on_forecast", "impact_magnitude",
                "is_key_evidence", "rank", "justification"):
        assert dumped[key] == payload[key]
    # Datetime fields preserved as datetime in default mode. Compare against
    # the payload's own values — not a freshly-sampled datetime.now(), which
    # differs by microseconds from the one _minimum_valid_kwargs() created.
    assert dumped["published_at"] == payload["published_at"]
    assert dumped["fetched_at"] == payload["fetched_at"]


# ==========================================================
# Enum enforcement (Patch 10 closed enums)
# ==========================================================
@pytest.mark.parametrize("invalid_source_type", [
    "vault_unknown",
    "news",            # frontend display type, not source_type
    "VAULT_NEWS",      # case-sensitive
    "",
])
def test_invalid_source_type_rejected(invalid_source_type: str):
    kwargs = _minimum_valid_kwargs()
    kwargs["source_type"] = invalid_source_type
    with pytest.raises(ValidationError):
        EvidenceItem(**kwargs)


@pytest.mark.parametrize("valid_source_type", [
    "vault_news", "vault_telegram", "vault_market", "vault_arxiv",
    "vault_hackernews", "vault_fred", "online_news", "online_blog",
])
def test_all_eight_source_types_accepted(valid_source_type: str):
    kwargs = _minimum_valid_kwargs()
    kwargs["source_type"] = valid_source_type
    EvidenceItem(**kwargs)  # constructs without error


def test_invalid_origin_rejected():
    kwargs = _minimum_valid_kwargs()
    kwargs["origin"] = "external"
    with pytest.raises(ValidationError):
        EvidenceItem(**kwargs)


def test_invalid_credibility_tier_rejected():
    kwargs = _minimum_valid_kwargs()
    kwargs["credibility_tier"] = "tier_4"
    with pytest.raises(ValidationError):
        EvidenceItem(**kwargs)


def test_invalid_impact_on_forecast_rejected():
    kwargs = _minimum_valid_kwargs()
    kwargs["impact_on_forecast"] = "boost"
    with pytest.raises(ValidationError):
        EvidenceItem(**kwargs)


# ==========================================================
# Range bounds
# ==========================================================
@pytest.mark.parametrize("field,bad_value", [
    ("relevance_score", -0.01),
    ("relevance_score", 1.01),
    ("recency_weight", -0.01),
    ("recency_weight", 1.5),
    ("impact_magnitude", -0.01),
    ("impact_magnitude", 2.0),
])
def test_out_of_range_floats_rejected(field: str, bad_value: float):
    kwargs = _minimum_valid_kwargs()
    kwargs[field] = bad_value
    with pytest.raises(ValidationError):
        EvidenceItem(**kwargs)


@pytest.mark.parametrize("field,boundary", [
    ("relevance_score", 0.0),
    ("relevance_score", 1.0),
    ("recency_weight", 0.0),
    ("recency_weight", 1.0),
])
def test_inclusive_range_boundaries_accepted(field: str, boundary: float):
    kwargs = _minimum_valid_kwargs()
    kwargs[field] = boundary
    EvidenceItem(**kwargs)


# ==========================================================
# extra="forbid" — typo guard
# ==========================================================
def test_unknown_field_rejected():
    kwargs = _minimum_valid_kwargs()
    kwargs["relavance_score"] = 0.9   # typo
    with pytest.raises(ValidationError):
        EvidenceItem(**kwargs)


# ==========================================================
# Frontend display-type mapping
# ==========================================================
def test_source_type_to_frontend_type_covers_every_enum_value():
    """If a new source_type lands without a frontend-type mapping the
    write_to_firestore (T20.5) code path will silently drop the type
    field. Catch that here."""
    expected = {
        "vault_news", "vault_telegram", "vault_market", "vault_arxiv",
        "vault_hackernews", "vault_fred", "online_news", "online_blog",
    }
    assert set(SOURCE_TYPE_TO_FRONTEND_TYPE.keys()) == expected


def test_source_type_to_frontend_type_values_are_in_partner_set():
    """Frontend filter tabs accept these four type values per Patch 10
    table. Adding a new value here would require partner agreement."""
    allowed = {"news", "social", "expert", "market"}
    assert set(SOURCE_TYPE_TO_FRONTEND_TYPE.values()) <= allowed
