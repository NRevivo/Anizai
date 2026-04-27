"""
Gate 2 — Logic Gate: Keyword Sniper Filter Unit Tests (Section 4.1A).

Validates that the Keyword Sniper correctly scores article text against the
Master Keyword List using position-weighted density, and that is_high_signal
correctly gates downstream OpenAI enrichment.

Design decisions:
  - Zero live connections — snipe() and snipe_article() are pure functions.
  - No mock file needed — inputs are constructed inline as plain strings.
  - Tests cover: scoring math, position weighting, word-boundary safety,
    deduplication across fields, edge cases, and constants invariants.

Gate 2 checklist (Section 9.3 Gate 2 — Keyword Sniper):
  [1]  SniperResult fields: is_high_signal, relevance_score, matched_keywords,
       keyword_density — all present and correctly typed
  [2]  Title keyword hit produces score >= description hit > content hit
  [3]  Same keyword found in multiple fields is counted only once (dedup)
  [4]  relevance_score is capped at 1.0 regardless of hit count
  [5]  is_high_signal=True when relevance_score >= threshold
  [6]  is_high_signal=False when no keywords match (pure noise)
  [7]  Word-boundary guard: "war" does not match "award" or "forward"
  [8]  Case-insensitivity: "NATO" matches "nato" keyword
  [9]  Multi-word phrase matching: "crude oil", "interest rates", "central bank"
  [10] matched_keywords is a sorted list of strings
  [11] Empty inputs produce is_high_signal=False, score=0.0
  [12] Custom threshold parameter respected
  [13] snipe_article() correctly extracts title/description/content from dict
  [14] MASTER_KEYWORD_LIST: all 9 Section B.4 NewsAPI keywords are present
  [15] MASTER_KEYWORD_LIST: key geopolitical and financial terms present
  [16] SCORE_SATURATION and DEFAULT_THRESHOLD are positive floats
  [17] Realistic Reuters headline produces is_high_signal=True
  [18] Lifestyle/sports article produces is_high_signal=False

References:
    - Section 4.1A: Keyword Sniper Filter specification
    - Section 2.2:  SNR Optimization — Domain Filtering Strategy
    - Section B.4:  NewsAPI Keyword Sniper keywords
    - Section 9.3:  Triple-Gate Test Matrix — Gate 2
    - Section C.3:  Silver relevance_score field
    - Section C.5:  Gold domain_context.sniper_keywords field
"""

import pytest

from processing.keyword_sniper import (
    DEFAULT_THRESHOLD,
    MASTER_KEYWORD_LIST,
    SCORE_SATURATION,
    TITLE_WEIGHT,
    DESCRIPTION_WEIGHT,
    CONTENT_WEIGHT,
    SniperResult,
    snipe,
    snipe_article,
)

# ==========================================================
# Section B.4 keywords that MUST be in MASTER_KEYWORD_LIST
# ==========================================================
B4_REQUIRED_KEYWORDS = {
    "conflict",
    "sanctions",
    "crude oil",
    "opec",
    "missile defense",
    "interest rates",
    "ai regulation",
    "nato",
    "central bank",
}


# ==========================================================
# Gate 2 — SniperResult Shape (check [1])
# ==========================================================

class TestSniperResultShape:
    """Verifies that snipe() always returns a correctly-typed SniperResult."""

    def test_returns_sniper_result_instance(self):
        result = snipe("NATO summit in Brussels", "Ministers discuss eastern flank.")
        assert isinstance(result, SniperResult)

    def test_is_high_signal_is_bool(self):
        result = snipe("NATO summit")
        assert isinstance(result.is_high_signal, bool)

    def test_relevance_score_is_float(self):
        result = snipe("NATO summit")
        assert isinstance(result.relevance_score, float)

    def test_matched_keywords_is_list_of_strings(self):
        result = snipe("NATO summit")
        assert isinstance(result.matched_keywords, list)
        for kw in result.matched_keywords:
            assert isinstance(kw, str)

    def test_keyword_density_is_float(self):
        result = snipe("NATO summit")
        assert isinstance(result.keyword_density, float)

    def test_relevance_score_in_range(self):
        """relevance_score must always be in [0.0, 1.0]."""
        for title in [
            "",
            "sports update",
            "NATO missiles crude oil opec sanctions conflict war ceasefire",
        ]:
            result = snipe(title)
            assert 0.0 <= result.relevance_score <= 1.0, (
                f"relevance_score out of range for title={title!r}: {result.relevance_score}"
            )


# ==========================================================
# Gate 2 — Position Weighting (check [2])
# ==========================================================

class TestPositionWeighting:
    """
    Verifies that title > description > content in scoring weight.

    Tests use an isolated single keyword in each field to compare scores
    independently — no other keywords present to avoid confounding results.
    """

    def test_title_scores_higher_than_description(self):
        """
        The same keyword in the title must produce a strictly higher score
        than the same keyword in the description only.
        """
        title_result = snipe(title="NATO summit in Brussels", description="", content="")
        desc_result  = snipe(title="", description="NATO summit in Brussels", content="")
        assert title_result.relevance_score > desc_result.relevance_score, (
            f"Title score ({title_result.relevance_score}) should exceed "
            f"description score ({desc_result.relevance_score})"
        )

    def test_description_scores_higher_than_content(self):
        """
        The same keyword in the description must produce a strictly higher
        score than the same keyword in the content only.
        """
        desc_result    = snipe(title="", description="NATO summit in Brussels", content="")
        content_result = snipe(title="", description="", content="NATO summit in Brussels")
        assert desc_result.relevance_score > content_result.relevance_score, (
            f"Description score ({desc_result.relevance_score}) should exceed "
            f"content score ({content_result.relevance_score})"
        )

    def test_title_weight_produces_correct_raw_contribution(self):
        """
        One title keyword should produce relevance_score = TITLE_WEIGHT / SCORE_SATURATION.
        Uses "nato" (single-word, unambiguous match).
        """
        result   = snipe(title="NATO summit", description="", content="")
        expected = round(TITLE_WEIGHT / SCORE_SATURATION, 4)
        assert result.relevance_score == expected, (
            f"Expected {expected}, got {result.relevance_score}"
        )

    def test_content_weight_produces_correct_raw_contribution(self):
        """One content-only keyword should give CONTENT_WEIGHT / SCORE_SATURATION."""
        result   = snipe(title="", description="", content="NATO summit discussed")
        expected = round(CONTENT_WEIGHT / SCORE_SATURATION, 4)
        assert result.relevance_score == expected, (
            f"Expected {expected}, got {result.relevance_score}"
        )


# ==========================================================
# Gate 2 — Cross-Field Deduplication (check [3])
# ==========================================================

class TestCrossFieldDeduplication:
    """
    Verifies that a keyword found in multiple fields is counted only once
    (using the highest-weight field's score).
    """

    def test_keyword_in_title_and_description_counted_once(self):
        """
        "nato" in both title and description must produce the same score as
        "nato" in title only — no double-counting.
        """
        both_result  = snipe(title="NATO summit", description="NATO ministers met", content="")
        title_result = snipe(title="NATO summit", description="",                    content="")
        assert both_result.relevance_score == title_result.relevance_score, (
            "Keyword found in title+description should score the same as title-only "
            f"(title_only={title_result.relevance_score}, both={both_result.relevance_score})"
        )

    def test_keyword_in_all_three_fields_counted_once_at_title_weight(self):
        """
        A keyword appearing in title, description, AND content must still
        produce only the title-weight score contribution.
        """
        all_fields_result = snipe(
            title="NATO summit",
            description="NATO ministers convened",
            content="The NATO meeting concluded with a statement.",
        )
        title_only_result = snipe(title="NATO summit", description="", content="")
        assert all_fields_result.relevance_score == title_only_result.relevance_score

    def test_matched_keywords_no_duplicates(self):
        """matched_keywords must contain each matched term exactly once."""
        result = snipe(
            title="NATO sanctions crude oil",
            description="NATO and EU impose crude oil sanctions",
            content="The crude oil sanctions are linked to NATO pressure",
        )
        # Every matched keyword appears exactly once in the list
        assert len(result.matched_keywords) == len(set(result.matched_keywords)), (
            f"Duplicate keywords in matched_keywords: {result.matched_keywords}"
        )


# ==========================================================
# Gate 2 — Score Cap (check [4])
# ==========================================================

class TestScoreCap:
    """relevance_score must never exceed 1.0."""

    def test_many_keywords_capped_at_1(self):
        """
        A headline saturated with multiple high-signal keywords must produce
        relevance_score == 1.0, never > 1.0.
        """
        dense_title = (
            "NATO sanctions crude oil opec conflict war ceasefire "
            "missile nato israel iran"
        )
        result = snipe(title=dense_title)
        assert result.relevance_score == 1.0, (
            f"Expected 1.0 for keyword-dense title, got {result.relevance_score}"
        )

    def test_score_monotonically_increases_with_more_keywords(self):
        """Adding more matched keywords must never decrease the score."""
        r1 = snipe(title="NATO summit")
        r2 = snipe(title="NATO summit imposes sanctions")
        r3 = snipe(title="NATO summit imposes sanctions on crude oil opec")
        assert r1.relevance_score <= r2.relevance_score <= r3.relevance_score


# ==========================================================
# Gate 2 — High-Signal Gate (checks [5], [6])
# ==========================================================

class TestHighSignalGate:
    """Verifies the is_high_signal boolean gate (the SNR filter)."""

    def test_keyword_match_produces_high_signal(self):
        """Any keyword match above DEFAULT_THRESHOLD must set is_high_signal=True."""
        result = snipe(title="NATO summit", description="", content="")
        assert result.is_high_signal is True

    def test_pure_noise_produces_low_signal(self):
        """
        An article with no Master Keyword List terms must produce is_high_signal=False.

        This is the primary noise-reduction gate — lifestyle, sports, and
        entertainment content must never proceed to OpenAI enrichment (Section 4.1A).
        """
        result = snipe(
            title="Local football team wins championship match",
            description="Great performance in the final against rivals.",
            content="Thousands of fans celebrated in the stadium.",
        )
        assert result.is_high_signal is False
        assert result.relevance_score == 0.0
        assert result.matched_keywords == []

    def test_custom_threshold_zero_always_high_signal_on_match(self):
        """threshold=0.0 means any non-zero score qualifies as high-signal."""
        result = snipe(title="", description="", content="NATO discussed", threshold=0.0)
        assert result.is_high_signal is True

    def test_custom_threshold_1_never_high_signal_except_perfect(self):
        """threshold=1.0 means only a perfectly saturated article passes."""
        partial = snipe(title="NATO summit", threshold=1.0)
        assert partial.is_high_signal is False

        dense = snipe(
            title="NATO sanctions crude oil opec conflict war ceasefire missile",
            threshold=1.0,
        )
        assert dense.is_high_signal is True   # score == 1.0


# ==========================================================
# Gate 2 — Word Boundary Safety (check [7])
# ==========================================================

class TestWordBoundary:
    """
    Verifies that short keywords do not match as substrings inside longer words.

    The most dangerous false-positive risk is short keywords like "war" matching
    "award", "forward", "hardware", etc. A fashion article should never be
    flagged as geopolitical signal.
    """

    def test_war_does_not_match_award(self):
        result = snipe(title="Award ceremony for best film", description="")
        assert "war" not in result.matched_keywords, (
            "'war' keyword must not match inside 'award' (word-boundary failure)"
        )

    def test_war_does_not_match_forward(self):
        result = snipe(title="Moving forward with new strategy", description="")
        assert "war" not in result.matched_keywords

    def test_war_does_not_match_hardware(self):
        result = snipe(title="New hardware released by tech company", description="")
        assert "war" not in result.matched_keywords

    def test_war_matches_standalone(self):
        """'war' must match when it appears as a standalone word."""
        result = snipe(title="War escalates in eastern region", description="")
        assert "war" in result.matched_keywords

    def test_iran_does_not_match_ukraine_substring(self):
        """'iran' must not match as a substring of 'ukraine' (it doesn't, but confirm)."""
        result = snipe(title="Ukraine conflict update", description="")
        assert "iran" not in result.matched_keywords

    def test_energy_does_not_match_synergy(self):
        result = snipe(title="Business synergy drives growth", description="")
        assert "energy" not in result.matched_keywords

    def test_energy_matches_standalone(self):
        result = snipe(title="Energy prices surge after OPEC meeting", description="")
        assert "energy" in result.matched_keywords


# ==========================================================
# Gate 2 — Case Insensitivity (check [8])
# ==========================================================

class TestCaseInsensitivity:
    """Keywords in MASTER_KEYWORD_LIST are lowercase. Input may be any case."""

    def test_uppercase_nato_matches(self):
        result = snipe(title="NATO expands eastern flank")
        assert "nato" in result.matched_keywords

    def test_mixed_case_crude_oil_matches(self):
        result = snipe(title="Crude Oil Prices Surge on OPEC Cuts")
        assert "crude oil" in result.matched_keywords
        assert "opec"      in result.matched_keywords

    def test_all_caps_title_matches(self):
        result = snipe(title="SANCTIONS IMPOSED ON IRAN OVER NUCLEAR PROGRAM")
        assert "sanctions" in result.matched_keywords
        assert "iran"      in result.matched_keywords
        assert "nuclear"   in result.matched_keywords


# ==========================================================
# Gate 2 — Multi-Word Phrase Matching (check [9])
# ==========================================================

class TestMultiWordPhrases:
    """Multi-word keywords must match as complete phrases, not partial words."""

    def test_crude_oil_matches_as_phrase(self):
        result = snipe(title="Crude oil falls below $70 per barrel")
        assert "crude oil" in result.matched_keywords

    def test_interest_rates_matches(self):
        result = snipe(title="Fed holds interest rates steady amid inflation fears")
        assert "interest rates" in result.matched_keywords

    def test_central_bank_matches(self):
        result = snipe(title="Central bank signals pause in rate cycle")
        assert "central bank" in result.matched_keywords

    def test_missile_defense_matches(self):
        result = snipe(title="US deploys missile defense system in Eastern Europe")
        assert "missile defense" in result.matched_keywords

    def test_ai_regulation_matches(self):
        result = snipe(title="EU passes landmark AI regulation framework")
        assert "ai regulation" in result.matched_keywords

    def test_crude_does_not_match_without_oil(self):
        """
        'crude' alone must NOT match 'crude oil' keyword — phrase must be complete.
        (Note: 'crude' by itself is not in the master list either.)
        """
        result = snipe(title="Crude remarks from the minister")
        assert "crude oil" not in result.matched_keywords


# ==========================================================
# Gate 2 — Sorted matched_keywords (check [10])
# ==========================================================

class TestMatchedKeywordsSorted:
    """matched_keywords must be alphabetically sorted (Section 4.1A: deterministic output)."""

    def test_matched_keywords_are_sorted(self):
        result = snipe(
            title="NATO sanctions crude oil opec conflict war",
            description="",
            content="",
        )
        assert result.matched_keywords == sorted(result.matched_keywords), (
            f"matched_keywords not sorted: {result.matched_keywords}"
        )


# ==========================================================
# Gate 2 — Empty Inputs (check [11])
# ==========================================================

class TestEmptyInputs:
    """All-empty input must return a zero-score, not-high-signal result."""

    def test_all_empty_strings(self):
        result = snipe("", "", "")
        assert result.is_high_signal is False
        assert result.relevance_score == 0.0
        assert result.matched_keywords == []
        assert result.keyword_density == 0.0

    def test_whitespace_only_inputs(self):
        result = snipe("   ", "\t\n", "  \n  ")
        assert result.is_high_signal is False
        assert result.relevance_score == 0.0


# ==========================================================
# Gate 2 — snipe_article() Wrapper (check [13])
# ==========================================================

class TestSnipeArticleWrapper:
    """snipe_article() must extract title/description/content from raw_payload dict."""

    def test_extracts_title_field(self):
        raw = {"title": "NATO summit", "description": "", "content": ""}
        result = snipe_article(raw)
        assert "nato" in result.matched_keywords

    def test_extracts_description_field(self):
        raw = {"title": "", "description": "Sanctions imposed on Iran", "content": ""}
        result = snipe_article(raw)
        assert "sanctions" in result.matched_keywords
        assert "iran"      in result.matched_keywords

    def test_missing_keys_default_to_empty(self):
        """raw_payload with no text fields must not raise — silently return no-match."""
        raw = {}
        result = snipe_article(raw)
        assert result.is_high_signal is False
        assert result.relevance_score == 0.0

    def test_passes_threshold_through(self):
        """Custom threshold must be forwarded to snipe()."""
        raw = {"title": "NATO summit", "description": "", "content": ""}
        high_threshold = snipe_article(raw, threshold=1.0)
        assert high_threshold.is_high_signal is False   # single keyword can't saturate

        low_threshold = snipe_article(raw, threshold=0.0)
        assert low_threshold.is_high_signal is True

    def test_produces_same_result_as_snipe_direct(self):
        """snipe_article() must be equivalent to calling snipe() directly."""
        raw = {
            "title":       "OPEC cuts crude oil output amid demand slump",
            "description": "Oil ministers agreed to extend production cuts.",
            "content":     "The decision was taken in Vienna on Monday.",
        }
        direct  = snipe(raw["title"], raw["description"], raw["content"])
        wrapper = snipe_article(raw)
        assert direct.relevance_score  == wrapper.relevance_score
        assert direct.matched_keywords == wrapper.matched_keywords
        assert direct.is_high_signal   == wrapper.is_high_signal


# ==========================================================
# Gate 2 — Master Keyword List Invariants (checks [14], [15])
# ==========================================================

class TestMasterKeywordListInvariants:
    """
    Verifies the MASTER_KEYWORD_LIST contains all mandated terms.

    A missing keyword means the SNR filter silently passes noise articles
    that the spec says should be blocked (Section 4.1A).
    """

    def test_all_b4_newsapi_keywords_present(self):
        """
        All 9 keywords mandated by Section B.4 must be in MASTER_KEYWORD_LIST.

        These are the minimum required for the NewsAPI General-category
        Keyword Sniper to function correctly at the Silver layer.
        """
        missing = B4_REQUIRED_KEYWORDS - MASTER_KEYWORD_LIST
        assert not missing, (
            f"MASTER_KEYWORD_LIST is missing Section B.4 required keywords: {missing}"
        )

    def test_key_geopolitical_terms_present(self):
        """Core geopolitical conflict terms must be present."""
        required = {"nato", "sanctions", "conflict", "war", "nuclear", "missile"}
        missing  = required - MASTER_KEYWORD_LIST
        assert not missing, f"Missing geopolitical terms: {missing}"

    def test_key_energy_terms_present(self):
        """Core energy/commodity terms must be present."""
        required = {"crude oil", "opec", "natural gas", "petroleum"}
        missing  = required - MASTER_KEYWORD_LIST
        assert not missing, f"Missing energy terms: {missing}"

    def test_key_financial_terms_present(self):
        """Core financial/monetary terms must be present."""
        required = {"interest rates", "central bank", "inflation", "recession"}
        missing  = required - MASTER_KEYWORD_LIST
        assert not missing, f"Missing financial terms: {missing}"

    def test_master_list_is_frozenset(self):
        """MASTER_KEYWORD_LIST must be a frozenset (immutable — prevents accidental mutation)."""
        assert isinstance(MASTER_KEYWORD_LIST, frozenset)

    def test_all_keywords_lowercase(self):
        """All keywords must be lowercase — matching is case-insensitive via _normalise()."""
        non_lowercase = [kw for kw in MASTER_KEYWORD_LIST if kw != kw.lower()]
        assert not non_lowercase, (
            f"Keywords must be lowercase, found: {non_lowercase}"
        )


# ==========================================================
# Gate 2 — Scoring Constants (check [16])
# ==========================================================

class TestScoringConstants:
    """Scoring constants must be positive floats in expected ranges."""

    def test_score_saturation_is_positive_float(self):
        assert isinstance(SCORE_SATURATION, (int, float))
        assert SCORE_SATURATION > 0

    def test_default_threshold_in_range(self):
        assert 0.0 <= DEFAULT_THRESHOLD < 1.0

    def test_title_weight_greater_than_description(self):
        assert TITLE_WEIGHT > DESCRIPTION_WEIGHT

    def test_description_weight_greater_than_content(self):
        assert DESCRIPTION_WEIGHT > CONTENT_WEIGHT

    def test_content_weight_positive(self):
        assert CONTENT_WEIGHT > 0


# ==========================================================
# Gate 2 — Realistic Article Tests (checks [17], [18])
# ==========================================================

class TestRealisticArticles:
    """
    End-to-end validation using realistic news headlines.
    Mirrors the articles that will flow through the Silver Job in production.
    """

    def test_reuters_geopolitical_headline_is_high_signal(self):
        """
        A Reuters-style geopolitical headline must pass the keyword sniper.
        This is the primary use case — it must never be false-negative.
        """
        result = snipe(
            title="NATO allies agree on new sanctions package targeting Iran oil exports",
            description="Foreign ministers convened in Brussels to discuss the escalating conflict.",
            content="The sanctions target crude oil shipments and missile technology transfers.",
        )
        assert result.is_high_signal is True
        assert result.relevance_score > 0.5
        # Expect multiple keywords across geopolitical + energy domains
        assert len(result.matched_keywords) >= 4

    def test_bloomberg_financial_headline_is_high_signal(self):
        """A Bloomberg-style monetary policy headline must pass."""
        result = snipe(
            title="Federal Reserve holds interest rates as inflation fears ease",
            description="Central bank signals patience, bond yields fall.",
            content="",
        )
        assert result.is_high_signal is True
        assert "interest rates" in result.matched_keywords
        assert "central bank"   in result.matched_keywords

    def test_opec_energy_article_is_high_signal(self):
        """An OPEC crude oil production article must pass."""
        result = snipe(
            title="OPEC extends crude oil output cuts through Q3 2026",
            description="Oil price climbs on supply reduction agreement.",
            content="",
        )
        assert result.is_high_signal is True
        assert "opec"      in result.matched_keywords
        assert "crude oil" in result.matched_keywords

    def test_sports_article_is_not_high_signal(self):
        """
        A sports/lifestyle article must be blocked — no false positives.

        False positives are expensive: each one costs an OpenAI API call
        (Section 4.1A: 'significantly reducing downstream token costs').
        """
        result = snipe(
            title="Manchester City wins Premier League title in dramatic final",
            description="Haaland scores twice as City clinch the championship.",
            content="Thousands of fans celebrated in the streets of Manchester.",
        )
        assert result.is_high_signal is False
        assert result.relevance_score == 0.0

    def test_celebrity_entertainment_article_is_not_high_signal(self):
        """Celebrity news must be blocked."""
        result = snipe(
            title="Taylor Swift announces new world tour dates",
            description="The pop star will perform in 40 cities across six continents.",
            content="Tickets go on sale Friday.",
        )
        assert result.is_high_signal is False

    def test_arxiv_ai_regulation_abstract_is_high_signal(self):
        """
        An ArXiv abstract on AI Regulation / geopolitical risk should pass.
        Uses lower threshold (0.05) since abstracts are shorter than news articles.
        """
        result = snipe(
            title="AI Regulation and National Security: A Framework for Export Controls",
            description=(
                "We propose a risk-based framework for semiconductor export controls "
                "targeting dual-use AI systems in adversarial geopolitical contexts."
            ),
            content="",
            threshold=0.05,
        )
        assert result.is_high_signal is True
        assert "ai regulation"    in result.matched_keywords
        assert "export controls"  in result.matched_keywords
