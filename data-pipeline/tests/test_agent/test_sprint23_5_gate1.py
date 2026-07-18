"""
Gate 1 unit tests for Sprint 23.5 — Pre-26 Remediation.

Pure unit-level: no Kafka, no Postgres, no Firestore, no live OpenAI. Covers
the three tracks' new/changed units:

  Track 1 (reactive / sufficiency correctness):
    - agent.utils.evidence_counting.count_raw_signals — raw pre-normalization
      package counting (R2)
    - agent.nodes.sufficiency_check.run — deterministic V1 rubric (signal
      floor + per-entity coverage), missing_dimensions as telemetry, attempt
      numbering (23.5.1 / 23.5.2)

  Track 2 (cost instrumentation):
    - agent.utils.llm_cost.compute_cost — per-model pricing, embedding
      zero-completion, unknown-model-zero, env override (23.5.8)
    - agent.utils.llm_cost.record_usage / extract_usage — shared capture (23.5.9/10)
    - per-site cost accumulation into state.total_cost_usd (23.5.10)
    - state.ForecastState.total_cost_usd annotation

  Track 3 (version hygiene):
    - AGENT_VERSION lives in settings, is re-exported by synthesize, imported
      by health, and is bumped to 0.5.0-sprint23.5 (23.5.12)

Spec references:
    - data-pipeline/docs/B_hub/sprint23_5_pre26_remediation.md §2/§3/§4/§5
    - cabinet-outputs/advisor/problem-reports/sprint23_5_advisor-ron-decisions.md
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import get_type_hints
from unittest.mock import MagicMock

import pytest

from agent.utils import evidence_counting, llm_cost


# ============================================================
# Track 1 — count_raw_signals (R2)
# ============================================================


class TestCountRawSignals:

    def test_empty_state_is_zero(self):
        assert evidence_counting.count_raw_signals({}) == 0

    def test_sums_all_three_packages(self):
        state = {
            "researcher_evidence": {"articles": [{}, {}, {}]},  # 3
            "pulse_evidence": {
                "market_consensus": [{}],          # 1
                "community_discussion": [{}, {}],  # 2
            },
            "market_evidence": {"fred_anomalies": [{}]},  # 1
        }
        assert evidence_counting.count_raw_signals(state) == 7

    def test_empty_flag_contributes_zero(self):
        state = {
            "researcher_evidence": {"empty": True, "articles": [{}, {}]},
            "pulse_evidence": None,
        }
        assert evidence_counting.count_raw_signals(state) == 0

    def test_polymarket_anchor_not_counted(self):
        # market_evidence.polymarket is the market anchor, not a signal —
        # only fred_anomalies count (mirrors rate_evidence).
        state = {
            "market_evidence": {
                "polymarket": {"current_odds": 0.6},
                "fred_anomalies": [{}, {}],
            }
        }
        assert evidence_counting.count_raw_signals(state) == 2

    def test_non_list_fields_ignored(self):
        state = {"researcher_evidence": {"articles": "not-a-list"}}
        assert evidence_counting.count_raw_signals(state) == 0


# ============================================================
# Track 1 — sufficiency_check rubric (23.5.1 / 23.5.2)
# ============================================================


def _researcher(*titles: str) -> dict:
    return {
        "articles": [
            {"title": t, "full_text_snippet": "", "source_platform": "newsapi"}
            for t in titles
        ]
    }


class TestSufficiencyCheck:

    def test_sufficient_when_floor_met_and_entities_covered(self):
        from agent.nodes import sufficiency_check

        # 5 articles (== default floor), both entities appear in titles.
        state = {
            "structured_intent": {"entities": ["Iran", "OPEC"]},
            "researcher_evidence": _researcher(
                "Iran tensions rise",
                "OPEC cuts output",
                "Iran and OPEC meet",
                "Markets watch Iran",
                "OPEC quota debate",
            ),
        }
        out = sufficiency_check.run(state)
        verdict = out["sufficiency_checks"][-1]
        assert verdict["is_sufficient"] is True
        assert verdict["missing_dimensions"] == []
        assert verdict["attempt"] == 1
        assert "sufficient" in verdict["reason"]

    def test_insufficient_when_below_signal_floor(self):
        from agent.nodes import sufficiency_check

        # Only 2 signals (< floor 5), entity covered — still insufficient.
        state = {
            "structured_intent": {"entities": ["Iran"]},
            "researcher_evidence": _researcher("Iran one", "Iran two"),
        }
        out = sufficiency_check.run(state)
        verdict = out["sufficiency_checks"][-1]
        assert verdict["is_sufficient"] is False
        assert verdict["missing_dimensions"] == []  # entity WAS covered
        assert "signals" in verdict["reason"]

    def test_uncovered_entity_becomes_missing_dimension(self):
        from agent.nodes import sufficiency_check

        # 6 signals (clears floor) but "Venezuela" appears nowhere.
        state = {
            "structured_intent": {"entities": ["Iran", "Venezuela"]},
            "researcher_evidence": _researcher(
                "Iran a", "Iran b", "Iran c", "Iran d", "Iran e", "Iran f"
            ),
        }
        out = sufficiency_check.run(state)
        verdict = out["sufficiency_checks"][-1]
        assert verdict["is_sufficient"] is False
        assert verdict["missing_dimensions"] == ["Venezuela"]
        assert "Venezuela" in verdict["reason"]

    def test_coverage_matches_snippet_and_is_case_insensitive(self):
        from agent.nodes import sufficiency_check

        # Entity not in title but present in snippet; case differs.
        articles = {
            "articles": [
                {"title": "Energy update", "full_text_snippet": "the iran deal",
                 "source_platform": "newsapi"},
            ] + [
                {"title": f"filler {i}", "full_text_snippet": "",
                 "source_platform": "newsapi"} for i in range(5)
            ]
        }
        state = {
            "structured_intent": {"entities": ["Iran"]},
            "researcher_evidence": articles,
        }
        out = sufficiency_check.run(state)
        assert out["sufficiency_checks"][-1]["missing_dimensions"] == []

    def test_no_entities_decided_by_floor_only(self):
        from agent.nodes import sufficiency_check

        state = {
            "structured_intent": {"entities": []},
            "researcher_evidence": _researcher(*[f"a{i}" for i in range(5)]),
        }
        out = sufficiency_check.run(state)
        verdict = out["sufficiency_checks"][-1]
        # Vacuous coverage + floor met → sufficient.
        assert verdict["is_sufficient"] is True
        assert verdict["missing_dimensions"] == []

    def test_attempt_increments_and_appends(self):
        from agent.nodes import sufficiency_check

        state = {
            "structured_intent": {"entities": ["Iran"]},
            "researcher_evidence": _researcher("Iran one"),
            "sufficiency_checks": [
                {"is_sufficient": False, "missing_dimensions": [],
                 "reason": "prior", "attempt": 1}
            ],
        }
        out = sufficiency_check.run(state)
        checks = out["sufficiency_checks"]
        assert len(checks) == 2  # prior preserved + new appended
        assert checks[-1]["attempt"] == 2

    def test_pulse_and_market_text_feed_coverage(self):
        from agent.nodes import sufficiency_check

        # Entity only appears in pulse + market packages; count from all.
        state = {
            "structured_intent": {"entities": ["Inflation"]},
            "pulse_evidence": {
                "market_consensus": [
                    {"market_id_ref": "inflation-2026",
                     "executive_summary": "Inflation expectations"},
                ],
                "community_discussion": [
                    {"title": "CPI thread", "top_technical_insights": ["x"]},
                ],
            },
            "market_evidence": {
                "fred_anomalies": [
                    {"indicator_name": "Inflation rate", "anomaly_flags": ["spike"]},
                    {"series_id": "CPIAUCSL", "anomaly_flags": []},
                    {"series_id": "PCE", "anomaly_flags": []},
                ],
            },
        }
        out = sufficiency_check.run(state)
        verdict = out["sufficiency_checks"][-1]
        # 1 + 1 + 3 = 5 signals (== floor) and "Inflation" covered.
        assert verdict["is_sufficient"] is True
        assert verdict["missing_dimensions"] == []

    def test_does_not_call_llm_or_emit_events(self):
        """Determinism (agent-design P5 / decision record R4/R6): the node is
        pure — no `agentEvents` key, no client kwarg. Same input → same output."""
        from agent.nodes import sufficiency_check
        import inspect

        sig = inspect.signature(sufficiency_check.run)
        assert list(sig.parameters.keys()) == ["state"], (
            "sufficiency_check.run takes state only — no LLM client (V1 is "
            "deterministic, no LLM)."
        )
        state = {
            "structured_intent": {"entities": ["Iran"]},
            "researcher_evidence": _researcher("Iran"),
        }
        out_a = sufficiency_check.run(dict(state))
        out_b = sufficiency_check.run(dict(state))
        assert out_a == out_b
        assert "agentEvents" not in out_a


# ============================================================
# Track 2 — llm_cost.compute_cost (23.5.8)
# ============================================================


class TestComputeCost:

    def test_gpt_4o_input_and_output_priced(self):
        # 0.0025 + 0.0100 per 1k each
        assert llm_cost.compute_cost("gpt-4o", 1000, 1000) == pytest.approx(0.0125)

    def test_gpt_4o_mini_priced(self):
        # 0.00015 + 0.00060
        assert llm_cost.compute_cost("gpt-4o-mini", 1000, 1000) == pytest.approx(0.00075)

    def test_embedding_zero_completion(self):
        # input-only; completion price is 0.0 and embeddings pass 0 completion
        assert llm_cost.compute_cost("text-embedding-3-small", 1000, 0) == pytest.approx(0.00002)

    def test_embedding_ignores_any_completion_via_zero_price(self):
        # Even if a nonzero completion sneaks in, the output price is 0.0.
        assert llm_cost.compute_cost("text-embedding-3-small", 1000, 500) == pytest.approx(0.00002)

    def test_unknown_model_is_zero_and_never_raises(self):
        assert llm_cost.compute_cost("gpt-does-not-exist", 5000, 5000) == 0.0

    def test_none_token_counts_treated_as_zero(self):
        assert llm_cost.compute_cost("gpt-4o", None, None) == 0.0

    def test_partial_tokens(self):
        # 500 prompt @ 0.0025/1k = 0.00125 ; 250 completion @ 0.0100/1k = 0.0025
        assert llm_cost.compute_cost("gpt-4o", 500, 250) == pytest.approx(0.00375)


class TestPricingTableAndEnvOverride:

    def test_default_pricing_has_three_models(self):
        pricing = llm_cost.get_pricing()
        assert set(pricing) == {"gpt-4o", "gpt-4o-mini", "text-embedding-3-small"}
        assert pricing["gpt-4o"] == (0.0025, 0.0100)

    def test_env_override_applies(self, monkeypatch):
        monkeypatch.setenv("LLM_COST_GPT_4O_INPUT_PER_1K", "0.005")
        monkeypatch.setenv("LLM_COST_GPT_4O_OUTPUT_PER_1K", "0.020")
        # 1k/1k → 0.005 + 0.020 = 0.025
        assert llm_cost.compute_cost("gpt-4o", 1000, 1000) == pytest.approx(0.025)
        assert llm_cost.get_pricing()["gpt-4o"] == (0.005, 0.020)

    def test_malformed_env_override_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("LLM_COST_GPT_4O_INPUT_PER_1K", "not-a-number")
        assert llm_cost.compute_cost("gpt-4o", 1000, 0) == pytest.approx(0.0025)


class TestExtractAndRecordUsage:

    def _resp(self, prompt, completion, total):
        return SimpleNamespace(
            usage=SimpleNamespace(
                prompt_tokens=prompt,
                completion_tokens=completion,
                total_tokens=total,
            )
        )

    def test_extract_usage_splits_fields(self):
        assert llm_cost.extract_usage(self._resp(30, 70, 100)) == (30, 70, 100)

    def test_extract_usage_missing_usage_is_zeroes(self):
        assert llm_cost.extract_usage(SimpleNamespace()) == (0, 0, 0)

    def test_extract_usage_reconstructs_total_from_split(self):
        resp = SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=0)
        )
        assert llm_cost.extract_usage(resp) == (10, 5, 15)

    def test_record_usage_returns_total_and_cost(self):
        total, cost = llm_cost.record_usage(
            "gpt-4o", self._resp(1000, 1000, 2000), site="synthesize"
        )
        assert total == 2000
        assert cost == pytest.approx(0.0125)

    def test_record_usage_missing_usage(self):
        total, cost = llm_cost.record_usage(
            "gpt-4o", SimpleNamespace(), site="synthesize"
        )
        assert total == 0
        assert cost == 0.0


# ============================================================
# Track 2 — per-site cost accumulation (23.5.10)
# ============================================================


class TestPerSiteCostAccumulation:

    def test_build_embedding_accumulates_cost_and_tokens(self):
        from agent.nodes import build_embedding

        client = MagicMock()
        client.embeddings.create.return_value = SimpleNamespace(
            data=[SimpleNamespace(embedding=[0.0] * 1536)],
            usage=SimpleNamespace(prompt_tokens=8, completion_tokens=0, total_tokens=8),
        )
        out = build_embedding.run(
            {"raw_question": "Will the Fed cut?", "total_cost_usd": 0.001},
            client=client,
        )
        # embedding @ 0.00002/1k * 8/1000 = 1.6e-7, added to prior 0.001
        assert out["total_cost_usd"] == pytest.approx(0.001 + 8 / 1000 * 0.00002)
        assert out["total_tokens_used"] == 8

    def test_state_has_total_cost_usd_annotation(self):
        from agent.state import ForecastState

        hints = get_type_hints(ForecastState)
        assert "total_cost_usd" in hints
        assert hints["total_cost_usd"] is float


# ============================================================
# Track 3 — version hygiene (23.5.12)
# ============================================================


class TestVersionHygiene:

    def test_agent_version_in_settings(self):
        from agent.config import settings
        # Sprint 26 T26.5 bumped the base to 0.5.0-sprint26. With no sha env
        # (unit test), AGENT_VERSION is just the base — no trailing '+'.
        assert settings.AGENT_VERSION == "0.5.0-sprint26"

    def test_synthesize_reexports_settings_version(self):
        from agent.config import settings
        from agent.nodes import synthesize
        assert synthesize.AGENT_VERSION == settings.AGENT_VERSION

    def test_health_imports_version_from_settings(self):
        from agent.config import settings
        import agent.health as health
        assert health.AGENT_VERSION == settings.AGENT_VERSION

    def test_version_env_overridable(self, monkeypatch):
        monkeypatch.setenv("AGENT_VERSION", "9.9.9-hotfix")
        import agent.config.settings as settings_mod
        importlib.reload(settings_mod)
        try:
            assert settings_mod.AGENT_VERSION == "9.9.9-hotfix"
        finally:
            monkeypatch.undo()
            importlib.reload(settings_mod)

    def test_version_appends_git_short_sha(self, monkeypatch):
        # Sprint 26 T26.5: when the build injects AGENT_GIT_COMMIT_SHORT_SHA,
        # AGENT_VERSION resolves to `<base>+<sha>`; the base stays env-overridable.
        monkeypatch.setenv("AGENT_VERSION", "0.5.0-sprint26")
        monkeypatch.setenv("AGENT_GIT_COMMIT_SHORT_SHA", "abc1234")
        import agent.config.settings as settings_mod
        importlib.reload(settings_mod)
        try:
            assert settings_mod.AGENT_GIT_COMMIT_SHORT_SHA == "abc1234"
            assert settings_mod.AGENT_VERSION == "0.5.0-sprint26+abc1234"
        finally:
            monkeypatch.undo()
            importlib.reload(settings_mod)
