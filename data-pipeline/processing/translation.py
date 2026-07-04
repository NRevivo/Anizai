"""
Real-time Translation — Non-English Signal Translation (Section 4.1D).

Translates non-English Telegram channel messages and Google Trends keywords to
English at the Silver layer, ensuring a linguistically unified Gold Layer.

Two public functions are provided:
    translate_to_english(text, channel_username, client)
        — for Telegram messages; looks up language from CHANNEL_LANGUAGE allowlist.
    translate_keyword_to_english(term, geo, client)
        — for Google Trends keywords; looks up language from GEO_LANGUAGE allowlist.

Both share the private _call_translation_api() helper to avoid duplicating the
OpenAI call logic (Section 3.2 — DRY principle).

Architecture:
    - Silver layer only. All other OpenAI calls in the pipeline are in gold_job.py.
    - Client is injected as an argument (same pattern as call_openai_consensus() in
      gold_job.py) so tests can pass a mock without importing openai.
    - client=None is a valid no-op: the function returns the original text unchanged.
      This allows Gate 2 tests to call map_telegram_message_to_silver() without an
      OpenAI client and get the pre-translation content (identical to pre-Phase 5 behaviour).
    - Translation failure is non-fatal: original text is returned with a warning.
      The underlying signal is valid; only the enrichment failed (Section 4.3).

Channel language registry (Section A.1 — 7 Telegram channels):
    Only channels with non-English content appear in CHANNEL_LANGUAGE.
    English-only channels return False from needs_translation() without any dict lookup.

    Active as of Sprint 13 (2026-04-10):
        abualiexpress  — Hebrew (he)
        yediotnews25   — Hebrew (he)
    English-only (not in CHANNEL_LANGUAGE):
        clashreport, financialjuice, disclosetv, Faytuks_Network, intelslava

Geo language registry (Section B.5 — Google Trends DE/IL geos):
    Active as of Sprint 13 (2026-04-10):
        IL — Hebrew (he)  — Israeli geo surfaces Hebrew trending keywords
        DE — German  (de) — German geo surfaces German trending keywords

Why GPT-3.5-turbo (not GPT-4o):
    Translation is a well-solved NLP task. GPT-3.5-turbo handles Hebrew, Arabic,
    and German reliably. Using GPT-4o would cost 3-5× more per token with no
    material quality improvement for direct translation (Section 4.1D).

References:
    - Section 4.1D: Real-time Translation (Silver Job task)
    - Section A.1:  Telegram Channel Registry (7 channels)
    - Section B.5:  Google Trends parameters (DE/IL geos)
    - Section 4.3:  Dead-Letter Queue — schema failures only; enrichment failures
                    must not discard valid-signal records
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from prompts.translation import TRANSLATION_SYSTEM_PROMPT
from utils.llm_cost import record_usage

logger = logging.getLogger(__name__)


# ==========================================================
# Channel Language Registry (Section A.1)
# ==========================================================
# Maps lowercased Telegram channel username → ISO 639-1 language code.
# Only non-English channels are listed. English channels are absent —
# needs_translation() returns False for any username not in this dict.
CHANNEL_LANGUAGE: dict[str, str] = {
    "abualiexpress": "he",
    "yediotnews25":  "he",
}

# ==========================================================
# Geo Language Registry (Section B.5)
# ==========================================================
# Maps Google Trends geo code (uppercase) → ISO 639-1 language code.
# Only geos that regularly surface non-English trending keywords are listed.
GEO_LANGUAGE: dict[str, str] = {
    "IL": "he",   # Israel → Hebrew trending keywords
    "DE": "de",   # Germany → German trending keywords
}

# GPT-3.5-turbo for translation — cheaper than GPT-4o, sufficient for
# direct translation (not reasoning or structured extraction).
TRANSLATION_MODEL = "gpt-3.5-turbo"

# Conservative token cap: Telegram messages are micro-articles (50-500 chars).
# Google Trends keywords are 1-5 words. 512 tokens is well above the expected
# output length for any input we translate.
_MAX_TOKENS = 512


# ==========================================================
# Private Helper
# ==========================================================

def _call_translation_api(
    text: str,
    source_lang: str,
    client: Any,
    *,
    source_name: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> str:
    """
    Call GPT-3.5-turbo to translate text to English (Section 4.1D).

    Not called directly — use translate_to_english() or translate_keyword_to_english().
    Centralises the OpenAI call so both public functions share one implementation
    (Section 3.2 — DRY principle).

    client=None guard: returns text unchanged, no API call. This allows callers
    in Gate 2 tests and non-translation contexts to pass None safely.

    On API failure: logs a warning and returns the original text (pass-through).
    Translation is enrichment, not validation; a failure must not discard a valid
    OSINT signal (Section 4.3).

    Cost instrumentation (Phase 7B.5-I T4): record_usage() runs immediately
    after the response returns, BEFORE the content is read — a call that was
    paid for is counted even if the response shape is unexpected (the except
    branch then passes the original text through). source_name/trace_id are
    keyword-only with None defaults so existing callers stay valid (P2).

    Args:
        text:        The text to translate. Must be non-empty.
        source_lang: ISO 639-1 language code of the source text (e.g. "he", "de").
                     Used only for logging — the model infers the language from the text.
        client:      openai.OpenAI instance, or None for a no-op pass-through.
        source_name: Producing source for the cost row ("telegram" / "googletrends").
        trace_id:    canonical_event_id of the processed object, when the caller has one.

    Returns:
        Translated English string, or the original text on failure or client=None.
    """
    if client is None:
        return text

    try:
        response = client.chat.completions.create(
            model=TRANSLATION_MODEL,
            messages=[
                {"role": "system", "content": TRANSLATION_SYSTEM_PROMPT},
                {"role": "user",   "content": text},
            ],
            temperature=0.0,   # deterministic — translation has one correct answer
            max_tokens=_MAX_TOKENS,
        )
        record_usage(
            TRANSLATION_MODEL, response,
            site="translate",
            source_name=source_name,
            trace_id=trace_id,
        )
        return response.choices[0].message.content.strip()

    except Exception as exc:  # noqa: BLE001
        # Pass-through: log the failure but return the original text so the
        # Silver record is still emitted with unmodified content (Section 4.3).
        logger.warning(
            "[translation] OpenAI call failed (lang=%s) — using original text. Error: %s",
            source_lang, exc,
        )
        return text


# ==========================================================
# Public API
# ==========================================================

def needs_translation(channel_username: str) -> bool:
    """
    Return True if this Telegram channel posts non-English content (Section A.1).

    Uses the CHANNEL_LANGUAGE allowlist — case-insensitive lookup.
    English-only channels return False immediately, incurring zero API cost.

    Args:
        channel_username: Telegram channel username from the Bronze payload
                          (e.g. "abualiexpress", "Faytuks_Network").

    Returns:
        True if the channel is registered as non-English; False otherwise.
    """
    return channel_username.lower() in CHANNEL_LANGUAGE


def translate_to_english(
    text: str,
    channel_username: str,
    client: Any,
    *,
    trace_id: Optional[str] = None,
) -> str:
    """
    Translate a non-English Telegram message to English (Section 4.1D).

    Decision tree:
        1. text is empty             → return "" immediately (no API call)
        2. needs_translation()=False → return text unchanged (no API call, zero cost)
        3. client is None            → return text unchanged (Gate 2 test no-op)
        4. OpenAI call succeeds      → return stripped translated string
        5. OpenAI call fails         → log warning, return original text (pass-through)

    Args:
        text:             Raw message text from the Telegram Bronze payload.
        channel_username: Channel username used to look up the source language.
        client:           openai.OpenAI instance, or None for a no-op pass-through.
        trace_id:         canonical_event_id for the cost row (7B.5-I T4);
                          keyword-only, None default keeps existing callers valid.

    Returns:
        English-language string. Equals the original text if the channel is
        English-only, client is None, or the API call failed.
    """
    if not text or not needs_translation(channel_username):
        return text

    source_lang = CHANNEL_LANGUAGE[channel_username.lower()]
    translated = _call_translation_api(
        text, source_lang, client,
        source_name="telegram", trace_id=trace_id,
    )

    if translated != text:
        logger.debug(
            "[translation] channel=%s lang=%s original_len=%d translated_len=%d",
            channel_username, source_lang, len(text), len(translated),
        )

    return translated


def translate_keyword_to_english(
    term: str,
    geo: str,
    client: Any,
    *,
    trace_id: Optional[str] = None,
) -> str:
    """
    Translate a non-English Google Trends keyword to English (Section 4.1D, B.5).

    Used by silver_job._translate_keyword() so the Gold Job's keyed-state
    momentum operator keys by an English term regardless of the trending geo.
    DE/IL geos surface German/Hebrew keywords; other geos (US, GB, SG) are
    English-only and return the term unchanged.

    Decision tree:
        1. term is empty            → return "" immediately
        2. geo not in GEO_LANGUAGE  → return term unchanged (English geo)
        3. client is None           → return term unchanged (Gate 2 test no-op)
        4. OpenAI call succeeds     → return translated term
        5. OpenAI call fails        → log warning, return original term (pass-through)

    Args:
        term:     Keyword string as returned by Pytrends (may be in any language).
        geo:      Google Trends geo code (e.g. "US", "IL", "DE"). Case-insensitive.
        client:   openai.OpenAI instance, or None for a no-op pass-through.
        trace_id: canonical_event_id for the cost row (7B.5-I T4);
                  keyword-only, None default keeps existing callers valid.

    Returns:
        English-language keyword string, or the original term unchanged.
    """
    if not term or geo.upper() not in GEO_LANGUAGE:
        return term

    source_lang = GEO_LANGUAGE[geo.upper()]
    return _call_translation_api(
        term, source_lang, client,
        source_name="googletrends", trace_id=trace_id,
    )
