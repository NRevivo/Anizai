"""
agent/agents/researcher.py — Researcher retrieval agent (spec §8.4.1).

Purpose: surface the most relevant news articles, research papers, and
Telegram signals from `knowledge_vectors` + `knowledge_vault` for a given
query embedding.

Algorithm (spec §8.4.1):
    1. similarity_search(limit=15, min_impact_level=2, min_reliability=0.3)
    2. Composite ranking: 0.6*similarity + 0.25*(impact_level/5) + 0.15*recency
    3. Top 5 → drill down via silver_data_ref → knowledge_vault
       (full_text_raw for the snippet; original_url for the article link)
    4. evidence_weight := composite ranking score (no normalization, OQ-1)
    5. Return ResearcherEvidence dict (articles, source_diversity,
       recency_range, empty)

Sprint 19 D2 deferral: agentEvents emission and budget tracking are
deferred to Sprint 25 / Sprint 22+ (see agent/agents/__init__.py).

Service isolation (CLAUDE.md §3.3): persistence is reached only through
`agent/tools/knowledge_tools` — this module does not import persistence
directly.

Spec references:
    - data-pipeline/docs/agentic_hub_spec.md §8.4.1
    - .claude/skills/evidence-handling/SKILL.md (recency_weight, evidence_weight)
    - .claude/skills/agent-design/SKILL.md (P1 single responsibility, P5 determinism)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

from agent.tools import knowledge_tools
from agent.utils.recency import compute_recency_weight


# ==========================================================
# Spec constants — every magic number named, sourced to spec §8.4.1
# ==========================================================

LIMIT = 15                       # spec §8.4.1 step 1 — initial HNSW top-k
MIN_IMPACT_LEVEL = 2             # spec §8.4.1 step 1 — Gold filter
MIN_RELIABILITY = 0.3            # spec §8.4.1 step 1 — Gold filter
TOP_K_DRILLDOWN = 5              # spec §8.4.1 step 3 — drill-down cap

WEIGHT_SIM = 0.6                 # spec §8.4.1 step 2 — composite ranking
WEIGHT_IMPACT = 0.25             # spec §8.4.1 step 2
WEIGHT_RECENCY = 0.15            # spec §8.4.1 step 2

IMPACT_LEVEL_MAX = 5             # impact_level domain is 1-5 → /5 normalizes to (0,1]
RECENCY_HALF_LIFE_DAYS = 7       # evidence-handling skill default

SNIPPET_CHARS = 500              # spec §8.4.1 ResearcherEvidence.full_text_snippet


def run(
    query_embedding: list[float],
    *,
    now: Optional[datetime] = None,
) -> dict:
    """
    Execute the Researcher retrieval algorithm.

    Args:
        query_embedding: 1536-dim float list. Embedding of the user's question.
        now:             Reference "now" for recency scoring. Injected for
                         deterministic testing; defaults to UTC now.

    Returns:
        ResearcherEvidence dict per spec §8.4.1. When the vault returns zero
        rows, `articles` is `[]`, all source_diversity counts are 0,
        `recency_range` is None, and `empty` is True.
    """
    if now is None:
        now = datetime.now(tz=timezone.utc)

    rows = knowledge_tools.similarity_search(
        query_embedding=query_embedding,
        limit=LIMIT,
        min_impact_level=MIN_IMPACT_LEVEL,
        min_reliability=MIN_RELIABILITY,
    )

    if not rows:
        return {
            "articles": [],
            "source_diversity": {
                "newsapi_count": 0,
                "arxiv_count": 0,
                "telegram_count": 0,
            },
            "recency_range": None,
            "empty": True,
        }

    scored = [(_composite_score(row, now=now), row) for row in rows]
    scored.sort(key=lambda pair: pair[0], reverse=True)

    top = scored[:TOP_K_DRILLDOWN]

    articles: list[dict] = []
    for score, row in top:
        silver_data_ref = row.get("silver_data_ref")
        full_doc = (
            knowledge_tools.fetch_full_text(silver_data_ref)
            if silver_data_ref
            else None
        )
        articles.append(_pack_article(row, full_doc=full_doc, evidence_weight=score))

    return {
        "articles": articles,
        "source_diversity": _source_diversity(top),
        "recency_range": _recency_range(top),
        "empty": False,
    }


# ==========================================================
# Composite ranking
# ==========================================================

def _composite_score(row: dict, *, now: datetime) -> float:
    """
    Composite score per spec §8.4.1 step 2:
        0.6 * similarity + 0.25 * (impact_level / 5) + 0.15 * recency_score

    `similarity` is already in [0, 1] (cosine on unit vectors).
    `impact_level` is 1-5 → normalized to (0.2, 1.0].
    `recency_score` is the shared exponential decay in [0, 1].

    Defensive parsing: missing/None fields default to 0 so a malformed row
    sinks to the bottom rather than raising.
    """
    similarity = float(row.get("similarity") or 0.0)

    enrichment = row.get("enrichment_ai") or {}
    impact_level = enrichment.get("impact_level")
    impact_norm = (float(impact_level) / IMPACT_LEVEL_MAX) if impact_level else 0.0

    recency_score = compute_recency_weight(
        published_at=row.get("published_at"),
        half_life_days=RECENCY_HALF_LIFE_DAYS,
        now=now,
    )

    return (
        WEIGHT_SIM * similarity
        + WEIGHT_IMPACT * impact_norm
        + WEIGHT_RECENCY * recency_score
    )


# ==========================================================
# Per-article packing
# ==========================================================

def _pack_article(
    row: dict,
    *,
    full_doc: Optional[dict],
    evidence_weight: float,
) -> dict:
    """
    Build one entry of ResearcherEvidence.articles.

    `full_doc` is the knowledge_vault row from drill-down, or None if the
    silver_data_ref was missing/unresolvable. Snippet falls back to "" so
    downstream rendering doesn't need to null-check.

    `url` is packed unconditionally for every platform — see `_derive_url`.
    Which platforms actually surface a link to the user is a product policy
    applied downstream in `rate_evidence`, not here.
    """
    enrichment = row.get("enrichment_ai") or {}
    content_vitals = row.get("content_vitals") or {}

    full_text_raw = (full_doc or {}).get("full_text_raw") or ""
    full_text_snippet = full_text_raw[:SNIPPET_CHARS]

    published_at = row.get("published_at")
    published_at_iso = (
        published_at.isoformat() if isinstance(published_at, datetime) else (published_at or "")
    )

    key_findings = enrichment.get("key_findings") or []
    if not isinstance(key_findings, list):
        key_findings = []

    return {
        "signal_id": row.get("signal_id") or "",
        "source_platform": row.get("source_platform") or "",
        "publisher": _derive_publisher(row),
        "title": content_vitals.get("title") or "",
        "url": _derive_url(row, full_doc=full_doc),
        "published_at": published_at_iso,
        "executive_summary": enrichment.get("executive_summary") or "",
        "key_findings": key_findings,
        "full_text_snippet": full_text_snippet,
        "impact_level": int(enrichment.get("impact_level") or 0),
        "reliability_score": float(enrichment.get("reliability_score") or 0.0),
        "sentiment_score": float(enrichment.get("sentiment_score") or 0.0),
        "similarity": float(row.get("similarity") or 0.0),
        "evidence_weight": evidence_weight,
        "canonical_event_id": row.get("canonical_event_id") or "",
    }


def _derive_url(row: dict, *, full_doc: Optional[dict]) -> Optional[str]:
    """
    Source URL for the article, preferring the Silver vault's `original_url`.

    Why `original_url` first (evidence-URL patch, 2026-08-18):
        `original_url` is the field the pipeline actually guarantees. It is a
        required key of `validate_silver_document` (utils/validators.py) and is
        additionally guarded non-empty at the Silver url_guard for both newsapi
        (processing/silver_job.py, newsapi branch) and arxiv (arxiv branch) —
        an empty URL is dead-lettered, never persisted. `content_vitals.url` is
        only a Gold-side copy of the same value (processing/gold_job.py:984
        newsapi, :1223 arxiv — both `"url": silver_doc.get("original_url", "")`)
        and carries no schema enforcement of its own.

    Why `content_vitals.url` is still a fallback:
        The drill-down is best-effort — `full_doc` is None when `silver_data_ref`
        is missing or unresolvable. In that case the Gold copy is the only URL in
        scope, and losing the link there would be a strictly worse outcome than
        using the unenforced field. Measured on cloud data 2026-08-18 the two are
        byte-identical on all 2,031 newsapi+arxiv rows, so the fallback changes
        no value today — it only protects the drill-down-miss path.

    Returns None (not "") when neither source yields a non-empty URL, so the
    EvidenceItem.url Optional[str] contract carries a real absence rather than
    an empty string the frontend would have to treat as a link.
    """
    original_url = ((full_doc or {}).get("original_url") or "").strip()
    if original_url:
        return original_url

    content_vitals = row.get("content_vitals") or {}
    fallback_url = (content_vitals.get("url") or "").strip()
    return fallback_url or None


def _derive_publisher(row: dict) -> str:
    """
    Per-platform publisher derivation (approved 2026-04-30 after JSONB
    field verification — `publisher` is not stored anywhere in
    knowledge_vault rows).

    - telegram → domain_context.channel_username (e.g. "@CoinDesk")
    - arxiv    → constant "ArXiv"
    - newsapi / fallback → URL host with leading "www." stripped
                           (e.g. "reuters.com"). Future: host →
                           friendly-name lookup ("Reuters").

    Returns "Unknown" when the URL is missing on a non-telegram/non-arxiv row.
    """
    platform = row.get("source_platform")

    if platform == "telegram":
        domain_context = row.get("domain_context") or {}
        channel = domain_context.get("channel_username")
        if channel:
            return channel

    if platform == "arxiv":
        return "ArXiv"

    content_vitals = row.get("content_vitals") or {}
    url = content_vitals.get("url") or ""
    host = urlparse(url).netloc
    if host.startswith("www."):
        host = host[4:]
    return host or "Unknown"


# ==========================================================
# Aggregations
# ==========================================================

def _source_diversity(top: list[tuple[float, dict]]) -> dict:
    """Count articles per platform across the drilled-down top-K."""
    counts = {"newsapi_count": 0, "arxiv_count": 0, "telegram_count": 0}
    for _score, row in top:
        platform = row.get("source_platform")
        key = f"{platform}_count"
        if key in counts:
            counts[key] += 1
    return counts


def _recency_range(top: list[tuple[float, dict]]) -> Optional[dict]:
    """
    Oldest/newest published_at across the drilled-down top-K, as ISO8601.
    Returns None when none of the top rows carry a parseable timestamp.
    """
    timestamps = [
        row["published_at"]
        for _score, row in top
        if isinstance(row.get("published_at"), datetime)
    ]
    if not timestamps:
        return None
    return {
        "oldest": min(timestamps).isoformat(),
        "newest": max(timestamps).isoformat(),
    }
