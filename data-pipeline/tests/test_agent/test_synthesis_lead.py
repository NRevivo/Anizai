"""
Gate 1 tests for `agent/prompts/synthesis_lead.py` (Sprint 20 T20.4).

Pure module tests — no OpenAI calls, no node logic. Verifies the
module's public surface is shaped per spec §8.6/§8.7.2 and the
agent-prompt-engineering skill (calibration anchors, permission to
admit gaps, plain-language copy rules, two few-shot examples).
"""

from __future__ import annotations

from agent.prompts.synthesis_lead import (
    KEY_FACTORS_MAX,
    KEY_FACTORS_MIN,
    PROBABILITY_ANCHOR_HIGH,
    PROBABILITY_ANCHOR_LOW,
    PROBABILITY_ANCHOR_MID,
    REASONING_STEPS_MAX,
    REASONING_STEPS_MIN,
    RESPONSE_SCHEMA,
    SYSTEM_PROMPT,
    build_user_message,
)


# ==========================================================
# Constants — sanity bounds
# ==========================================================
def test_key_factors_range_matches_spec():
    """Spec §8.7.2: '3-5 drivers, ranked.'"""
    assert KEY_FACTORS_MIN == 3
    assert KEY_FACTORS_MAX == 5


def test_reasoning_steps_range_matches_q3_resolution():
    """Per Q3 of the Sprint 20 plan: 4-6 post-hoc reasoning steps."""
    assert REASONING_STEPS_MIN == 4
    assert REASONING_STEPS_MAX == 6


def test_probability_anchors_ordered_low_mid_high():
    assert 0.0 <= PROBABILITY_ANCHOR_LOW < PROBABILITY_ANCHOR_MID < PROBABILITY_ANCHOR_HIGH <= 1.0


# ==========================================================
# System prompt — content invariants
# ==========================================================
def test_system_prompt_has_domain_context():
    assert "Anizai" in SYSTEM_PROMPT


def test_system_prompt_anchors_calibration_for_probability():
    """Skill P2: anchored examples at 0.1/0.5/0.9, not just 'rate 0-1'."""
    assert "0.1" in SYSTEM_PROMPT
    assert "0.5" in SYSTEM_PROMPT
    assert "0.9" in SYSTEM_PROMPT


def test_system_prompt_grants_permission_to_admit_uncertainty():
    """Skill P3: explicit 'better to say I don't know' instruction
    prevents fabricated confidence on sparse evidence."""
    lowered = SYSTEM_PROMPT.lower()
    assert "uncertainty" in lowered or "uncertain" in lowered


def test_system_prompt_includes_two_few_shot_examples():
    """Skill P5: 1-2 representative examples. The synthesis prompt
    pairs a rich-evidence example with a sparse-evidence example so
    the model sees both calibration regimes."""
    assert "EXAMPLE 1" in SYSTEM_PROMPT
    assert "EXAMPLE 2" in SYSTEM_PROMPT


def test_system_prompt_describes_what_i_didnt_find():
    """SessionResult.whatIDidntFind is the gap-acknowledgment field —
    prompt must teach the model when to populate it."""
    assert "WHAT I DIDN'T FIND" in SYSTEM_PROMPT or "what_i_didnt_find" in SYSTEM_PROMPT


def test_system_prompt_blocks_meta_references():
    """Skill P4: user-facing strings must not say 'the agent decided'
    or similar."""
    lowered = SYSTEM_PROMPT.lower()
    assert "meta-references" in lowered or "the agent decided" in lowered


def test_system_prompt_describes_evidence_overlay():
    """Synthesis must produce an overlay entry per evidence item — the
    used_in_answer / impact_on_forecast / impact_magnitude fields are
    where post-synthesis ranking gets its inputs."""
    assert "evidence_overlay" in SYSTEM_PROMPT or "EVIDENCE OVERLAY" in SYSTEM_PROMPT


def test_system_prompt_under_2000_words():
    """Synthesis_lead prompt is necessarily longer than evidence_rating
    (more fields, two examples) — but still under the agent-prompt-
    engineering soft cap (raised here from the 800-word default to
    accommodate two few-shot examples)."""
    word_count = len(SYSTEM_PROMPT.split())
    assert word_count < 2000, f"system prompt has {word_count} words"


# ==========================================================
# JSON schema — strict-mode shape
# ==========================================================
def test_response_schema_is_strict():
    assert RESPONSE_SCHEMA["strict"] is True


def test_response_schema_has_all_session_result_fields():
    """Every SessionResult-shaping field must be required so the model
    can't silently omit one and break the §8.7.2 contract."""
    schema = RESPONSE_SCHEMA["schema"]
    required = set(schema["required"])
    expected = {
        "final_probability", "confidence", "consensus_score",
        "bottom_line_answer", "detailed_explanation", "summary_markdown",
        "market_comparison_insight", "sentiment_analysis_insight",
        "evidence_feed_summary",
        "what_i_didnt_find", "key_factors", "reasoning_chain",
        "evidence_overlay",
    }
    assert required == expected


def test_probability_fields_are_bounded_0_to_1():
    schema = RESPONSE_SCHEMA["schema"]["properties"]
    for field in ("final_probability", "confidence", "consensus_score"):
        prop = schema[field]
        assert prop["type"] == "number"
        assert prop["minimum"] == 0.0
        assert prop["maximum"] == 1.0


def test_key_factors_array_caps_match_constants():
    kf = RESPONSE_SCHEMA["schema"]["properties"]["key_factors"]
    assert kf["minItems"] == KEY_FACTORS_MIN
    assert kf["maxItems"] == KEY_FACTORS_MAX


def test_each_key_factor_has_required_fields():
    item = RESPONSE_SCHEMA["schema"]["properties"]["key_factors"]["items"]
    assert item["additionalProperties"] is False
    assert set(item["required"]) == {
        "label", "description", "weight", "direction", "evidence_ids",
    }


def test_key_factor_direction_excludes_neutral_and_context_only():
    """KeyFactor.direction is restricted to 'increases'/'decreases' —
    neutral/context_only items don't earn a key_factors slot per
    schemas.py docstring."""
    direction = (
        RESPONSE_SCHEMA["schema"]["properties"]["key_factors"]["items"]
        ["properties"]["direction"]
    )
    assert set(direction["enum"]) == {"increases", "decreases"}


def test_reasoning_chain_array_caps_match_constants():
    rc = RESPONSE_SCHEMA["schema"]["properties"]["reasoning_chain"]
    assert rc["minItems"] == REASONING_STEPS_MIN
    assert rc["maxItems"] == REASONING_STEPS_MAX


def test_evidence_overlay_includes_full_impact_enum():
    """EvidenceItem.impact_on_forecast has 4 values — the overlay must
    accept all four (vs key_factors which is restricted to 2)."""
    direction = (
        RESPONSE_SCHEMA["schema"]["properties"]["evidence_overlay"]["items"]
        ["properties"]["impact_on_forecast"]
    )
    assert set(direction["enum"]) == {
        "increases", "decreases", "neutral", "context_only",
    }


# ==========================================================
# build_user_message — formatting
# ==========================================================
def test_build_user_message_includes_question():
    msg = build_user_message(
        question="Will the Fed cut rates?",
        structured_intent={},
        polymarket_market=None,
        evidence=[],
    )
    assert "Will the Fed cut rates?" in msg


def test_build_user_message_renders_structured_intent():
    msg = build_user_message(
        question="Q?",
        structured_intent={
            "intent": "forecast",
            "domain": "macro",
            "entities": ["Federal Reserve"],
        },
        polymarket_market=None,
        evidence=[],
    )
    assert "forecast" in msg
    assert "macro" in msg
    assert "Federal Reserve" in msg


def test_build_user_message_handles_no_market():
    """Sprint 20 D3: polymarket_market is always None until KG-PHASE8-12
    closes. The message must signal this clearly to the prompt."""
    msg = build_user_message(
        question="Q?",
        structured_intent={},
        polymarket_market=None,
        evidence=[],
    )
    assert "no canonical market" in msg.lower() or "market context" in msg.lower()


def test_build_user_message_handles_polymarket_when_present():
    """Forward-compat: when KG-PHASE8-12 closes and polymarket_market
    is populated, the slug + odds should reach the prompt."""
    msg = build_user_message(
        question="Q?",
        structured_intent={},
        polymarket_market={
            "market_slug": "fed-rate-cut-q2-2026",
            "current_odds": 0.62,
        },
        evidence=[],
    )
    assert "fed-rate-cut-q2-2026" in msg
    assert "0.62" in msg


def test_build_user_message_renders_all_evidence_fields():
    item = {
        "evidence_id": "ev-42",
        "source_type": "vault_news",
        "credibility_tier": "tier_1",
        "relevance_score": 0.85,
        "recency_weight": 0.92,
        "title": "Powell signals patience on rate cuts",
        "snippet": "Federal Reserve Chair Jerome Powell told the Senate...",
    }
    msg = build_user_message(
        question="Q?",
        structured_intent={},
        polymarket_market=None,
        evidence=[item],
    )
    for value in item.values():
        assert str(value) in msg


def test_build_user_message_handles_empty_evidence():
    """Cold-start case: when evidence is empty, the message must
    explicitly tell the prompt to produce a low-confidence forecast."""
    msg = build_user_message(
        question="Q?",
        structured_intent={},
        polymarket_market=None,
        evidence=[],
    )
    lowered = msg.lower()
    assert "no evidence" in lowered or "0 items" in lowered


def test_build_user_message_numbers_evidence_items():
    items = [
        {
            "evidence_id": f"ev-{i}", "source_type": "vault_news",
            "credibility_tier": "tier_1", "relevance_score": 0.5,
            "recency_weight": 0.5, "title": f"Title {i}", "snippet": f"snip {i}",
        }
        for i in range(3)
    ]
    msg = build_user_message(
        question="Q?",
        structured_intent={},
        polymarket_market=None,
        evidence=items,
    )
    assert "[1]" in msg
    assert "[2]" in msg
    assert "[3]" in msg
