"""
Gate 1 tests for `agent/prompts/evidence_rating.py` (Sprint 20 T20.2).

Pure module tests — no OpenAI calls, no node logic. Verifies the
module's public surface is shaped per spec §8.5.5 and the
agent-prompt-engineering skill (calibration anchors, permission to
admit gaps, plain-language justifications, no JSON instructions in
prompt body).
"""

from __future__ import annotations

from agent.prompts.evidence_rating import (
    BATCH_SIZE,
    RELEVANCE_ANCHOR_HIGH,
    RELEVANCE_ANCHOR_LOW,
    RELEVANCE_ANCHOR_MID,
    RESPONSE_SCHEMA,
    SYSTEM_PROMPT,
    build_user_message,
)


# ==========================================================
# Constants — sanity bounds
# ==========================================================
def test_batch_size_in_skill_recommended_range():
    """agent-prompt-engineering skill suggests 5-10 per batch."""
    assert 5 <= BATCH_SIZE <= 10


def test_relevance_anchors_ordered_low_mid_high():
    assert 0.0 <= RELEVANCE_ANCHOR_LOW < RELEVANCE_ANCHOR_MID < RELEVANCE_ANCHOR_HIGH <= 1.0


# ==========================================================
# System prompt — content invariants
# ==========================================================
def test_system_prompt_has_domain_context():
    """Skill P6: domain context anchors the model. Must mention Anizai."""
    assert "Anizai" in SYSTEM_PROMPT


def test_system_prompt_anchors_calibration():
    """Skill P2: calibration anchors at concrete examples, not 'rate 1-5'."""
    # Each anchor value should appear as a number in the prompt body
    assert "0.1" in SYSTEM_PROMPT
    assert "0.5" in SYSTEM_PROMPT
    assert "0.9" in SYSTEM_PROMPT


def test_system_prompt_grants_permission_to_admit_uncertainty():
    """Skill P3: explicit permission to be unsure prevents fabricated
    confidence. Look for the explicit calibration instruction."""
    lowered = SYSTEM_PROMPT.lower()
    assert "uncertain" in lowered or "not sure" in lowered


def test_system_prompt_rules_out_meta_references():
    """Justifications are user-facing — model must not write 'the agent
    decided' or similar (skill P4)."""
    lowered = SYSTEM_PROMPT.lower()
    assert "no meta-references" in lowered or "no jargon" in lowered


def test_system_prompt_rules_out_credibility_judgments():
    """Per evidence-handling skill: credibility comes from the source
    allowlist or vault metadata, NOT from per-call agent judgment."""
    assert "credibility" in SYSTEM_PROMPT.lower()


def test_system_prompt_under_800_words():
    """Skill P5: under 800 words. Beyond that, the model follows some
    rules and ignores others."""
    word_count = len(SYSTEM_PROMPT.split())
    assert word_count < 800, f"system prompt has {word_count} words"


# ==========================================================
# JSON schema — strict-mode shape
# ==========================================================
def test_response_schema_is_strict():
    assert RESPONSE_SCHEMA["strict"] is True


def test_response_schema_has_ratings_array():
    schema = RESPONSE_SCHEMA["schema"]
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert "ratings" in schema["properties"]
    assert schema["required"] == ["ratings"]


def test_ratings_array_caps_at_batch_size():
    ratings = RESPONSE_SCHEMA["schema"]["properties"]["ratings"]
    assert ratings["type"] == "array"
    assert ratings["minItems"] == 1
    assert ratings["maxItems"] == BATCH_SIZE


def test_each_rating_has_three_required_fields():
    item_schema = RESPONSE_SCHEMA["schema"]["properties"]["ratings"]["items"]
    assert item_schema["additionalProperties"] is False
    required = set(item_schema["required"])
    assert required == {"evidence_id", "relevance_score", "justification"}


def test_relevance_score_is_bounded_0_to_1_in_schema():
    item_schema = RESPONSE_SCHEMA["schema"]["properties"]["ratings"]["items"]
    score = item_schema["properties"]["relevance_score"]
    assert score["type"] == "number"
    assert score["minimum"] == 0.0
    assert score["maximum"] == 1.0


# ==========================================================
# build_user_message — formatting
# ==========================================================
def test_build_user_message_includes_question():
    msg = build_user_message(
        question="Will the Fed cut rates?",
        items=[],
    )
    assert "Will the Fed cut rates?" in msg


def test_build_user_message_includes_every_item_field():
    item = {
        "evidence_id": "ev-42",
        "source_type": "vault_news",
        "source_domain": "reuters.com",
        "published_at": "2026-05-04T12:00:00+00:00",
        "title": "Fed signals patience",
        "snippet": "Powell told the Senate banking committee...",
    }
    msg = build_user_message(question="Q?", items=[item])
    for value in item.values():
        assert str(value) in msg


def test_build_user_message_separates_items_with_dashes():
    items = [
        {"evidence_id": "a", "source_type": "vault_news",
         "source_domain": "x.com", "published_at": "", "title": "", "snippet": ""},
        {"evidence_id": "b", "source_type": "vault_news",
         "source_domain": "y.com", "published_at": "", "title": "", "snippet": ""},
    ]
    msg = build_user_message(question="Q?", items=items)
    # Two items → at least two `---` separators (one per block)
    assert msg.count("---") >= 2


def test_build_user_message_handles_missing_keys_gracefully():
    """An item dict missing a key shouldn't crash — defensive .get()."""
    msg = build_user_message(
        question="Q?",
        items=[{"evidence_id": "ev-1"}],   # only the id
    )
    assert "ev-1" in msg


def test_build_user_message_passes_snippet_through_verbatim():
    """rate_evidence is responsible for snippet truncation — the prompt
    builder does NOT re-truncate."""
    long_snippet = "x" * 1000
    msg = build_user_message(
        question="Q?",
        items=[{
            "evidence_id": "ev-1", "source_type": "vault_news",
            "source_domain": "x.com", "published_at": "", "title": "",
            "snippet": long_snippet,
        }],
    )
    assert long_snippet in msg
