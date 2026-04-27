"""
Gate 2 — Logic Gate: Telegram Gold Processing Validation (Section 4.2A).

Validates that the Gold Job's Telegram branch enriches and routes correctly
using mock OpenAI responses — no live OpenAI API, no Flink, no Kafka.

What Gate 2 covers (Section 9.3 Gate 2):
    [1]  High-signal message routes to GOLD_GLOBAL_NEWS
    [2]  Low-signal (is_high_signal=False) message → (None, None) — intentional skip
    [3]  Low-signal: OpenAI never called (zero token cost, Section 4.1A)
    [4]  Invalid Silver document → DEAD_LETTER_QUEUE
    [5]  OpenAI cognitive metadata API error → DEAD_LETTER_QUEUE
    [6]  OpenAI embedding API error → DEAD_LETTER_QUEUE
    [7]  Gold output passes validate_gold_signal()
    [8]  signal_id is a non-empty UUID string
    [9]  metadata.canonical_event_id == silver_doc.canonical_event_id
    [10] metadata.source_platform == "telegram"
    [11] metadata.silver_data_ref == silver_doc.doc_id
    [12] metadata.raw_data_ref == silver_doc.bronze_ref
    [13] enrichment_ai.executive_summary matches the mock response
    [14] enrichment_ai.impact_level is int in [1, 5]
    [15] enrichment_ai.urgency_level is int in [1, 5]
    [16] enrichment_ai.reliability_score is float in [0.0, 1.0]
    [17] enrichment_ai.sentiment_score is float in [-1.0, 1.0]
    [18] enrichment_ai.extracted_entities is a list
    [19] enrichment_ai.topic_classification is a non-empty string
    [20] enrichment_ai.key_findings is a list
    [21] enrichment_ai.fact_check_flag is a bool
    [22] domain_context has exactly 5 keys (Direct Message extension, Section C.5)
    [23] domain_context.channel_username matches silver_doc.channel_username
    [24] domain_context.is_forwarded matches silver_doc.is_forwarded
    [25] domain_context.forwarded_from matches silver_doc.forwarded_from
    [26] domain_context.views matches silver_doc.views
    [27] domain_context.extracted_links matches silver_doc.extracted_links
    [28] domain_context has NO is_breaking field (NewsAPI Tactical only)
    [29] domain_context has NO sniper_keywords field (NewsAPI Tactical only)
    [30] domain_context has NO authors field (ArXiv Academic only)
    [31] No Impact Boost applied — impact_level == mock AI value (Section A.1)
    [32] Even with geopolitics/energy content, no impact_level boost
    [33] build_telegram_gold_global_signal() builder has no boost logic
    [34] embedding is a list[float] of length 1536
    [35] content_vitals.url matches silver_doc.original_url
    [36] description_snippet is first 300 chars of inverted_pyramid_lead
    [37] signal_id is unique per call
    [38] DLQ source_topic == SILVER_GLOBAL_NEWS (not SILVER_SOCIAL_PULSE)
    [39] DLQ record has non-empty validation_errors

Key Telegram departures from ArXiv Gold Gate 2 (Section A.1):
    - build_telegram_gold_global_signal() used (not build_arxiv_gold_global_signal())
    - Direct Message domain_context: channel_username, is_forwarded, forwarded_from,
      views, extracted_links (5 keys)
    - No apply_impact_boost() call — ever (Section A.1)
    - No is_breaking, no sniper_keywords (NewsAPI Tactical), no authors (ArXiv Academic)
    - Bronze http_status_code=0, request_duration_ms=0 (MTProto streaming, no HTTP poll)
    - DLQ source_topic = SILVER_GLOBAL_NEWS (same as ArXiv/NewsAPI — all arrive from global_news)

References:
    - Section 4.2A:  Gold Job — Cognitive Metadata Extraction + embeddings
    - Section 4.4:   Mock-Driven Development — tests/mocks/ payloads
    - Section 9.3:   Triple-Gate Test Matrix — Gate 2
    - Section C.5:   Gold Global Signal Schema — Telegram Direct Message domain_context
    - Section A.1:   Telegram parameters — no Impact Boost, 7 vetted channels
    - Section 3.5:   Dead-Letter Queue — never silently drop
"""

import json
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from config.kafka_topics import (
    DEAD_LETTER_QUEUE,
    GOLD_GLOBAL_NEWS,
    SILVER_GLOBAL_NEWS,
    SILVER_SOCIAL_PULSE,
)
from processing.gold_job import (
    build_telegram_gold_global_signal,
    process_telegram_gold_message,
)
from processing.silver_job import process_telegram_message
from utils.kafka_utils import build_bronze_message
from utils.validators import validate_gold_signal

# ==========================================================
# Paths & Constants
# ==========================================================

MOCKS_DIR      = Path(__file__).parent.parent / "mocks"
_EMBEDDING_DIM = 1536


# ==========================================================
# Helpers
# ==========================================================

def _make_mock_embedding() -> list[float]:
    """Return a plausible 1536-dim embedding (all 0.1 — structurally valid)."""
    return [0.1] * _EMBEDDING_DIM


def _make_openai_client(ai_meta: dict) -> MagicMock:
    """
    Build a MagicMock OpenAI client that returns ai_meta as GPT-4o response
    and a 1536-dim float list for embeddings.

    Injected via the openai_client parameter of process_telegram_gold_message()
    to keep tests fully self-contained with no network I/O (Section 4.4).
    """
    mock_client = MagicMock()

    mock_response = MagicMock()
    mock_response.choices[0].message.content = json.dumps(ai_meta)
    mock_client.chat.completions.create.return_value = mock_response

    mock_embed_response = MagicMock()
    mock_embed_response.data[0].embedding = _make_mock_embedding()
    mock_client.embeddings.create.return_value = mock_embed_response

    return mock_client


def _build_envelope(raw: dict) -> dict:
    """
    Wrap a raw Telegram payload in a Bronze envelope.

    http_status_code=0 and request_duration_ms=0 because Telegram uses MTProto
    streaming (Telethon events.NewMessage), not an HTTP REST poll (Section A.1,
    telegram_producer.py _emit()).
    """
    return build_bronze_message(
        source_name="telegram",
        source_endpoint="mtproto://telegram",
        raw_payload=raw,
        http_status_code=0,
        request_duration_ms=0,
    )


# ==========================================================
# Fixtures
# ==========================================================

@pytest.fixture(scope="module")
def telegram_raw() -> dict:
    """Load Telegram mock raw_payload (strip _comment key)."""
    with open(MOCKS_DIR / "telegram_bronze_payload.json", encoding="utf-8") as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith("_")}


@pytest.fixture(scope="module")
def mock_ai_meta() -> dict:
    """
    Load the mock GPT-4o Cognitive Metadata response for the Telegram naval incident
    (Section 4.4 — openai_telegram_enrichment.json).

    Used to populate the MagicMock OpenAI client so tests assert against
    realistic AI output: impact_level=4, urgency_level=5, reliability_score=0.78,
    sentiment_score=-0.7, fact_check_flag=true.
    """
    with open(MOCKS_DIR / "openai_telegram_enrichment.json", encoding="utf-8") as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith("_")}


@pytest.fixture(scope="module")
def silver_doc_high_signal(telegram_raw) -> dict:
    """
    Silver document for the mock Telegram message, confirmed high-signal.

    The IRGC naval / Strait of Hormuz message must score above DEFAULT_THRESHOLD
    via 'naval', 'strait', 'hormuz', 'oil' keywords in the description slot.
    """
    envelope = _build_envelope(telegram_raw)
    _, record = process_telegram_message(envelope)
    assert record["is_high_signal"] is True, (
        "Test setup error: mock Telegram message must be high-signal. "
        f"Score={record.get('relevance_score')}, keywords={record.get('sniper_keywords')}"
    )
    return record


@pytest.fixture(scope="module")
def mock_client(mock_ai_meta) -> MagicMock:
    """MagicMock OpenAI client pre-loaded with the mock Telegram AI metadata."""
    return _make_openai_client(mock_ai_meta)


@pytest.fixture(scope="module")
def gold_record(silver_doc_high_signal, mock_client) -> dict:
    """
    Run process_telegram_gold_message() with the mock OpenAI client.

    Returns the Gold Global Signal record for structural assertion tests.
    Fails immediately if routing fails — downstream tests depend on a valid Gold record.
    """
    topic, record = process_telegram_gold_message(silver_doc_high_signal, mock_client)
    assert topic == GOLD_GLOBAL_NEWS, (
        f"Expected GOLD_GLOBAL_NEWS but got '{topic}'. Record: {record}"
    )
    return record


# ==========================================================
# Gate 2 — Routing Tests (checks [1]–[6])
# ==========================================================

class TestTelegramGoldRouting:
    """
    Verifies process_telegram_gold_message() routes to the correct target.
    """

    def test_high_signal_routes_to_gold_global_news(
        self, silver_doc_high_signal, mock_client
    ):
        """High-signal message must route to GOLD_GLOBAL_NEWS (check [1])."""
        topic, record = process_telegram_gold_message(silver_doc_high_signal, mock_client)
        assert topic == GOLD_GLOBAL_NEWS
        assert record is not None

    def test_low_signal_returns_none_none(self, telegram_raw):
        """
        A message with is_high_signal=False must return (None, None) (check [2]).

        Low-signal messages skip Gold enrichment entirely — zero OpenAI spend.
        They are stored in the Knowledge Vault but without embeddings (Section 4.1A).
        """
        raw = dict(telegram_raw)
        raw["message_text"] = "The weekly schedule for the cultural centre has been posted."
        raw["message_url"]  = "https://t.me/clashreport/11111"
        envelope = _build_envelope(raw)
        _, silver = process_telegram_message(envelope)
        assert silver["is_high_signal"] is False  # confirms setup

        topic, record = process_telegram_gold_message(silver, _make_openai_client({}))
        assert topic is None
        assert record is None

    def test_low_signal_openai_never_called(self, telegram_raw):
        """
        OpenAI must never be called when is_high_signal=False (check [3]).

        Confirms the Keyword Sniper guard short-circuits before any API call —
        critical for the 'reducing downstream token costs' guarantee (Section 4.1A).
        """
        raw = dict(telegram_raw)
        raw["message_text"] = "Reminder: the community hall will be closed on Tuesday."
        raw["message_url"]  = "https://t.me/clashreport/22222"
        envelope = _build_envelope(raw)
        _, silver = process_telegram_message(envelope)
        assert silver["is_high_signal"] is False

        spy_client = _make_openai_client({})
        process_telegram_gold_message(silver, spy_client)
        spy_client.chat.completions.create.assert_not_called()

    def test_invalid_silver_routes_to_dlq(self, silver_doc_high_signal, mock_client):
        """
        A Silver document with a corrupt document_hash must route to DLQ (check [4]).
        """
        corrupted = dict(silver_doc_high_signal)
        corrupted["document_hash"] = "tooshort"  # not 64 chars

        topic, record = process_telegram_gold_message(corrupted, mock_client)
        assert topic == DEAD_LETTER_QUEUE
        assert "validation_errors" in record

    def test_openai_cognitive_metadata_error_routes_to_dlq(
        self, silver_doc_high_signal
    ):
        """
        An OpenAI API error during cognitive metadata extraction must route to
        DLQ (check [5]).

        Network failures, rate limit errors, and model timeouts raise exceptions.
        The Gold Job must route failures to DLQ rather than crashing the Flink
        task slot (Section 3.5).
        """
        bad_client = MagicMock()
        bad_client.chat.completions.create.side_effect = RuntimeError("rate limit exceeded")
        bad_client.embeddings.create.side_effect = AssertionError("should not reach embedding")

        topic, record = process_telegram_gold_message(silver_doc_high_signal, bad_client)
        assert topic == DEAD_LETTER_QUEUE
        error_text = " ".join(record.get("validation_errors", []))
        assert "rate limit" in error_text.lower() or "cognitive" in error_text.lower()

    def test_openai_embedding_error_routes_to_dlq(
        self, silver_doc_high_signal, mock_ai_meta
    ):
        """
        An OpenAI API error during embedding generation must route to DLQ (check [6]).

        A Gold record without an embedding cannot be inserted into knowledge_vectors
        (which requires a 1536-dim vector column).
        """
        bad_embed_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices[0].message.content = json.dumps(mock_ai_meta)
        bad_embed_client.chat.completions.create.return_value = mock_response
        bad_embed_client.embeddings.create.side_effect = RuntimeError("embedding service down")

        topic, record = process_telegram_gold_message(silver_doc_high_signal, bad_embed_client)
        assert topic == DEAD_LETTER_QUEUE
        error_text = " ".join(record.get("validation_errors", []))
        assert "embedding" in error_text.lower()


# ==========================================================
# Gate 2 — Gold Schema Correctness (checks [7]–[21] + [34]–[37])
# ==========================================================

class TestTelegramGoldSchema:
    """
    Verifies the Gold Global Signal output conforms to Section C.5 for Telegram.
    """

    def test_gold_passes_validator(self, gold_record):
        """
        The Gold output must pass validate_gold_signal() (check [7]).

        Running it again here independently confirms the schema contract,
        not just that the function didn't crash.
        """
        result = validate_gold_signal(gold_record)
        assert result.is_valid, f"Gold signal validation failed: {result.errors}"

    def test_signal_id_is_uuid(self, gold_record):
        """signal_id must be a non-empty UUID string (check [8])."""
        signal_id = gold_record.get("metadata", {}).get("signal_id", "")
        assert isinstance(signal_id, str) and len(signal_id) > 0
        parsed = uuid.UUID(signal_id)
        assert str(parsed) == signal_id

    def test_canonical_event_id_matches_silver(self, silver_doc_high_signal, gold_record):
        """
        metadata.canonical_event_id must equal silver_doc.canonical_event_id (check [9]).

        This is the Paper Trail link — every Gold record traces back to its
        originating Bronze envelope via Silver (Section 3.2 / 7.2).
        """
        assert (
            gold_record["metadata"]["canonical_event_id"]
            == silver_doc_high_signal["canonical_event_id"]
        )

    def test_source_platform_is_telegram(self, gold_record):
        """metadata.source_platform must be 'telegram' (check [10])."""
        assert gold_record["metadata"]["source_platform"] == "telegram"

    def test_silver_data_ref_is_doc_id(self, silver_doc_high_signal, gold_record):
        """
        metadata.silver_data_ref must equal silver_doc.doc_id (check [11]).

        This is the link from the Gold record to the Silver document in
        the knowledge_vault table. knowledge_vectors.py uses it to join
        the embedding back to the message text for RAG retrieval.
        """
        assert (
            gold_record["metadata"]["silver_data_ref"]
            == silver_doc_high_signal["doc_id"]
        )

    def test_raw_data_ref_is_bronze_ref(self, silver_doc_high_signal, gold_record):
        """metadata.raw_data_ref must equal silver_doc.bronze_ref (check [12])."""
        assert (
            gold_record["metadata"]["raw_data_ref"]
            == silver_doc_high_signal["bronze_ref"]
        )

    def test_executive_summary_from_mock(self, gold_record, mock_ai_meta):
        """
        enrichment_ai.executive_summary must equal the mock AI response (check [13]).

        Confirms build_telegram_gold_global_signal() takes the summary from ai_meta,
        not from the raw message text.
        """
        assert (
            gold_record["enrichment_ai"]["executive_summary"]
            == mock_ai_meta["executive_summary"]
        )

    def test_impact_level_is_int_in_range(self, gold_record):
        """enrichment_ai.impact_level must be int in [1, 5] (check [14])."""
        il = gold_record["enrichment_ai"]["impact_level"]
        assert isinstance(il, int), f"impact_level must be int, got {type(il)}"
        assert 1 <= il <= 5, f"impact_level must be in [1, 5], got {il}"

    def test_urgency_level_is_int_in_range(self, gold_record):
        """enrichment_ai.urgency_level must be int in [1, 5] (check [15])."""
        ul = gold_record["enrichment_ai"]["urgency_level"]
        assert isinstance(ul, int)
        assert 1 <= ul <= 5

    def test_reliability_score_is_float_in_range(self, gold_record):
        """enrichment_ai.reliability_score must be float in [0.0, 1.0] (check [16])."""
        rs = gold_record["enrichment_ai"]["reliability_score"]
        assert isinstance(rs, float)
        assert 0.0 <= rs <= 1.0

    def test_sentiment_score_is_float_in_range(self, gold_record):
        """enrichment_ai.sentiment_score must be float in [-1.0, 1.0] (check [17])."""
        ss = gold_record["enrichment_ai"]["sentiment_score"]
        assert isinstance(ss, float)
        assert -1.0 <= ss <= 1.0

    def test_extracted_entities_is_list(self, gold_record):
        """enrichment_ai.extracted_entities must be a list (check [18])."""
        assert isinstance(gold_record["enrichment_ai"]["extracted_entities"], list)

    def test_topic_classification_is_non_empty_string(self, gold_record):
        """enrichment_ai.topic_classification must be a non-empty string (check [19])."""
        tc = gold_record["enrichment_ai"]["topic_classification"]
        assert isinstance(tc, str) and len(tc) > 0

    def test_key_findings_is_a_list(self, gold_record):
        """enrichment_ai.key_findings must be a list (check [20])."""
        assert isinstance(gold_record["enrichment_ai"]["key_findings"], list)

    def test_fact_check_flag_is_bool(self, gold_record):
        """enrichment_ai.fact_check_flag must be a bool (check [21])."""
        assert isinstance(gold_record["enrichment_ai"]["fact_check_flag"], bool)

    def test_embedding_is_1536_dim_float_list(self, gold_record):
        """embedding must be a list[float] of exactly 1536 elements (check [34])."""
        embedding = gold_record.get("embedding", [])
        assert isinstance(embedding, list), "embedding must be a list"
        assert len(embedding) == _EMBEDDING_DIM, (
            f"embedding must be 1536-dim, got {len(embedding)}"
        )
        assert all(isinstance(v, float) for v in embedding[:10]), (
            "embedding elements must be floats"
        )

    def test_content_vitals_url_matches_silver(self, silver_doc_high_signal, gold_record):
        """content_vitals.url must match silver_doc.original_url (check [35])."""
        assert (
            gold_record["content_vitals"]["url"]
            == silver_doc_high_signal["original_url"]
        )

    def test_content_vitals_title_matches_silver(self, silver_doc_high_signal, gold_record):
        """content_vitals.title must match silver_doc.title."""
        assert gold_record["content_vitals"]["title"] == silver_doc_high_signal["title"]

    def test_description_snippet_is_first_300_chars(
        self, silver_doc_high_signal, gold_record
    ):
        """
        content_vitals.description_snippet must be the first 300 chars of
        inverted_pyramid_lead (check [36]).

        For Telegram, inverted_pyramid_lead == message_text. The 300-char cap limits
        Gold record size (Section C.5 rationale).
        """
        snippet  = gold_record["content_vitals"]["description_snippet"]
        expected = silver_doc_high_signal["inverted_pyramid_lead"][:300]
        assert snippet == expected

    def test_signal_id_unique_per_call(self, silver_doc_high_signal, mock_ai_meta):
        """
        signal_id must be a fresh UUID on each call (check [37]).

        Each Gold record is a distinct row in knowledge_vectors with its own PK.
        """
        c1 = _make_openai_client(mock_ai_meta)
        _, r1 = process_telegram_gold_message(silver_doc_high_signal, c1)
        c2 = _make_openai_client(mock_ai_meta)
        _, r2 = process_telegram_gold_message(silver_doc_high_signal, c2)
        assert r1["metadata"]["signal_id"] != r2["metadata"]["signal_id"]


# ==========================================================
# Gate 2 — Telegram Direct Message domain_context (checks [22]–[30])
# ==========================================================

class TestTelegramDirectMessageDomainContext:
    """
    Verifies the Direct Message domain_context extension (Section C.5 Telegram row).

    This is the key structural difference between the Telegram and NewsAPI/ArXiv Gold
    paths: no Tactical fields (is_breaking, sniper_keywords, share_count from NewsAPI),
    no Academic fields (authors, is_peer_reviewed, citation_count, domain_tags from ArXiv).
    Only Telegram-specific provenance fields: channel_username, is_forwarded,
    forwarded_from, views, extracted_links (5 keys total).
    """

    def test_domain_context_has_exactly_five_keys(self, gold_record):
        """
        domain_context must have exactly five keys: channel_username, is_forwarded,
        forwarded_from, views, extracted_links (Section C.5 Telegram Direct Message
        extension) (check [22]).

        Extra keys indicate builder scope creep; missing keys break the schema.
        """
        expected_keys = {
            "channel_username", "is_forwarded", "forwarded_from",
            "views", "extracted_links",
        }
        actual_keys = set(gold_record["domain_context"].keys())
        assert actual_keys == expected_keys, (
            f"domain_context keys mismatch.\n"
            f"  Expected: {sorted(expected_keys)}\n"
            f"  Actual:   {sorted(actual_keys)}"
        )

    def test_domain_context_channel_username_matches_silver(
        self, silver_doc_high_signal, gold_record
    ):
        """domain_context.channel_username must equal silver_doc.channel_username (check [23])."""
        assert (
            gold_record["domain_context"]["channel_username"]
            == silver_doc_high_signal["channel_username"]
        )

    def test_domain_context_is_forwarded_matches_silver(
        self, silver_doc_high_signal, gold_record
    ):
        """
        domain_context.is_forwarded must equal silver_doc.is_forwarded (check [24]).

        For the mock payload is_forwarded=False — the naval alert is an original post.
        """
        assert (
            gold_record["domain_context"]["is_forwarded"]
            == silver_doc_high_signal["is_forwarded"]
        )

    def test_domain_context_forwarded_from_matches_silver(
        self, silver_doc_high_signal, gold_record
    ):
        """
        domain_context.forwarded_from must equal silver_doc.forwarded_from (check [25]).

        For the mock payload forwarded_from=None (original post, not forwarded).
        The field is preserved even when None so downstream consumers can
        distinguish 'not forwarded' from 'forwarded from unknown channel'.
        """
        assert (
            gold_record["domain_context"]["forwarded_from"]
            == silver_doc_high_signal["forwarded_from"]
        )

    def test_domain_context_views_matches_silver(
        self, silver_doc_high_signal, gold_record
    ):
        """
        domain_context.views must equal silver_doc.views (check [26]).

        Views is a proxy for signal reach — 24,312 for the mock message.
        """
        assert (
            gold_record["domain_context"]["views"]
            == silver_doc_high_signal["views"]
        )

    def test_domain_context_extracted_links_matches_silver(
        self, silver_doc_high_signal, gold_record
    ):
        """
        domain_context.extracted_links must equal silver_doc.extracted_links (check [27]).

        Preserved as a list so downstream consumers can follow sources without
        re-parsing the raw message text.
        """
        assert (
            gold_record["domain_context"]["extracted_links"]
            == silver_doc_high_signal["extracted_links"]
        )

    def test_domain_context_has_no_is_breaking_field(self, gold_record):
        """
        domain_context must NOT have an 'is_breaking' field (check [28]).

        is_breaking is part of the NewsAPI Tactical extension only.
        Including it here would pollute the Direct Message schema and
        could mislead RAG agents into treating Telegram messages as
        NewsAPI articles.
        """
        assert "is_breaking" not in gold_record["domain_context"], (
            "domain_context must not have 'is_breaking' (NewsAPI Tactical field)"
        )

    def test_domain_context_has_no_sniper_keywords_field(self, gold_record):
        """
        domain_context must NOT have a 'sniper_keywords' field (check [29]).

        sniper_keywords is a NewsAPI Tactical field. The sniper output is stored
        in the Silver record (silver_doc.sniper_keywords) and available via the
        knowledge_vault join — it does not belong in the Gold domain_context for Telegram.
        """
        assert "sniper_keywords" not in gold_record["domain_context"], (
            "domain_context must not have 'sniper_keywords' (NewsAPI Tactical field)"
        )

    def test_domain_context_has_no_authors_field(self, gold_record):
        """
        domain_context must NOT have an 'authors' field (check [30]).

        authors is part of the ArXiv Academic extension only.
        Telegram messages have no separate author metadata beyond channel_username.
        """
        assert "authors" not in gold_record["domain_context"], (
            "domain_context must not have 'authors' (ArXiv Academic field)"
        )


# ==========================================================
# Gate 2 — No Impact Boost for Telegram (Section A.1, checks [31]–[33])
# ==========================================================

class TestNoImpactBoostForTelegram:
    """
    Verifies that impact_level is NEVER boosted for Telegram messages, regardless
    of content. The Gold Job must use the raw GPT-4o value unchanged (Section A.1).
    """

    def test_impact_level_equals_mock_value_no_boost(
        self, silver_doc_high_signal, mock_ai_meta
    ):
        """
        impact_level in the Gold record must equal the mock AI value (check [31]).

        mock_ai_meta has impact_level=4. The silver_doc has impact_boost=False
        (hard-coded for all Telegram messages). Expected final impact_level = 4 (no +1).

        If Impact Boost were incorrectly applied, impact_level would be 5.
        """
        assert mock_ai_meta["impact_level"] == 4, "Test setup: mock impact_level must be 4"
        assert silver_doc_high_signal["impact_boost"] is False, (
            "Test setup: Telegram silver_doc must have impact_boost=False"
        )

        client = _make_openai_client(mock_ai_meta)
        _, record = process_telegram_gold_message(silver_doc_high_signal, client)

        assert record["enrichment_ai"]["impact_level"] == 4, (
            f"Expected impact_level=4 (no boost for Telegram), "
            f"got {record['enrichment_ai']['impact_level']}"
        )

    def test_geopolitics_energy_content_not_boosted(self, telegram_raw, mock_ai_meta):
        """
        A Telegram message about oil/energy/geopolitics must NOT get impact_level
        boosted (check [32]).

        Even if the content would trigger Impact Boost as a NewsAPI article (Section B.4),
        Telegram messages are categorically excluded (Section A.1). The channel authority
        is structural — enforced at ingestion via the 7-channel Source Registry, not
        per-message via content rules.
        """
        raw = dict(telegram_raw)
        raw["message_text"] = (
            "BREAKING: Houthi forces have declared a blockade of the Bab-el-Mandeb strait. "
            "Israeli tankers are being specifically targeted. Oil prices spiking."
        )
        raw["message_url"] = "https://t.me/clashreport/33333"

        envelope = _build_envelope(raw)
        _, silver = process_telegram_message(envelope)

        # Despite geopolitics/energy/Israel content, impact_boost must still be False
        assert silver["impact_boost"] is False, "Telegram impact_boost must always be False"

        ai_meta_base_4 = dict(mock_ai_meta)
        ai_meta_base_4["impact_level"] = 4

        client = _make_openai_client(ai_meta_base_4)
        topic, record = process_telegram_gold_message(silver, client)

        if topic == GOLD_GLOBAL_NEWS:
            assert record["enrichment_ai"]["impact_level"] == 4, (
                "Geopolitics/energy Telegram message must NOT receive +1 Impact Boost "
                "(Section A.1)"
            )

    def test_builder_has_no_impact_boost_logic(
        self, silver_doc_high_signal, mock_ai_meta
    ):
        """
        Calling build_telegram_gold_global_signal() directly must never apply a boost
        (check [33]).

        This confirms the builder itself is free of any apply_impact_boost() call,
        not just that the test fixture has the right content (Section A.1).
        """
        ai_meta = dict(mock_ai_meta)
        ai_meta["impact_level"] = 2  # low score from GPT

        gold = build_telegram_gold_global_signal(
            silver_doc_high_signal,
            ai_meta,
            _make_mock_embedding(),
        )
        assert gold["enrichment_ai"]["impact_level"] == 2, (
            "build_telegram_gold_global_signal() must not increment impact_level"
        )


# ==========================================================
# Gate 2 — DLQ Correctness (checks [38]–[39])
# ==========================================================

class TestTelegramGoldDLQCorrectness:
    """
    Verifies DLQ records from the Telegram Gold branch are correctly formed.
    """

    def test_dlq_source_topic_is_silver_global_news(self, silver_doc_high_signal):
        """
        DLQ records from the Telegram Gold branch must carry
        source_topic=SILVER_GLOBAL_NEWS (check [38]).

        Telegram, ArXiv, and NewsAPI messages all arrive from SILVER_GLOBAL_NEWS.
        DLQ operators can distinguish Telegram records by inspecting
        original_message.source_name == "telegram".
        """
        bad_client = MagicMock()
        bad_client.chat.completions.create.side_effect = RuntimeError("forced failure")

        topic, dlq_record = process_telegram_gold_message(silver_doc_high_signal, bad_client)

        assert topic == DEAD_LETTER_QUEUE
        assert dlq_record["source_topic"] == SILVER_GLOBAL_NEWS, (
            f"DLQ source_topic must be SILVER_GLOBAL_NEWS ('{SILVER_GLOBAL_NEWS}'), "
            f"got '{dlq_record['source_topic']}'"
        )

    def test_dlq_source_topic_not_silver_social_pulse(self, silver_doc_high_signal):
        """
        Regression guard: DLQ from Telegram Gold must NOT use SILVER_SOCIAL_PULSE
        (check [39]).

        Telegram routes to the Global News family (knowledge_vault / knowledge_vectors),
        not the Social Pulse family. This guard catches the same routing class of bug
        that was fixed in kafka_topics.py (BRONZE_TELEGRAM → SILVER_GLOBAL_NEWS).
        """
        bad_client = MagicMock()
        bad_client.chat.completions.create.side_effect = RuntimeError("forced failure")

        topic, dlq_record = process_telegram_gold_message(silver_doc_high_signal, bad_client)

        assert topic == DEAD_LETTER_QUEUE
        assert dlq_record["source_topic"] != SILVER_SOCIAL_PULSE, (
            "DLQ source_topic must NOT be SILVER_SOCIAL_PULSE — "
            "Telegram is in the Global News family, not Social Pulse"
        )

    def test_dlq_has_non_empty_validation_errors(self, silver_doc_high_signal):
        """
        DLQ record must always include non-empty validation_errors (Section 3.5).

        This ensures the DLQ consumer can always determine the failure reason
        without having to parse the original_message payload.
        """
        bad_client = MagicMock()
        bad_client.chat.completions.create.side_effect = RuntimeError("some api error")

        _, dlq_record = process_telegram_gold_message(silver_doc_high_signal, bad_client)

        assert "validation_errors" in dlq_record
        assert len(dlq_record["validation_errors"]) > 0, (
            "DLQ record must carry at least one validation_error string"
        )
