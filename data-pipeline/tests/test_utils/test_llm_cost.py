"""
Gate 2 — Pipeline LLM Cost Layer Tests (Phase 7B.5-I, T5).

First dedicated coverage for the llm_cost module ANYWHERE in the project —
the agent-side original has only indirect coverage (Sprint 23.5 gate tests
assert state accumulation, not pricing math). This suite tests the pipeline
copy `utils/llm_cost.py` to the §4 quality bar:

  [1]  Exact pricing math for ALL FOUR models, incl. the 4x input/output
       asymmetry on gpt-4o and the 3x asymmetry on gpt-3.5-turbo.
  [2]  Env-override: LLM_COST_* vars change the price without reload;
       malformed values keep the default + warn.
  [3]  Unknown model → $0.00 + warning, never raises.
  [4]  extract_usage edge matrix: embedding responses (no completion_tokens),
       missing usage, absent total reconstructed from the split, None fields.
  [5]  record_usage: correct llm_cost_events row shape (site/source/model/
       tokens/cost/trace/run), fail-open on DB error, log line emitted
       BEFORE the insert attempt, empty RUN_ID stored as NULL, explicit
       run_id precedence over settings.RUN_ID.

The DB insert is intercepted by the autouse `llm_cost_insert_calls` conftest
fixture (stubs persistence.llm_cost_events.insert_event and captures kwargs),
so every test here is hermetic — no Postgres required. Real-INSERT round
trips are Gate 3 (tests/test_persistence/, marked llm_cost_db).

References:
    - docs/A_pipeline/plans/phase7b5i_filter_observability_and_cost.md §2.4, §4
    - utils/llm_cost.py (module under test — the D1 copy, two deltas)
    - agent/utils/llm_cost.py (source of the copy; NOT under test here)
"""

from __future__ import annotations

import logging
import math
from types import SimpleNamespace

import pytest

from utils.llm_cost import (
    _DEFAULT_PRICING,
    _env_key,
    compute_cost,
    extract_usage,
    get_pricing,
    record_usage,
)


# ==========================================================
# Helpers
# ==========================================================

def _chat_response(prompt: int = 100, completion: int = 50, total: int | None = None):
    """Minimal OpenAI-SDK-shaped chat response (only .usage is read)."""
    if total is None:
        total = prompt + completion
    return SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=total,
        )
    )


def _embedding_response(prompt: int = 42):
    """
    Embedding-shaped response: usage carries prompt_tokens + total_tokens
    but NO completion_tokens attribute — exactly the OpenAI embeddings
    contract extract_usage must default to 0.
    """
    return SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=prompt,
            total_tokens=prompt,
        )
    )


# ==========================================================
# [1] Exact pricing math — all four models
# ==========================================================

class TestPricingMath:
    """compute_cost must reproduce the §2.4 price table to the cent-fraction."""

    def test_gpt_4o_exact_value(self):
        # 1000 prompt @ 0.0025 + 1000 completion @ 0.0100 = 0.0125
        assert compute_cost("gpt-4o", 1000, 1000) == pytest.approx(0.0125)

    def test_gpt_4o_output_is_4x_input(self):
        """The gpt-4o asymmetry the plan calls out: output = 4x input."""
        input_only  = compute_cost("gpt-4o", 1000, 0)
        output_only = compute_cost("gpt-4o", 0, 1000)
        assert output_only == pytest.approx(4.0 * input_only)

    def test_gpt_4o_mini_exact_value(self):
        # 1000 @ 0.00015 + 1000 @ 0.00060 = 0.00075
        assert compute_cost("gpt-4o-mini", 1000, 1000) == pytest.approx(0.00075)

    def test_gpt_4o_mini_output_is_4x_input(self):
        assert compute_cost("gpt-4o-mini", 0, 1000) == pytest.approx(
            4.0 * compute_cost("gpt-4o-mini", 1000, 0)
        )

    def test_gpt_3_5_turbo_exact_value(self):
        """Delta 1 of the copy: the translation model must be priced."""
        # 1000 @ 0.0005 + 1000 @ 0.0015 = 0.0020
        assert compute_cost("gpt-3.5-turbo", 1000, 1000) == pytest.approx(0.0020)

    def test_gpt_3_5_turbo_output_is_3x_input(self):
        assert compute_cost("gpt-3.5-turbo", 0, 1000) == pytest.approx(
            3.0 * compute_cost("gpt-3.5-turbo", 1000, 0)
        )

    def test_embedding_exact_value_and_zero_output_price(self):
        # 1000 @ 0.00002 = 0.00002; completion tokens must contribute nothing
        assert compute_cost("text-embedding-3-small", 1000, 0) == pytest.approx(0.00002)
        assert compute_cost("text-embedding-3-small", 1000, 5000) == pytest.approx(0.00002)

    def test_fractional_token_counts_scale_linearly(self):
        # 500 prompt gpt-4o = 0.00125; 250 completion = 0.0025
        assert compute_cost("gpt-4o", 500, 0) == pytest.approx(0.00125)
        assert compute_cost("gpt-4o", 0, 250) == pytest.approx(0.0025)

    def test_zero_tokens_cost_zero(self):
        for model in _DEFAULT_PRICING:
            assert compute_cost(model, 0, 0) == 0.0

    def test_none_token_counts_treated_as_zero(self):
        """compute_cost coerces None → 0 (defensive int(x or 0))."""
        assert compute_cost("gpt-4o", None, None) == 0.0

    def test_pipeline_prices_match_agent_copy(self):
        """
        KG-PHASE-9.5-9 reconciliation guard: the three models shared with
        agent/utils/llm_cost.py must carry IDENTICAL figures in both copies.
        gpt-3.5-turbo is pipeline-only (delta 1) and is excluded.
        """
        from agent.utils.llm_cost import _DEFAULT_PRICING as agent_pricing
        for model, agent_price in agent_pricing.items():
            assert model in _DEFAULT_PRICING, (
                f"{model} priced in agent copy but missing from pipeline copy"
            )
            assert _DEFAULT_PRICING[model] == agent_price, (
                f"{model} price drift between copies — reconcile per KG-PHASE-9.5-9"
            )


# ==========================================================
# [2] Env-override resolution
# ==========================================================

class TestEnvOverrides:
    """LLM_COST_* env vars must reprice without reload; malformed → default + warn."""

    def test_env_key_derivation(self):
        assert _env_key("gpt-4o", "INPUT") == "LLM_COST_GPT_4O_INPUT_PER_1K"
        assert _env_key("gpt-3.5-turbo", "OUTPUT") == "LLM_COST_GPT_3_5_TURBO_OUTPUT_PER_1K"
        assert _env_key("text-embedding-3-small", "INPUT") == (
            "LLM_COST_TEXT_EMBEDDING_3_SMALL_INPUT_PER_1K"
        )

    def test_override_changes_price_without_reload(self, monkeypatch):
        monkeypatch.setenv("LLM_COST_GPT_4O_INPUT_PER_1K", "0.005")
        # 1000 prompt @ overridden 0.005 (default is 0.0025)
        assert compute_cost("gpt-4o", 1000, 0) == pytest.approx(0.005)

    def test_override_applies_to_output_side_independently(self, monkeypatch):
        monkeypatch.setenv("LLM_COST_GPT_3_5_TURBO_OUTPUT_PER_1K", "0.003")
        # Output overridden, input untouched
        assert compute_cost("gpt-3.5-turbo", 1000, 1000) == pytest.approx(0.0005 + 0.003)

    def test_override_visible_in_get_pricing(self, monkeypatch):
        monkeypatch.setenv("LLM_COST_GPT_4O_MINI_INPUT_PER_1K", "0.001")
        assert get_pricing()["gpt-4o-mini"][0] == pytest.approx(0.001)

    def test_removing_override_restores_default(self, monkeypatch):
        monkeypatch.setenv("LLM_COST_GPT_4O_INPUT_PER_1K", "0.005")
        assert compute_cost("gpt-4o", 1000, 0) == pytest.approx(0.005)
        monkeypatch.delenv("LLM_COST_GPT_4O_INPUT_PER_1K")
        assert compute_cost("gpt-4o", 1000, 0) == pytest.approx(0.0025)

    def test_malformed_override_keeps_default_and_warns(self, monkeypatch, caplog):
        monkeypatch.setenv("LLM_COST_GPT_4O_INPUT_PER_1K", "not-a-number")
        with caplog.at_level(logging.WARNING, logger="utils.llm_cost"):
            cost = compute_cost("gpt-4o", 1000, 0)
        assert cost == pytest.approx(0.0025), "Malformed override must keep the default"
        assert "non-numeric override" in caplog.text

    def test_empty_string_override_keeps_default(self, monkeypatch):
        monkeypatch.setenv("LLM_COST_GPT_4O_INPUT_PER_1K", "   ")
        assert compute_cost("gpt-4o", 1000, 0) == pytest.approx(0.0025)

    def test_get_pricing_covers_all_default_models(self):
        assert set(get_pricing().keys()) == {
            "gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo", "text-embedding-3-small",
        }


# ==========================================================
# [3] Unknown-model policy
# ==========================================================

class TestUnknownModel:
    """Unknown model → $0.00 + warning; NEVER raises (fail-open philosophy)."""

    def test_unknown_model_returns_zero(self, caplog):
        with caplog.at_level(logging.WARNING, logger="utils.llm_cost"):
            cost = compute_cost("gpt-9-experimental", 1000, 1000)
        assert cost == 0.0
        assert "unknown model" in caplog.text

    def test_unknown_model_warning_names_the_env_vars(self, caplog):
        """The warning is the actionable half — it must name the fix."""
        with caplog.at_level(logging.WARNING, logger="utils.llm_cost"):
            compute_cost("gpt-9-experimental", 10, 10)
        assert "LLM_COST_GPT_9_EXPERIMENTAL_INPUT_PER_1K" in caplog.text

    def test_unknown_model_never_raises(self):
        # No exception even with pathological inputs
        assert compute_cost("", 0, 0) == 0.0
        assert compute_cost("weird/model:v2", None, None) == 0.0


# ==========================================================
# [4] extract_usage edge matrix
# ==========================================================

class TestExtractUsage:
    """Token extraction must survive every response shape OpenAI produces."""

    def test_chat_response_full_split(self):
        assert extract_usage(_chat_response(100, 50, 150)) == (100, 50, 150)

    def test_embedding_response_no_completion_attr(self):
        """Embeddings carry no completion_tokens — must default to 0."""
        assert extract_usage(_embedding_response(42)) == (42, 0, 42)

    def test_missing_usage_returns_zeros(self):
        assert extract_usage(SimpleNamespace()) == (0, 0, 0)
        assert extract_usage(object()) == (0, 0, 0)

    def test_none_usage_returns_zeros(self):
        assert extract_usage(SimpleNamespace(usage=None)) == (0, 0, 0)

    def test_absent_total_reconstructed_from_split(self):
        resp = SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=30, completion_tokens=12)
        )
        assert extract_usage(resp) == (30, 12, 42)

    def test_none_fields_coerced_to_zero(self):
        resp = SimpleNamespace(
            usage=SimpleNamespace(
                prompt_tokens=None, completion_tokens=None, total_tokens=None
            )
        )
        assert extract_usage(resp) == (0, 0, 0)

    def test_zero_total_with_zero_split_stays_zero(self):
        resp = SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=0, completion_tokens=0, total_tokens=0)
        )
        assert extract_usage(resp) == (0, 0, 0)


# ==========================================================
# [5] record_usage — row shape, fail-open, run_id semantics
# ==========================================================

class TestRecordUsage:
    """
    record_usage = log line + DB row (delta 2 of the copy). The autouse
    conftest fixture `llm_cost_insert_calls` captures the insert kwargs.
    """

    def test_row_shape_complete(self, llm_cost_insert_calls):
        total, cost = record_usage(
            "gpt-4o", _chat_response(200, 100),
            site="gold_enrich",
            source_name="newsapi",
            trace_id="evt-row-shape",
            run_id="dayrun-test",
        )
        assert total == 300
        assert cost == pytest.approx(200 / 1000 * 0.0025 + 100 / 1000 * 0.0100)

        assert len(llm_cost_insert_calls) == 1
        row = llm_cost_insert_calls[0]
        assert row["site"] == "gold_enrich"
        assert row["model"] == "gpt-4o"
        assert row["source_name"] == "newsapi"
        assert row["trace_id"] == "evt-row-shape"
        assert row["run_id"] == "dayrun-test"
        assert row["prompt_tokens"] == 200
        assert row["completion_tokens"] == 100
        assert row["total_tokens"] == 300
        assert row["cost_usd"] == pytest.approx(cost)

    def test_embedding_row_has_zero_completion(self, llm_cost_insert_calls):
        record_usage(
            "text-embedding-3-small", _embedding_response(64),
            site="rescue_embed", source_name="newsapi", trace_id="evt-embed",
        )
        row = llm_cost_insert_calls[0]
        assert row["completion_tokens"] == 0
        assert row["prompt_tokens"] == 64
        assert row["cost_usd"] == pytest.approx(64 / 1000 * 0.00002)

    def test_empty_run_id_stored_as_null(self, llm_cost_insert_calls, monkeypatch):
        """settings.RUN_ID default '' must reach the insert as None (SQL NULL)."""
        monkeypatch.setattr("config.settings.RUN_ID", "")
        record_usage(
            "gpt-4o", _chat_response(), site="gold_enrich", source_name="newsapi",
        )
        assert llm_cost_insert_calls[0]["run_id"] is None

    def test_run_id_resolved_from_settings_when_not_passed(
        self, llm_cost_insert_calls, monkeypatch
    ):
        """P3: run_id=None → resolved from settings.RUN_ID at call time."""
        monkeypatch.setattr("config.settings.RUN_ID", "dayrun-from-settings")
        record_usage(
            "gpt-4o", _chat_response(), site="gold_enrich", source_name="newsapi",
        )
        assert llm_cost_insert_calls[0]["run_id"] == "dayrun-from-settings"

    def test_explicit_run_id_wins_over_settings(
        self, llm_cost_insert_calls, monkeypatch
    ):
        monkeypatch.setattr("config.settings.RUN_ID", "dayrun-from-settings")
        record_usage(
            "gpt-4o", _chat_response(),
            site="gold_enrich", source_name="newsapi", run_id="explicit-run",
        )
        assert llm_cost_insert_calls[0]["run_id"] == "explicit-run"

    def test_optional_fields_default_to_none(self, llm_cost_insert_calls, monkeypatch):
        monkeypatch.setattr("config.settings.RUN_ID", "")
        record_usage("gpt-4o", _chat_response(), site="gold_enrich")
        row = llm_cost_insert_calls[0]
        assert row["source_name"] is None
        assert row["trace_id"] is None

    def test_log_line_emitted_and_parseable(self, caplog):
        with caplog.at_level(logging.INFO, logger="utils.llm_cost"):
            record_usage(
                "gpt-3.5-turbo", _chat_response(80, 20),
                site="translate", source_name="telegram",
            )
        usage_lines = [
            r.getMessage() for r in caplog.records if "llm_usage" in r.getMessage()
        ]
        assert len(usage_lines) == 1
        line = usage_lines[0]
        # Identical shape to the agent copy — same parseable key=value fields
        assert "site=translate" in line
        assert "model=gpt-3.5-turbo" in line
        assert "prompt_tokens=80" in line
        assert "completion_tokens=20" in line
        assert "total_tokens=100" in line
        expected_cost = 80 / 1000 * 0.0005 + 20 / 1000 * 0.0015
        assert f"cost_usd={expected_cost:.6f}" in line

    def test_fail_open_on_db_error(self, monkeypatch, caplog):
        """
        A DB insert failure must: log a warning, NOT raise, still return
        (total, cost), and still emit the llm_usage audit line (§2.4).
        """
        import persistence.llm_cost_events as lce

        def _boom(**kwargs):
            raise RuntimeError("connection pool exhausted")

        monkeypatch.setattr(lce, "insert_event", _boom)

        with caplog.at_level(logging.INFO):
            total, cost = record_usage(
                "gpt-4o", _chat_response(100, 50),
                site="gold_enrich", source_name="newsapi", trace_id="evt-failopen",
            )

        assert total == 150
        assert cost > 0.0
        assert "llm_usage" in caplog.text, "Audit log line must survive the DB failure"
        assert "fail-open" in caplog.text, "DB failure must log the fail-open warning"

    def test_fail_open_on_settings_import_error(self, monkeypatch, caplog):
        """
        Even a failure BEFORE the insert (settings resolution) must not
        raise — the entire post-log block is inside the fail-open guard.
        """
        import builtins
        real_import = builtins.__import__

        def _broken_settings(name, *args, **kwargs):
            if name == "config.settings":
                raise ImportError("settings unavailable")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _broken_settings)
        with caplog.at_level(logging.WARNING):
            total, cost = record_usage(
                "gpt-4o", _chat_response(), site="gold_enrich",
            )
        assert total == 150
        assert "fail-open" in caplog.text

    def test_unknown_model_records_zero_cost_row(self, llm_cost_insert_calls):
        """Unknown model still writes a row — $0.00 is the visible pricing-gap signal."""
        record_usage(
            "gpt-9-experimental", _chat_response(10, 10),
            site="gold_enrich", source_name="newsapi",
        )
        assert len(llm_cost_insert_calls) == 1
        assert llm_cost_insert_calls[0]["cost_usd"] == 0.0
