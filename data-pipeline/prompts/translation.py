"""
Translation prompt — GPT-3.5-turbo plain-text translation (Section 4.1D).

Used by:
    - processing/translation.py (Silver layer — non-English Telegram channels)
    - silver_job.py/_translate_keyword() (Google Trends DE/IL keywords, future use)

Unlike the Gold-layer cognitive metadata prompts, this prompt requests PLAIN TEXT
output (the translated string) rather than JSON. Rationale: translation is a direct
text transformation with a single output — there is no metadata schema to extract,
and wrapping a single translated string in JSON adds parsing overhead without benefit.
The caller strips leading/trailing whitespace and uses the raw response body directly.

Why GPT-3.5-turbo (not GPT-4o):
    Translation is a well-solved NLP task that does not require Chain-of-Thought
    reasoning or complex instruction following. GPT-3.5-turbo produces high-quality
    translations for Hebrew and Arabic. Using GPT-4o would be 3-5× more expensive
    per token with no material quality improvement for this task (Section 4.1D).

Languages currently active (Section A.1 channel registry):
    - Hebrew (he): abualiexpress, yediotnews25
    English channels require no translation and never call this prompt.

Output format:
    Plain text (not JSON). Caller: translation.translate_to_english().
    The function returns the .strip() of choices[0].message.content.

Last reviewed: Sprint 13 (2026-04-10)
"""

TRANSLATION_SYSTEM_PROMPT = """\
You are a professional translator for a geopolitical intelligence platform.

Your task: translate the input text to English.

Rules:
- Return ONLY the translated English text — no introduction, no commentary, \
no quotation marks, no markdown formatting.
- Preserve proper nouns exactly as they appear (names of people, organizations, \
countries, cities, weapon systems, military units, and financial instruments).
- Preserve numbers, dates, coordinates, ticker symbols, and URLs exactly as they appear.
- If the input text is already in English, return it unchanged.
- Do not summarize, paraphrase, or omit any part of the original text.
- Do not add translator notes or explanatory brackets.\
"""
