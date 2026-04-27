"""
Gate 2 — Logic Gate: Article Scraper Unit Tests (Sprint 15).

Validates the domain classification, URL parsing, and scraping logic in
utils/article_scraper.py without making any real HTTP requests.
All newspaper4k Article calls are mocked.

Test checklist:
  [01] _extract_base_domain: www.bbc.com → bbc.com
  [02] _extract_base_domain: blogs.timesofisrael.com → timesofisrael.com
  [03] _extract_base_domain: defense-and-tech.jpost.com → jpost.com
  [04] _extract_base_domain: www.i24news.tv → i24news.tv (non-.com TLD)
  [05] _extract_base_domain: empty string → empty string (no crash)
  [06] is_scrapable_domain: all 6 approved outlets return True
  [07] is_scrapable_domain: non-whitelisted outlets return False
  [08] is_scrapable_domain: empty URL → False
  [09] scrape_article_text: empty URL → None (no Article created)
  [10] scrape_article_text: non-scrapable domain → None (no Article created)
  [11] scrape_article_text: success path → stripped text returned
  [12] scrape_article_text: Article.download() raises exception → None
  [13] scrape_article_text: Article.text is empty after parse → None
  [14] scrape_article_text: Article.text is whitespace-only → None
  [15] SCRAPABLE_BASE_DOMAINS contains exactly the 6 approved outlets
  [16] scrape_article_text: no Article instantiated for non-scrapable URL

Test strategy:
    newspaper4k Article is mocked via unittest.mock.patch("newspaper.Article")
    at the module level to intercept the lazy import inside scrape_article_text().
    No real HTTP requests are made.

References:
    - Sprint 15: Post-Silver article scraping layer
    - Section 4.3: Mock-Driven Development — newspaper4k Article mocked
    - Section 3.2: DRY — all scraping logic in utils/article_scraper.py
    - Section 3.3: Service Isolation — scraper does not write to DB
"""

from unittest.mock import MagicMock, patch

import pytest

from utils.article_scraper import (
    SCRAPABLE_BASE_DOMAINS,
    _extract_base_domain,
    is_scrapable_domain,
    scrape_article_text,
)


# ==========================================================
# [01-05] _extract_base_domain
# ==========================================================

class TestExtractBaseDomain:
    """URL normalisation: extract the registrable root domain."""

    def test_01_www_prefix_stripped(self):
        """[01] www.bbc.com → bbc.com"""
        assert _extract_base_domain("https://www.bbc.com/news/world") == "bbc.com"

    def test_02_content_subdomain_stripped(self):
        """[02] blogs.timesofisrael.com → timesofisrael.com"""
        assert _extract_base_domain(
            "https://blogs.timesofisrael.com/when-the-butcher-joins-the-bench"
        ) == "timesofisrael.com"

    def test_03_multi_segment_subdomain(self):
        """[03] defense-and-tech.jpost.com → jpost.com"""
        assert _extract_base_domain(
            "https://defense-and-tech.jpost.com/post/apps-and-tech-take-the-stage"
        ) == "jpost.com"

    def test_04_non_com_tld(self):
        """[04] www.i24news.tv → i24news.tv"""
        assert _extract_base_domain("https://www.i24news.tv/en/news/israel") == "i24news.tv"

    def test_05_empty_string(self):
        """[05] Empty URL does not crash — returns empty string."""
        assert _extract_base_domain("") == ""


# ==========================================================
# [06-08] is_scrapable_domain
# ==========================================================

class TestIsScrapableDomain:
    """Whitelist gate: True only for the 6 approved outlets."""

    @pytest.mark.parametrize("url", [
        "https://www.bbc.com/news/world-europe-12345",
        "https://www.theguardian.com/world/2026/apr/10/story",
        "https://www.timesofisrael.com/israel-news/latest",
        "https://blogs.timesofisrael.com/some-blog-post",
        "https://www.jpost.com/israel-news/article-12345",
        "https://defense-and-tech.jpost.com/post/tech-article",
        "https://www.ynetnews.com/category/3089",
        "https://www.i24news.tv/en/news/israel",
    ])
    def test_06_approved_outlets_return_true(self, url):
        """[06] All 6 approved outlets (incl. subdomains) return True."""
        assert is_scrapable_domain(url) is True

    @pytest.mark.parametrize("url", [
        "https://www.reuters.com/world/article",
        "https://apnews.com/article/12345",
        "https://www.cnbc.com/world-news/",
        "https://edition.cnn.com/world",
        "https://www.kan.org.il/content/",
        "https://www.wsj.com/articles/12345",
        "https://www.bloomberg.com/news/articles/12345",
        "https://www.nytimes.com/2026/04/10/story.html",
    ])
    def test_07_non_whitelisted_return_false(self, url):
        """[07] Non-whitelisted outlets return False."""
        assert is_scrapable_domain(url) is False

    def test_08_empty_url_returns_false(self):
        """[08] Empty URL returns False — no crash."""
        assert is_scrapable_domain("") is False


# ==========================================================
# [09-14] scrape_article_text
# ==========================================================

class TestScrapeArticleText:
    """Core scraping function: returns text on success, None on any failure."""

    def test_09_empty_url_returns_none(self):
        """[09] Empty URL → None immediately, no Article instantiated."""
        with patch("newspaper.Article") as mock_cls:
            result = scrape_article_text("")
        assert result is None
        mock_cls.assert_not_called()

    def test_10_non_scrapable_domain_returns_none(self):
        """[10] Non-scrapable domain → None without creating an Article object."""
        with patch("newspaper.Article") as mock_cls:
            result = scrape_article_text("https://www.reuters.com/world/article-12345")
        assert result is None
        mock_cls.assert_not_called()

    def test_11_success_returns_stripped_text(self):
        """[11] Success path: mocked Article returns text → caller receives stripped text."""
        mock_article = MagicMock()
        mock_article.text = "  Full article text with real content.  "

        with patch("newspaper.Article", return_value=mock_article):
            result = scrape_article_text("https://www.bbc.com/news/world-12345")

        assert result == "Full article text with real content."
        mock_article.download.assert_called_once()
        mock_article.parse.assert_called_once()

    def test_12_article_exception_returns_none(self):
        """[12] ArticleException (or any exception) during download → None, no re-raise."""
        mock_article = MagicMock()
        mock_article.download.side_effect = Exception("Http error 403")

        with patch("newspaper.Article", return_value=mock_article):
            result = scrape_article_text("https://www.bbc.com/news/world-12345")

        assert result is None

    def test_13_empty_text_after_parse_returns_none(self):
        """[13] Article.text is empty string after parse → None."""
        mock_article = MagicMock()
        mock_article.text = ""

        with patch("newspaper.Article", return_value=mock_article):
            result = scrape_article_text("https://www.theguardian.com/world/story")

        assert result is None

    def test_14_whitespace_only_text_returns_none(self):
        """[14] Article.text is only whitespace → None (strip reveals empty)."""
        mock_article = MagicMock()
        mock_article.text = "   \n\t  "

        with patch("newspaper.Article", return_value=mock_article):
            result = scrape_article_text("https://www.ynetnews.com/category/3089")

        assert result is None

    def test_16_non_scrapable_no_article_instantiation(self):
        """[16] Verify Article class is never instantiated for a non-scrapable URL."""
        with patch("newspaper.Article") as mock_cls:
            scrape_article_text("https://www.bloomberg.com/news/article")
        mock_cls.assert_not_called()


# ==========================================================
# [15] SCRAPABLE_BASE_DOMAINS invariant
# ==========================================================

class TestScrapableBaseDomains:
    """[15] Registry contains exactly the 6 approved outlets."""

    EXPECTED_DOMAINS = {
        "bbc.com",
        "theguardian.com",
        "timesofisrael.com",
        "jpost.com",
        "ynetnews.com",
        "i24news.tv",
    }

    def test_15_contains_all_approved_outlets(self):
        """[15] All 6 T1-approved outlets are in SCRAPABLE_BASE_DOMAINS."""
        assert self.EXPECTED_DOMAINS == SCRAPABLE_BASE_DOMAINS

    def test_15_is_frozenset(self):
        """SCRAPABLE_BASE_DOMAINS is a frozenset (immutable — prevents accidental mutation)."""
        assert isinstance(SCRAPABLE_BASE_DOMAINS, frozenset)
