"""
Gate 2 — Logic Gate: SHA-256 Deduplication Unit Tests (Section 4.1C).

Validates that the deduplication module produces stable, collision-resistant
content hashes for the Silver Job's dedup guard. NewsAPI is the primary
consumer this sprint (hash_document); social batch functions are also
covered since they share the same core hasher.

Design decisions:
  - Zero live connections — all functions are pure.
  - Tests verify determinism, field sensitivity, whitespace normalisation,
    and output format requirements.

Gate 2 checklist (Section 9.3 Gate 2 — Deduplication):
  [1]  hash_document returns a 64-character hex string
  [2]  hash_document is deterministic: same inputs → same hash
  [3]  hash_document is URL-sensitive: same text, different URL → different hash
  [4]  hash_document is URL-inclusive: no URL vs. empty URL → same hash
  [5]  hash_document normalises whitespace: double spaces, tabs, newlines → same hash
  [6]  hash_document normalises case: uppercase body → same hash as lowercase
  [7]  hash_document is text-sensitive: changing one word changes the hash
  [8]  hash_document handles empty full_text without raising
  [9]  hash_social_batch is deterministic regardless of comment list order
  [10] hash_social_batch is market_id-sensitive: different market → different hash
  [11] hash_social_batch detects new comments: adding a comment changes the hash
  [12] sha256_hash on dict uses sorted keys (field order never changes the hash)
  [13] sha256_hash output is always a 64-char lowercase hex string

References:
    - Section 4.1C: Deduplication (SHA-256)
    - Section 3.2:  DRY — shared logic centralised in processing/
    - Section C.3:  Silver document_hash and content_hash fields
    - Section 9.3:  Triple-Gate Test Matrix — Gate 2
"""

import pytest

from processing.deduplication import (
    hash_document,
    hash_social_batch,
    sha256_hash,
)

# A realistic Reuters article body (abbreviated) used across multiple tests.
SAMPLE_ARTICLE = (
    "VIENNA, March 31 (Reuters) - OPEC+ ministers agreed on Monday to maintain "
    "their current crude oil production cuts through the third quarter of 2026, "
    "keeping the alliance's collective reduction at 3.66 million barrels per day."
)
SAMPLE_URL = "https://www.reuters.com/markets/commodities/opec-extends-cuts-2026-03-31"


# ==========================================================
# Gate 2 — sha256_hash Core (check [13])
# ==========================================================

class TestSha256Hash:
    """Verifies the core hasher used by all higher-level helpers."""

    def test_returns_64_char_hex_string(self):
        result = sha256_hash("test")
        assert isinstance(result, str)
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_deterministic_on_string(self):
        assert sha256_hash("hello") == sha256_hash("hello")

    def test_different_inputs_different_hashes(self):
        assert sha256_hash("hello") != sha256_hash("world")

    def test_dict_input_uses_sorted_keys(self):
        """
        Dict hashing must use sort_keys=True so field insertion order never
        produces different hashes for logically identical objects.

        If this fails, the same article arriving from two API calls with
        slightly different dict construction order would get different hashes,
        breaking the dedup guard entirely (Section 4.1C).
        """
        dict_ab = {"a": 1, "b": 2}
        dict_ba = {"b": 2, "a": 1}
        assert sha256_hash(dict_ab) == sha256_hash(dict_ba)

    def test_list_input_is_serialised(self):
        """List inputs must be hashable without raising."""
        result = sha256_hash([1, 2, 3])
        assert len(result) == 64

    def test_empty_string_returns_known_hash(self):
        """Empty string must return a valid 64-char hash (not crash)."""
        result = sha256_hash("")
        assert len(result) == 64


# ==========================================================
# Gate 2 — hash_document (checks [1]–[8])
# ==========================================================

class TestHashDocument:
    """
    Validates hash_document() — the primary dedup function for NewsAPI
    articles and ArXiv papers in the Silver Knowledge Vault (Section C.3).
    """

    def test_returns_64_char_hex_string(self):
        """Output must be a 64-character lowercase hex string (check [1])."""
        result = hash_document(SAMPLE_ARTICLE, SAMPLE_URL)
        assert isinstance(result, str)
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_deterministic(self):
        """Same article text and URL must always produce the same hash (check [2])."""
        h1 = hash_document(SAMPLE_ARTICLE, SAMPLE_URL)
        h2 = hash_document(SAMPLE_ARTICLE, SAMPLE_URL)
        assert h1 == h2

    def test_url_sensitive(self):
        """
        Same article body on a different URL must produce a different hash (check [3]).

        Why this matters: NewsAPI and ArXiv can surface the same Reuters article
        on different canonical URLs. Treating them as duplicates would suppress
        legitimate cross-aggregator coverage (Section 4.1C).
        """
        different_url = "https://www.bloomberg.com/news/opec-cuts-2026"
        h1 = hash_document(SAMPLE_ARTICLE, SAMPLE_URL)
        h2 = hash_document(SAMPLE_ARTICLE, different_url)
        assert h1 != h2, "Different URLs must produce different hashes"

    def test_empty_url_same_as_no_url(self):
        """
        Calling with url="" must produce the same hash as calling with no url
        argument (check [4]). Ensures the default parameter is consistent.
        """
        h1 = hash_document(SAMPLE_ARTICLE, "")
        h2 = hash_document(SAMPLE_ARTICLE)
        assert h1 == h2

    def test_whitespace_normalisation_double_spaces(self):
        """
        An article with double spaces must hash identically to the same article
        with single spaces (check [5]).

        Why: HTTP APIs sometimes return bodies with collapsed or expanded
        whitespace depending on encoding. A scraper returning double spaces
        for the same article must not create a duplicate Knowledge Vault entry.
        """
        normalised   = "OPEC ministers agreed to maintain production cuts."
        double_space = "OPEC  ministers  agreed  to  maintain  production  cuts."
        assert hash_document(normalised, SAMPLE_URL) == hash_document(double_space, SAMPLE_URL)

    def test_whitespace_normalisation_newlines_tabs(self):
        """Newlines and tabs must be collapsed to single spaces before hashing."""
        clean  = "OPEC agreed to extend cuts through Q3 2026."
        messy  = "OPEC\tagreed\nto extend\t\tcuts\nthrough Q3 2026."
        assert hash_document(clean, SAMPLE_URL) == hash_document(messy, SAMPLE_URL)

    def test_case_normalisation(self):
        """
        Uppercase and lowercase versions of the same article must produce the
        same hash (check [6]).

        Why: some APIs return ALL-CAPS headlines. The dedup guard must not
        create a duplicate entry for "OPEC CUTS" vs "opec cuts" from two
        different sources (Section 4.1C: 'prevents duplicate ingestion').
        """
        lower = "opec agrees to maintain crude oil cuts through q3 2026."
        upper = "OPEC AGREES TO MAINTAIN CRUDE OIL CUTS THROUGH Q3 2026."
        assert hash_document(lower, SAMPLE_URL) == hash_document(upper, SAMPLE_URL)

    def test_text_sensitive(self):
        """
        Changing even one word must produce a completely different hash (check [7]).

        This confirms SHA-256 avalanche effect — a single character change
        produces an entirely different digest, preventing near-duplicate
        articles (different updates of the same story) from being collapsed.
        """
        original = "OPEC agrees to maintain cuts through Q3 2026."
        modified = "OPEC agrees to increase cuts through Q3 2026."   # "maintain" → "increase"
        assert hash_document(original, SAMPLE_URL) != hash_document(modified, SAMPLE_URL)

    def test_empty_full_text_does_not_raise(self):
        """
        Empty full_text must return a valid hash without raising (check [8]).

        Why: some NewsAPI articles have no body text (content field is empty
        on free/basic plan tiers). The Silver Job must not crash on these —
        an empty-text article still gets a unique hash via its URL.
        """
        result = hash_document("", SAMPLE_URL)
        assert isinstance(result, str)
        assert len(result) == 64

    def test_empty_text_and_url_returns_valid_hash(self):
        """Fully empty inputs must not raise — returns hash of empty content."""
        result = hash_document("", "")
        assert len(result) == 64

    def test_two_different_articles_different_hashes(self):
        """Two distinct Reuters articles must produce different hashes."""
        article_a = "OPEC extends crude oil production cuts through Q3."
        article_b = "Federal Reserve holds interest rates steady amid inflation."
        url_a = "https://reuters.com/article-a"
        url_b = "https://reuters.com/article-b"
        assert hash_document(article_a, url_a) != hash_document(article_b, url_b)

    def test_url_alone_differentiates_identical_text(self):
        """
        Two articles with identical text bodies but different URLs must have
        different hashes — the URL is always part of the hash input.
        """
        text = "Breaking news: major geopolitical event."
        h1 = hash_document(text, "https://reuters.com/article-1")
        h2 = hash_document(text, "https://ap.org/article-1")
        assert h1 != h2


# ==========================================================
# Gate 2 — hash_social_batch (checks [9]–[11])
# ==========================================================

class TestHashSocialBatch:
    """
    Validates hash_social_batch() — used by the Polymarket Silver branch.
    Covered here to confirm the shared sha256_hash core is stable.
    """

    MARKET_ID = "0x87ae1d39a1b42a30d3c3f45a8"

    COMMENTS = [
        {"comment_id": "c1", "text": "Yes will happen"},
        {"comment_id": "c2", "text": "No chance"},
        {"comment_id": "c3", "text": "50/50"},
    ]

    def test_deterministic(self):
        """Same market_id and same comments → same hash (check [9])."""
        h1 = hash_social_batch(self.MARKET_ID, self.COMMENTS)
        h2 = hash_social_batch(self.MARKET_ID, self.COMMENTS)
        assert h1 == h2

    def test_order_independent(self):
        """
        Comment list order must not affect the hash (check [9]).

        Why: Polymarket's REST API does not guarantee comment ordering.
        A re-fetch returning comments in different order must produce the
        same hash as the original — otherwise every re-fetch triggers
        re-ingestion of the same batch (Section 4.1C).
        """
        reversed_comments = list(reversed(self.COMMENTS))
        h1 = hash_social_batch(self.MARKET_ID, self.COMMENTS)
        h2 = hash_social_batch(self.MARKET_ID, reversed_comments)
        assert h1 == h2, "Comment order must not change the batch hash"

    def test_market_id_sensitive(self):
        """Different market_id + same comments → different hash (check [10])."""
        h1 = hash_social_batch(self.MARKET_ID, self.COMMENTS)
        h2 = hash_social_batch("0xdifferentmarketid", self.COMMENTS)
        assert h1 != h2

    def test_new_comment_changes_hash(self):
        """
        Adding a new comment to the batch must produce a different hash (check [11]).

        This is how the Silver Job detects that a market has new comments and
        re-ingests — the updated batch hash differs from the stored one.
        """
        original = list(self.COMMENTS)
        extended = original + [{"comment_id": "c4", "text": "New prediction"}]
        h1 = hash_social_batch(self.MARKET_ID, original)
        h2 = hash_social_batch(self.MARKET_ID, extended)
        assert h1 != h2

    def test_empty_comments_returns_valid_hash(self):
        """Empty comment list must return a valid hash (not crash)."""
        result = hash_social_batch(self.MARKET_ID, [])
        assert len(result) == 64


# hash_reddit_post removed — Sprint 11 T4. Reddit API pre-approval required (Nov 2025 policy).
