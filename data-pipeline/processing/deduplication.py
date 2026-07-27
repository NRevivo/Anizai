"""
Deduplication — SHA-256 content hashing for Silver Layer deduplication.

Why centralized here: multiple Silver Job branches need dedup (Polymarket
comments, NewsAPI articles, ArXiv papers). One module prevents hash logic
from diverging across sources (Section 3.2 DRY principle).

How dedup works at the Silver layer (Section 4.1C):
    The Silver Job computes a content hash for each inbound message.
    Before writing to the Social Vault or Knowledge Vault the persistence
    layer checks whether this hash already exists in the DB. If it does,
    the record is skipped — preventing duplicate ingestion from overlapping
    aggregators (e.g., the same article surfaced by both NewsAPI and ArXiv).

References:
    - Section 4.1C: Deduplication (SHA-256)
    - Section 3.2:  DRY — shared logic centralised in processing/
    - Section C.3:  Silver Social schema  — `content_hash` field
    - Section C.3:  Silver Document schema — `document_hash` field
"""

import hashlib
import json
from typing import Any


# ==========================================================
# Core Hasher
# ==========================================================

def sha256_hash(content: Any) -> str:
    """
    Compute a SHA-256 hex digest of any serialisable content.

    Why SHA-256: collision-resistant at pipeline scale; 64-char hex output
    fits in a standard varchar column and is human-inspectable in logs.

    Args:
        content: str, dict, or list. Non-string values are JSON-serialised
                 with sorted keys so field ordering never produces different
                 hashes for logically identical objects.

    Returns:
        64-character lowercase hex string (SHA-256 digest).
    """
    if isinstance(content, str):
        data = content
    else:
        data = json.dumps(content, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


# ==========================================================
# Document Deduplication — NewsAPI / ArXiv (Phase 3)
# ==========================================================

def hash_document(full_text: str, url: str = "") -> str:
    """
    Compute a stable content hash for a news article or academic paper.

    Why normalise whitespace: scrapers and APIs sometimes return the same
    article with minor whitespace or encoding differences. Normalisation
    collapses these variants to the same hash, preventing near-duplicate
    entries in the Knowledge Vault (Section 4.1C).

    Args:
        full_text: Raw article body or paper abstract.
        url:       Canonical URL — included in the hash to distinguish two
                   articles with identical text on different sources.

    Returns:
        64-character SHA-256 hex string.

    Usage (Silver Job — NewsAPI / ArXiv branches, Phase 3):
        doc_hash = hash_document(article["full_text"], article["url"])
        # stored as `document_hash` in Silver Document schema (Section C.3)
    """
    normalised = " ".join(full_text.lower().split())
    return sha256_hash(f"{url.strip()}|{normalised}")


# ==========================================================
# Social Batch Deduplication — Polymarket
# ==========================================================

def hash_social_batch(market_id: str, comments: list[dict]) -> str:
    """
    Compute a stable content hash for a Polymarket comment batch.

    Hashes market_id + the sorted set of comment identifiers so that:
      - The same batch always produces the same hash regardless of API
        return order.
      - A batch with even one new comment produces a different hash,
        triggering re-ingestion of the updated set.

    Why sort: Polymarket's REST API does not guarantee comment ordering.
    Sorting by comment_id makes the hash deterministic across fetches.

    Args:
        market_id: Polymarket market ID (e.g., "0x87ae1d...").
        comments:  List of comment dicts. Each should have a `comment_id`
                   or `id` field. Dicts missing both fall back to their
                   text content as the identifier.

    Returns:
        64-character SHA-256 hex string.

    Usage (Silver Job — Polymarket comment branch):
        content_hash = hash_social_batch(raw["market_id"], raw["raw_comments"])
        # stored as `content_hash` in Silver Social object
    """
    ids = sorted(
        str(c.get("comment_id") or c.get("id") or c.get("text", ""))
        for c in comments
    )
    return sha256_hash(f"{market_id}|{'|'.join(ids)}")


# ==========================================================
# Story Deduplication — HackerNews (Phase 7D, D1a)
# ==========================================================

def hash_hackernews_story(story_id: str) -> str:
    """
    Stable dedup key for a HackerNews story, keyed on story_id ALONE
    (Phase 7D, decision D1a — plan §3).

    Why story_id-only, not hash_social_batch(story_id, top_comments):
        D1a is "one enrichment per HackerNews story, ever." The previous
        derivation folded the comment set into the hash, so the key drifted every
        pulse as comments accrued — re-archiving and re-enriching the same story
        repeatedly (the social side of KG-A-7 / KG-A-8). Keying on story_id alone
        makes the key — and therefore the social_vault dedup check and the
        _deterministic_signal_id() UUID5 derived from this same content_hash —
        stable for the life of the story. HackerNews is a social-pulse source
        feeding community sentiment; re-analysing a story as its comment count
        drifts is low-value relative to its RPD cost. If the multi-day run shows HN
        evidence is load-bearing, D1 option (c) (a refresh window) becomes a
        data-backed follow-up rather than a guess made now.

    Why the "hackernews|" namespace prefix:
        story_id is an Algolia objectID (a bare integer string); the prefix keeps
        it from ever colliding with another source's key in the shared
        social_vault content_hash space.

    Whitespace normalisation (Phase 7D, T7):
        story_id is stripped before hashing, so " 12345 " and "12345" produce the
        SAME key. A stray whitespace difference must never fork a story's dedup
        identity — the Silver mapper strips it too, and this is the defensive
        backstop that keeps the pure hasher stable for any caller.

    Args:
        story_id: HackerNews Algolia objectID — the story's stable identifier.

    Returns:
        64-character SHA-256 hex string.

    Usage (Silver Job — HackerNews branch, map_hackernews_story_to_silver):
        content_hash = hash_hackernews_story(story_id)
        # stored as `content_hash` in the Silver Social record (Section C.3)
    """
    return sha256_hash(f"hackernews|{str(story_id).strip()}")


# Reddit deduplication (hash_reddit_post) removed — Sprint 11 T4.
# Reddit API pre-approval required (Nov 2025 policy). All code removed.
