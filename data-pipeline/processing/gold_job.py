"""
Gold Job — Flink Semantic & Structural Enrichment (Section 4.2).

Polymarket vertical slice covers two enrichment paths:

A. Social Pulse Path (Section 4.2A — Consensus Bundling):
   Consumes process.silver.social_pulse
   → GPT-4o Consensus Bundling (4-hour temporal groups)
   → Cognitive Metadata Extraction (impact_level, urgency_level, etc.)
   → text-embedding-3-small vector (1536-dim)
   → Gold Social Pulse record (Section C.6)
   → social_vectors table (via persistence/social_vectors.py)
   → serve.gold.social_pulse (Kafka)

B. Structured Metrics Path (Section 4.2B — Momentum Block):
   Consumes process.silver.structured_metrics
   → Flink keyed state → compute change_24h/7d/30d
   → Whale alert escalation (impact_level: 5 when whale_alert=True, Section B.8)
   → Gold Structured Metric (Section C.2 with populated momentum_block)
   → serve.gold.structured_metrics (Kafka)
   (momentum_vault persistence wired in Task 1.5)

Architecture note — same pattern as silver_job.py:
    All transform functions are pure Python and have no PyFlink dependency.
    Flink wiring is isolated in class definitions and build_pipeline(),
    guarded by PYFLINK_AVAILABLE. Gate 2 tests call transform functions
    directly with mock OpenAI responses from tests/mocks/ (Section 9.3).

OpenAI models used:
    Consensus Bundling:  gpt-4o  (OPENAI_MODEL_NAME from settings, Section 4.2A)
    Vector Embedding:    text-embedding-3-small  (1536-dim, Section 5.2)

References:
    - Section 4.2:  Gold Job specification
    - Section 4.2A: Semantic Enrichment — Consensus Bundling
    - Section 4.2B: Structural Enrichment — Momentum Block
    - Section B.8:  Polymarket parameters (consensus, whale_alert)
    - Section C.4:  Silver-to-Gold transition
    - Section C.6:  Gold Social Pulse Schema
    - Section 5.2:  Vector Intelligence — pgvector with HNSW
    - Section 9.3:  Triple-Gate Test Matrix — Gate 2
"""

from __future__ import annotations

import copy
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from config.kafka_topics import (
    DEAD_LETTER_QUEUE,
    GOLD_SOCIAL_PULSE,
    GOLD_STRUCTURED_METRICS,
    SILVER_SOCIAL_PULSE,
    SILVER_STRUCTURED_METRICS,
)
from utils.validators import validate_gold_social_pulse, validate_silver_social

logger = logging.getLogger(__name__)


# ==========================================================
# PyFlink — optional import (Docker container only, Section 8.2)
# ==========================================================
# See silver_job.py for rationale on this guard pattern.

try:
    from pyflink.common import SimpleStringSchema, WatermarkStrategy
    from pyflink.datastream import StreamExecutionEnvironment
    from pyflink.datastream.connectors.kafka import (
        KafkaOffsetsInitializer,
        KafkaRecordSerializationSchema,
        KafkaSink,
        KafkaSource,
    )
    from pyflink.datastream.functions import KeyedProcessFunction, ProcessFunction
    from pyflink.datastream.output_tag import OutputTag
    PYFLINK_AVAILABLE = True
except ImportError:
    PYFLINK_AVAILABLE = False


# ==========================================================
# Constants
# ==========================================================

# Embedding model for Gold vector generation (Section 5.2)
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM   = 1536

# Whale alert impact override threshold (Section B.8)
WHALE_ALERT_IMPACT_LEVEL = 5

# Max price-history entries retained per asset in Flink keyed state.
# 105,120 = 365 days × 288 five-minute samples; capped at 10,000
# most-recent to bound per-key state size.
_MAX_HISTORY = 10_000

# OutputTag constant — side-routes failed records to DLQ inside Flink
DLQ_TAG = "dlq"

# System prompt for GPT-4o Consensus Bundling (Section 4.2A).
# Instructs the model to respond ONLY with JSON conforming to our schema
# so parse_openai_consensus_response() can deserialise it without fallback logic.
_CONSENSUS_SYSTEM_PROMPT = """\
You are a financial and geopolitical intelligence analyst. \
Given a prediction market question and a batch of community comments, \
synthesize a concise consensus summary.

Respond ONLY with valid JSON matching this exact schema:
{
  "executive_summary":    "<2-3 sentence synthesis of community consensus>",
  "key_findings":         ["<finding 1>", "<finding 2>", "<finding 3>"],
  "impact_level":         <integer 1-5, geopolitical/market significance>,
  "urgency_level":        <integer 1-5, time-sensitivity of the signal>,
  "reliability_score":    <float 0.0-1.0, source credibility estimate>,
  "sentiment_score":      <float -1.0 to 1.0, negative=bearish, positive=bullish>,
  "extracted_entities":   ["<person, institution, or asset name>", ...],
  "topic_classification": "<category: Monetary Policy | Geopolitics | Energy | ...>",
  "fact_check_flag":      <true if any claim requires external verification, else false>,
  "consensus_rating":     <float 0.0-1.0, community probability estimate of YES outcome>,
  "uncertainty_index":    <float 0.0-1.0, degree of community disagreement>
}"""


# ==========================================================
# Consensus Bundling Helpers (Section 4.2A)
# ==========================================================

def build_consensus_prompt(silver_social: dict) -> str:
    """
    Build the GPT-4o user prompt for Polymarket Consensus Bundling.

    Formats the market question and comment texts into a numbered list.
    Numbered presentation helps GPT-4o weigh individual comment
    contributions without being distracted by JSON structure noise.

    Args:
        silver_social: Silver social record (Section C.3, Polymarket pattern).
                       Expected keys: question, raw_comments (list of dicts
                       with a 'text' key).

    Returns:
        Formatted user prompt string.
    """
    question = silver_social.get("question", "Unknown market question")
    comments = silver_social.get("raw_comments", [])

    comment_lines = "\n".join(
        f"{i + 1}. {c.get('text', '')}"
        for i, c in enumerate(comments)
    )
    return (
        f"Market question: {question}\n\n"
        f"Community comments ({len(comments)} total):\n"
        f"{comment_lines}"
    )


def parse_openai_consensus_response(content: str) -> dict:
    """
    Parse the JSON string returned by GPT-4o into an AI metadata dict.

    GPT-4o with response_format=json_object guarantees valid JSON, but
    some versions wrap the output in markdown code fences (```json...```).
    This function strips those before parsing.

    Args:
        content: Raw string from choices[0].message.content.

    Returns:
        Dict with AI metadata keys (executive_summary, impact_level, etc.).

    Raises:
        ValueError: If content cannot be parsed as valid JSON.
    """
    stripped = content.strip()
    if stripped.startswith("```"):
        # Strip opening fence and optional language tag
        stripped = stripped.split("```", 2)[1]
        if stripped.startswith("json"):
            stripped = stripped[4:]
        # Strip closing fence
        stripped = stripped.rsplit("```", 1)[0].strip()

    try:
        return json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"[gold_job] GPT-4o response is not valid JSON: {exc}\n"
            f"Content (first 200 chars): {content[:200]}"
        ) from exc


def call_openai_consensus(silver_social: dict, client: Any) -> dict:
    """
    Call GPT-4o to produce a Consensus Bundle summary (Section 4.2A).

    Uses response_format=json_object to guarantee parseable output.
    Temperature 0.3: balances determinism with natural language quality.
    max_tokens 1024: sufficient for the 11-field JSON schema; avoids
    over-generation that could corrupt the JSON structure.

    Args:
        silver_social: Silver social record (market context + comments).
        client:        openai.OpenAI instance (injected for testability).

    Returns:
        AI metadata dict parsed from GPT-4o JSON response.

    Raises:
        ValueError: If the OpenAI response cannot be parsed as JSON.
    """
    from config.settings import OPENAI_MODEL_NAME

    prompt = build_consensus_prompt(silver_social)
    response = client.chat.completions.create(
        model=OPENAI_MODEL_NAME,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": _CONSENSUS_SYSTEM_PROMPT},
            {"role": "user",   "content": prompt},
        ],
        temperature=0.3,
        max_tokens=1024,
    )
    return parse_openai_consensus_response(response.choices[0].message.content)


def call_openai_embedding(text: str, client: Any) -> list[float]:
    """
    Call text-embedding-3-small to produce a 1536-dim vector (Section 5.2).

    The input text is the GPT-4o executive_summary from Consensus Bundling:
    embedding the summary rather than raw comments means the HNSW index
    encodes the synthesised consensus position, not conversational noise.

    Args:
        text:   The text to embed (typically the consensus executive_summary).
        client: openai.OpenAI instance (injected for testability).

    Returns:
        list[float] of length EMBEDDING_DIM (1536).
    """
    response = client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=text,
    )
    return response.data[0].embedding


# ==========================================================
# Gold Social Pulse Builder (Section C.6)
# ==========================================================

def build_gold_social_pulse(
    silver_social: dict,
    ai_meta:       dict,
    embedding:     list[float],
) -> dict:
    """
    Assemble the Gold Social Pulse record (Section C.6) for Polymarket.

    Bridges Silver → Gold:
        canonical_event_id  ← bronze_ref  (traces back to Bronze envelope)
        silver_data_ref     ← social_vault.social_id UUID set by caller after sv_archive()
        embedding           ← GPT-4o executive_summary vectorised

    Why embed the summary, not individual comments: Consensus Bundling
    (Section 4.2A) compresses N comments into one representative vector.
    Per-comment embeddings would cause Vector DB bloat and recall dilution.

    Args:
        silver_social: Silver social record from process.silver.social_pulse.
        ai_meta:       Structured AI metadata from call_openai_consensus().
        embedding:     1536-dim float list from call_openai_embedding().

    Returns:
        Gold Social Pulse dict conforming to Section C.6.
    """
    market_id    = silver_social.get("market_id", "")
    question     = silver_social.get("question", "")
    raw_comments = silver_social.get("raw_comments", [])
    ingested_at  = silver_social.get("ingested_at", datetime.now(timezone.utc).isoformat())
    bronze_ref   = silver_social.get("bronze_ref", "")

    return {
        "metadata": {
            "signal_id":          str(uuid.uuid4()),
            "canonical_event_id": bronze_ref,
            "source_platform":    "polymarket",
            "published_at":       ingested_at,
            "silver_data_ref":    bronze_ref,   # Bronze envelope event_id; social_vault.social_id wiring deferred to Sprint 2
            "raw_data_ref":       bronze_ref,
        },
        "content_vitals": {
            "title":               question,
            "url":                 f"https://polymarket.com/event/{market_id}",
            "description_snippet": ai_meta.get("executive_summary", "")[:300],
        },
        "enrichment_ai": {
            "executive_summary":    ai_meta.get("executive_summary", ""),
            "key_findings":         ai_meta.get("key_findings", []),
            # Explicit int/float casts: the validator requires exact types.
            # GPT-4o may return integers for score fields depending on the
            # random seed — cast defensively rather than relying on model
            # type consistency.
            "impact_level":         int(ai_meta.get("impact_level", 3)),
            "urgency_level":        int(ai_meta.get("urgency_level", 2)),
            "reliability_score":    float(ai_meta.get("reliability_score", 0.5)),
            "sentiment_score":      float(ai_meta.get("sentiment_score", 0.0)),
            "extracted_entities":   ai_meta.get("extracted_entities", []),
            "topic_classification": ai_meta.get("topic_classification", ""),
            "fact_check_flag":      bool(ai_meta.get("fact_check_flag", False)),
        },
        "social_context": {
            "community_name":  "Polymarket",
            "author_handle":   None,
            # Platform-level default: no per-author reputation available for
            # consensus bundles (multiple authors collapsed into one record).
            "author_reputation":        0.8,
            # Engagement = comment volume fed into this consensus bundle
            "primary_engagement_score": len(raw_comments),
        },
        "platform_logic": {
            "entry_type":               "market_consensus",
            "aggregation_window_hours": 4,
            "comment_volume_analyzed":  len(raw_comments),
            "consensus_rating":         float(ai_meta.get("consensus_rating", 0.5)),
            "market_id_ref":            market_id,
            # Section B.8: drill-down flag — raw comments retrievable via Social Vault
            "has_raw_source":           True,
            "uncertainty_index":        float(ai_meta.get("uncertainty_index", 0.0)),
            # Whale alert not applicable to comment bundles; it is tracked in
            # the price_update (structured_metrics) path instead.
            "whale_alert":              False,
        },
        "embedding": embedding,
    }


# ==========================================================
# Momentum Block (Section 4.2B)
# ==========================================================

def compute_momentum_block(
    current_value: float,
    price_history: list[tuple[str, float]],
    _now: Optional[datetime] = None,
) -> dict:
    """
    Compute deterministic momentum deltas (change_24h, change_7d, change_30d).

    Why computed at Gold, not Silver: Silver records stub all deltas to 0.0
    because computing them requires the asset's historical observations.
    The Flink KeyedProcessFunction maintains per-asset history in ValueState
    so deltas can be computed without a DB round-trip on every update.

    Delta formula: (current - reference) / reference
    Reference selection: the most recent observation recorded AT OR BEFORE
    the target lookback window (e.g., current_time - 24h). If no observation
    falls in that window, the delta is 0.0.

    Args:
        current_value: The current asset price / metric value.
        price_history: Chronologically sorted list of (timestamp_utc_iso, value)
                       tuples for this asset. The current observation is NOT
                       included — it will be appended by the Flink operator
                       after this function returns.
        _now:          Override the "current" time. Injected by Gate 2 tests
                       to produce deterministic delta assertions without
                       relying on wall-clock time (Section 9.3).

    Returns:
        momentum_block dict: change_24h, change_7d, change_30d (fractional),
        is_new_market (True when no prior history exists).
    """
    now = _now or datetime.now(timezone.utc)

    def find_closest_before(hours: int) -> Optional[float]:
        """Return the most recent value recorded at or before now - hours."""
        target = now - timedelta(hours=hours)
        # Walk chronologically: last qualifying entry is the closest to target
        candidates: list[float] = []
        for ts_str, val in price_history:
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            except ValueError:
                continue
            if ts <= target:
                candidates.append(val)
        return candidates[-1] if candidates else None

    def pct_change(reference: Optional[float]) -> float:
        if reference is None or reference == 0.0:
            return 0.0
        return round((current_value - reference) / reference, 6)

    return {
        "change_24h":    pct_change(find_closest_before(24)),
        "change_7d":     pct_change(find_closest_before(24 * 7)),
        "change_30d":    pct_change(find_closest_before(24 * 30)),
        "is_new_market": len(price_history) == 0,
    }


def build_gold_structured_metric(
    silver_metric: dict,
    momentum:      dict,
) -> dict:
    """
    Produce a Gold Structured Metric by merging Silver with computed momentum.

    Replaces the stubbed momentum_block (all 0.0) from Silver with the
    values computed by compute_momentum_block() from Flink keyed state.

    Whale alert handling (Section B.8): when metadata_extension.whale_alert
    is True, the top-level whale_alert key is set on the Gold record so
    downstream consumers can filter on it without inspecting JSONB.

    Args:
        silver_metric: Silver Structured Metric (Section C.2) with stubs.
        momentum:      Computed momentum_block from compute_momentum_block().

    Returns:
        Gold Structured Metric — deep copy with layer=Gold, populated
        momentum_block, and top-level whale_alert flag.
    """
    result = copy.deepcopy(silver_metric)
    result["layer"]          = "Gold"
    result["momentum_block"] = momentum

    whale = result.get("metadata_extension", {}).get("whale_alert", False)
    result["whale_alert"] = bool(whale)

    return result


# ==========================================================
# Dispatch Functions — full Gold enrichment path per message type
# ==========================================================

def process_social_pulse_message(
    silver_social: dict,
    openai_client: Any = None,
) -> tuple[Optional[str], Optional[dict]]:
    """
    Full Gold enrichment path for a Polymarket Silver social pulse record.

    Steps (Section 4.2A):
        1. Guard: empty comment batch → (None, None)
        2. Validate Silver social schema → DLQ on failure
        3. Lazy-create OpenAI client if not injected
        4. GPT-4o consensus bundling → DLQ on API error
        5. text-embedding-3-small embedding → DLQ on API error
        6. Build Gold Social Pulse (Section C.6)
        7. Validate Gold Social Pulse schema → DLQ on failure
        8. Return (GOLD_SOCIAL_PULSE, gold_record)

    Note on DB persistence: the Flink PolymarketGoldSocialFunction calls
    social_vectors.insert(gold_record) AFTER this function returns. Keeping
    persistence out of this function ensures it is testable at Gate 2
    without a live database (Section 9.3).

    Args:
        silver_social:  Silver social record from process.silver.social_pulse.
        openai_client:  openai.OpenAI instance. If None, created from
                        OPENAI_API_KEY in config/settings.py.

    Returns:
        (GOLD_SOCIAL_PULSE, gold_record) on success.
        (DEAD_LETTER_QUEUE, dlq_record) on any validation or API failure.
        (None, None) if comment batch is empty (intentional skip, not an error).
    """
    if not silver_social.get("raw_comments"):
        logger.debug(
            "[gold/polymarket] Empty comment batch for market_id=%s — skipping",
            silver_social.get("market_id", "unknown"),
        )
        return None, None

    silver_result = validate_silver_social(silver_social)
    if not silver_result.is_valid:
        logger.warning(
            "[gold/polymarket] Silver social validation failed: %s",
            silver_result.errors,
        )
        return DEAD_LETTER_QUEUE, _dlq_record(
            silver_social, silver_result.errors, "silver_social_validation"
        )

    if openai_client is None:
        try:
            from openai import OpenAI
            from config.settings import OPENAI_API_KEY
            openai_client = OpenAI(api_key=OPENAI_API_KEY)
        except ImportError:
            logger.error("[gold/polymarket] openai package not installed")
            return DEAD_LETTER_QUEUE, _dlq_record(
                silver_social, ["openai package not available"], "openai_init"
            )

    try:
        ai_meta = call_openai_consensus(silver_social, openai_client)
    except Exception as exc:
        logger.error("[gold/polymarket] OpenAI consensus call failed: %s", exc)
        return DEAD_LETTER_QUEUE, _dlq_record(
            silver_social, [f"OpenAI consensus error: {exc}"], "openai_consensus"
        )

    try:
        embedding = call_openai_embedding(
            ai_meta.get("executive_summary") or silver_social.get("question", ""),
            openai_client,
        )
    except Exception as exc:
        logger.error("[gold/polymarket] OpenAI embedding call failed: %s", exc)
        return DEAD_LETTER_QUEUE, _dlq_record(
            silver_social, [f"OpenAI embedding error: {exc}"], "openai_embedding"
        )

    gold_record = build_gold_social_pulse(silver_social, ai_meta, embedding)

    gold_result = validate_gold_social_pulse(gold_record)
    if not gold_result.is_valid:
        logger.error(
            "[gold/polymarket] Gold social pulse validation failed: %s",
            gold_result.errors,
        )
        return DEAD_LETTER_QUEUE, _dlq_record(
            silver_social, gold_result.errors, "gold_social_pulse_validation"
        )

    return GOLD_SOCIAL_PULSE, gold_record


def process_structured_metrics_message(
    silver_metric: dict,
    price_history: list[tuple[str, float]],
) -> tuple[str, dict]:
    """
    Full Gold enrichment path for a Polymarket Silver structured metric record.

    Computes momentum deltas from Flink keyed state and builds the Gold
    Structured Metric. Whale alert escalation is structural (non-AI),
    applied here as part of Section 4.2B enrichment.

    Note on persistence: the Flink PolymarketGoldMetricsFunction is
    responsible for (a) calling this function, (b) appending the current
    observation to ValueState, (c) persisting to momentum_vault (Task 1.5),
    and (d) emitting to serve.gold.structured_metrics.

    Args:
        silver_metric: Silver Structured Metric (Section C.2).
        price_history: Per-asset observation history from Flink ValueState.
                       Pass [] on first observation for an asset.

    Returns:
        (GOLD_STRUCTURED_METRICS, gold_metric).
    """
    current_value = float(
        silver_metric.get("data_point", {}).get("current_value", 0.0)
    )
    momentum    = compute_momentum_block(current_value, price_history)
    gold_record = build_gold_structured_metric(silver_metric, momentum)
    return GOLD_STRUCTURED_METRICS, gold_record


# ==========================================================
# FRED Gold Branch — Automation Triggers & Dispatch (Section 4.2B / B.3)
# ==========================================================

# Series IDs whose 24h price variance triggers an immediate grounding flag.
# Commodity prices with fast-moving markets (Section B.3).
_FRED_PRICE_SERIES = frozenset({"DCOILWTICO", "GASREGW", "DHHNGSP"})

# Flink automation trigger thresholds (Section B.3)
_T10Y2Y_INVERSION_THRESHOLD = 0.0    # T10Y2Y < 0  → yield curve inversion
_VIXCLS_FEAR_THRESHOLD      = 30.0   # VIXCLS > 30 → extreme uncertainty
_PRICE_VARIANCE_THRESHOLD   = 0.05   # 5% in 24h   → price variance alert


def apply_fred_automation_triggers(
    gold_record: dict,
    momentum:    dict,
) -> dict:
    """
    Apply FRED-specific automation triggers to an already-built Gold record.

    Evaluates the three trigger rules from Section B.3 and enriches
    metadata_extension with the results. This is deterministic, non-AI
    logic — no OpenAI calls, no external I/O (Section 4.2B).

    Trigger rules (Section B.3):
        T10Y2Y < 0           → anomaly_flag "Market_Anomaly",      impact_level 5
                               (Yield Curve Inversion — historically precedes recessions)
        VIXCLS > 30          → anomaly_flag "Extreme_Uncertainty",  impact_level 5
                               (Fear Index spike — extreme market uncertainty)
        DCOILWTICO / GASREGW / DHHNGSP
          |change_24h| > 5%  → anomaly_flag "Price_Variance_Alert"
                               (Commodity price swing — potential supply/geopolitical shock)

    Why enrich metadata_extension rather than a top-level field: the Gold
    Structured Metric schema (Section C.2) uses metadata_extension as the
    JSONB column for source-specific fields. Adding trigger results here keeps
    the schema stable — the RAG agent queries metadata_extension.impact_level
    for fast severity filtering without altering the top-level schema contract.

    Why impact_level defaults to release_priority: release_priority (1–5) is
    the static editorial assessment of a series' importance. impact_level is
    the dynamic runtime assessment of this specific observation. When no trigger
    fires, the two coincide — keeping consistent semantics for the RAG agent.

    Args:
        gold_record: Gold Structured Metric produced by build_gold_structured_metric().
                     Modified copy is returned — original is never mutated.
        momentum:    The computed momentum_block dict from compute_momentum_block().
                     Needed to read change_24h for price variance checks.

    Returns:
        Deep-copy of gold_record with metadata_extension enriched with:
            anomaly_flags  (list[str])  — empty if no triggers fired
            impact_level   (int 1–5)    — elevated to 5 on critical triggers
            trigger_reason (str | None) — human-readable explanation, or None
    """
    series_id = gold_record.get("core_identity", {}).get("external_reference_id", "")
    value     = float(gold_record.get("data_point", {}).get("current_value", 0.0))

    # Default: inherit static release_priority, no alerts
    base_priority  = gold_record.get("metadata_extension", {}).get("release_priority", 3)
    anomaly_flags: list[str] = []
    impact_level   = base_priority
    trigger_reason: Optional[str] = None

    # --- Trigger 1: T10Y2Y yield curve inversion (Section B.3) ---
    if series_id == "T10Y2Y" and value < _T10Y2Y_INVERSION_THRESHOLD:
        anomaly_flags.append("Market_Anomaly")
        impact_level   = 5
        trigger_reason = (
            f"Yield curve inverted: T10Y2Y={value:.4f} < 0. "
            "Historical recession leading indicator."
        )

    # --- Trigger 2: VIXCLS extreme fear (Section B.3) ---
    elif series_id == "VIXCLS" and value > _VIXCLS_FEAR_THRESHOLD:
        anomaly_flags.append("Extreme_Uncertainty")
        impact_level   = 5
        trigger_reason = (
            f"VIX fear index elevated: VIXCLS={value:.2f} > {_VIXCLS_FEAR_THRESHOLD}. "
            "Extreme market uncertainty detected."
        )

    # --- Trigger 3: Commodity price variance > 5% in 24h (Section B.3) ---
    elif series_id in _FRED_PRICE_SERIES:
        change_24h_abs = abs(momentum.get("change_24h", 0.0))
        if change_24h_abs > _PRICE_VARIANCE_THRESHOLD:
            anomaly_flags.append("Price_Variance_Alert")
            trigger_reason = (
                f"{series_id} 24h variance={change_24h_abs * 100:.1f}% "
                f"exceeds {_PRICE_VARIANCE_THRESHOLD * 100:.0f}% threshold. "
                "Flag for immediate grounding."
            )

    result = copy.deepcopy(gold_record)
    result["metadata_extension"]["anomaly_flags"]  = anomaly_flags
    result["metadata_extension"]["impact_level"]   = impact_level
    result["metadata_extension"]["trigger_reason"] = trigger_reason

    return result


def process_fred_gold_message(
    silver_metric: dict,
    price_history: list[tuple[str, float]],
    _now: Optional[datetime] = None,
) -> tuple[str, dict]:
    """
    Full Gold enrichment path for a FRED Silver structured metric.

    Why FRED needs no OpenAI: FRED data is entirely numerical. The Gold
    enrichment is deterministic math (momentum deltas) plus rule-based
    trigger evaluation (Section B.3). Zero token cost, zero latency
    overhead — this path is always faster than the Social Pulse path.

    Steps (Section 4.2B + B.3):
        1. Compute momentum block from price_history via compute_momentum_block()
        2. Build Gold record via build_gold_structured_metric() — promotes layer to Gold,
           replaces stub momentum_block with computed values
        3. Apply FRED automation triggers via apply_fred_automation_triggers() —
           adds anomaly_flags, impact_level, trigger_reason to metadata_extension

    Note on persistence: the Flink FREDGoldMetricsFunction (in the PYFLINK_AVAILABLE
    block) calls momentum_vault.insert() AFTER this function returns. Keeping
    persistence out of this function ensures it is testable at Gate 2 without
    a live database (Section 9.3).

    Args:
        silver_metric: Silver Structured Metric from process.silver.structured_metrics,
                       produced by silver_job.map_fred_observation_to_silver().
        price_history: Per-series observation history from Flink ValueState.
                       List of (timestamp_utc_iso, value) tuples in chronological order.
                       Pass [] on first observation for a series.
        _now:          Override current time for deterministic test assertions
                       (injected by Gate 2 tests — same pattern as compute_momentum_block).

    Returns:
        (GOLD_STRUCTURED_METRICS, gold_metric) — always succeeds for valid Silver input.
        FRED Gold enrichment has no failure paths; a structurally valid Silver record
        always produces a valid Gold record (all operations are deterministic math).
    """
    current_value = float(
        silver_metric.get("data_point", {}).get("current_value", 0.0)
    )
    momentum    = compute_momentum_block(current_value, price_history, _now=_now)
    gold_record = build_gold_structured_metric(silver_metric, momentum)
    gold_record = apply_fred_automation_triggers(gold_record, momentum)
    return GOLD_STRUCTURED_METRICS, gold_record


def _dlq_record(record: dict, errors: list[str], failed_stage: str) -> dict:
    """
    Build a dead-letter-queue record for Gold Job failures (Section 3.5).

    Consistent DLQ envelope regardless of which stage fails, matching
    the format produced by silver_job._dlq_record() so DLQ consumers
    have a uniform structure to parse.
    """
    return {
        "dlq_id":            str(uuid.uuid4()),
        "failed_at":         datetime.now(timezone.utc).isoformat(),
        "failed_layer":      "Gold",
        "failed_stage":      failed_stage,
        "source_topic":      SILVER_SOCIAL_PULSE,
        "validation_errors": errors,
        "original_message":  record,
    }


# ==========================================================
# Flink Pipeline — requires PyFlink (Docker container only)
# ==========================================================

if PYFLINK_AVAILABLE:

    class PolymarketGoldSocialFunction(ProcessFunction):
        """
        Flink ProcessFunction — Social Pulse Gold enrichment.

        Wraps process_social_pulse_message() and handles:
            - Lazy OpenAI client creation (once per task slot in open())
            - persistence/social_vectors.insert() call after enrichment
            - DLQ side output on any failure

        Why client is created in open() not process_element():
        Establishing an HTTP connection on every message would dominate
        processing time. open() is called once per task slot lifetime.
        """

        _dlq_tag = OutputTag(DLQ_TAG)

        def open(self, runtime_context):
            from openai import OpenAI
            from config.settings import OPENAI_API_KEY
            self._openai_client = OpenAI(api_key=OPENAI_API_KEY)

        def process_element(self, value: str, ctx: ProcessFunction.Context):
            from persistence.social_vectors import insert as sv_insert
            from persistence.social_vault import (
                insert as sv_archive,
                exists_by_content_hash,
            )

            try:
                silver_social = json.loads(value)
            except (json.JSONDecodeError, TypeError) as exc:
                dlq = _dlq_record({}, [f"JSON parse error: {exc}"], "deserialise")
                ctx.output(self._dlq_tag, json.dumps(dlq))
                return

            # Archive raw Silver record to social_vault before Gold enrichment.
            # Dedup guard: skip re-archiving batches already stored (Section 4.1C).
            try:
                content_hash = silver_social.get("content_hash", "")
                if not (content_hash and exists_by_content_hash(content_hash)):
                    sv_archive(silver_social)
            except Exception as exc:
                # Non-fatal: archive failure must not block Gold enrichment
                logger.warning("[gold/flink] social_vault.insert failed: %s", exc)

            topic, record = process_social_pulse_message(
                silver_social,
                openai_client=self._openai_client,
            )

            if topic is None:
                return  # Intentional empty-batch skip

            if topic == DEAD_LETTER_QUEUE:
                ctx.output(self._dlq_tag, json.dumps(record))
                return

            # Persist Gold vector to PostgreSQL social_vectors (Section 5.2)
            try:
                sv_insert(record)
            except Exception as exc:
                logger.error("[gold/flink] social_vectors.insert failed: %s", exc)
                ctx.output(self._dlq_tag, json.dumps(
                    _dlq_record(
                        record, [f"DB insert error: {exc}"], "social_vectors_insert"
                    )
                ))
                return

            yield json.dumps(record)

    class PolymarketGoldMetricsFunction(KeyedProcessFunction):
        """
        Flink KeyedProcessFunction — Structured Metrics Gold enrichment.

        Keyed on canonical_event_id so each asset accumulates its own
        independent price history in ValueState (Section 4.2B).

        State schema: JSON-serialized list of [timestamp_utc_iso, value]
        pairs, kept in chronological order. Capped at _MAX_HISTORY entries
        (≈10,000) to bound per-key state size while covering 30d+ of history
        at standard 5-minute polling frequency.

        Why ValueState[str] (JSON) over ListState[Tuple]:
        PyFlink's TUPLE type requires type info for all constituent parts.
        JSON strings avoid that complexity at negligible serialization cost
        for these small payloads.
        """

        _dlq_tag = OutputTag(DLQ_TAG)

        def open(self, runtime_context):
            from pyflink.datastream.state import ValueStateDescriptor
            from pyflink.common.typeinfo import Types
            self._history_state = runtime_context.get_state(
                ValueStateDescriptor("price_history", Types.STRING())
            )

        def _load_history(self) -> list[tuple[str, float]]:
            raw = self._history_state.value()
            if raw is None:
                return []
            return [tuple(entry) for entry in json.loads(raw)]

        def _save_history(self, history: list[tuple[str, float]]) -> None:
            self._history_state.update(json.dumps(history))

        def process_element(self, value: str, ctx: KeyedProcessFunction.Context):
            try:
                silver_metric = json.loads(value)
            except (json.JSONDecodeError, TypeError) as exc:
                dlq = _dlq_record({}, [f"JSON parse error: {exc}"], "deserialise")
                ctx.output(self._dlq_tag, json.dumps(dlq))
                return

            history = self._load_history()
            _, gold_record = process_structured_metrics_message(silver_metric, history)

            # Persist Gold metric to momentum_vault TimescaleDB hypertable (Section 5.3).
            # ON CONFLICT (metric_id, timestamp_utc) DO NOTHING ensures Flink
            # exactly-once re-deliveries never create duplicate rows.
            try:
                from persistence.momentum_vault import insert as mv_insert
                mv_insert(gold_record)
            except Exception as exc:
                logger.error("[gold/flink] momentum_vault.insert failed: %s", exc)
                ctx.output(self._dlq_tag, json.dumps(
                    _dlq_record(
                        silver_metric,
                        [f"momentum_vault insert error: {exc}"],
                        "momentum_vault_insert",
                    )
                ))
                return

            # Append current observation, cap history size
            current_ts    = silver_metric.get("data_point", {}).get("timestamp_utc", "")
            current_value = float(
                silver_metric.get("data_point", {}).get("current_value", 0.0)
            )
            history.append((current_ts, current_value))
            if len(history) > _MAX_HISTORY:
                history = history[-_MAX_HISTORY:]
            self._save_history(history)

            yield json.dumps(gold_record)

    def _extract_metrics_key(msg: str) -> str:
        """Extract canonical_event_id from a Silver Structured Metric JSON string."""
        try:
            return json.loads(msg).get("core_identity", {}).get("canonical_event_id", "")
        except Exception:
            return ""

    def build_pipeline(env: "StreamExecutionEnvironment") -> None:
        """
        Wire the Flink pipeline for the Polymarket Gold branch.

        Two independent sub-pipelines share the same environment:
            A. Social Pulse:  SILVER_SOCIAL_PULSE   → PolymarketGoldSocialFunction
                              → DLQ side output  + GOLD_SOCIAL_PULSE main output
            B. Metrics:       SILVER_STRUCTURED_METRICS → key_by(canonical_event_id)
                              → PolymarketGoldMetricsFunction → GOLD_STRUCTURED_METRICS

        Args:
            env: StreamExecutionEnvironment configured by the caller.
        """
        from config.settings import KAFKA_BOOTSTRAP_SERVERS

        def _kafka_source(topic: str, group_id: str) -> "KafkaSource":
            return (
                KafkaSource.builder()
                .set_bootstrap_servers(KAFKA_BOOTSTRAP_SERVERS)
                .set_topics(topic)
                .set_group_id(group_id)
                .set_starting_offsets(KafkaOffsetsInitializer.earliest())
                .set_value_only_deserializer(SimpleStringSchema())
                .build()
            )

        def _kafka_sink(topic: str) -> "KafkaSink":
            return (
                KafkaSink.builder()
                .set_bootstrap_servers(KAFKA_BOOTSTRAP_SERVERS)
                .set_record_serializer(
                    KafkaRecordSerializationSchema.builder()
                    .set_topic(topic)
                    .set_value_serialization_schema(SimpleStringSchema())
                    .build()
                )
                .build()
            )

        dlq_tag = OutputTag(DLQ_TAG)

        # --- A. Social Pulse pipeline ---
        social_stream = env.from_source(
            _kafka_source(SILVER_SOCIAL_PULSE, "flink-gold-social-polymarket"),
            WatermarkStrategy.no_watermarks(),
            "silver-social-pulse-source",
        )
        social_processed = social_stream.process(PolymarketGoldSocialFunction())
        social_processed.get_side_output(dlq_tag).sink_to(_kafka_sink(DEAD_LETTER_QUEUE))
        social_processed.sink_to(_kafka_sink(GOLD_SOCIAL_PULSE))

        # --- B. Structured Metrics pipeline ---
        metrics_stream = env.from_source(
            _kafka_source(SILVER_STRUCTURED_METRICS, "flink-gold-metrics-polymarket"),
            WatermarkStrategy.no_watermarks(),
            "silver-structured-metrics-source",
        )
        (
            metrics_stream
            .key_by(_extract_metrics_key)
            .process(PolymarketGoldMetricsFunction())
            .sink_to(_kafka_sink(GOLD_STRUCTURED_METRICS))
        )


def main() -> None:
    """
    Entry point for Flink job submission inside the Docker container.

    Submit via Flink CLI:
        flink run -py processing/gold_job.py

    Requires PyFlink in the apache/flink:1.19-java11 image (Section 8.2).
    Running locally without PyFlink will print an informative error.
    """
    if not PYFLINK_AVAILABLE:
        print(
            "[gold_job] PyFlink is not installed in this environment.\n"
            "This job must be submitted to the Flink JobManager container.\n"
            "See docker-compose.yml — flink-jobmanager service (Section 8.2)."
        )
        return

    env = StreamExecutionEnvironment.get_execution_environment()
    env.set_parallelism(2)
    build_pipeline(env)
    env.execute("anizai-gold-polymarket")


if __name__ == "__main__":
    main()
