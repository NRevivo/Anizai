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


# Reddit deduplication (hash_reddit_post) removed — Sprint 11 T4.
# Reddit API pre-approval required (Nov 2025 policy). All code removed.
