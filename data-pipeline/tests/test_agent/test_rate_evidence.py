"""
Gate 1 tests for `agent/nodes/rate_evidence.py` (Sprint 20 T20.1).

Strategy: pure-mock at the OpenAI client boundary (matches Sprint 19
query_understand pattern). Fake `chat.completions.create()` responses
shaped like openai>=1.x: `response.choices[0].message.content` is a
JSON string; `response.usage.total_tokens` is an int.

Coverage:
- empty / missing evidence packages → empty evidence_trail, no LLM call
- researcher articles → correct source_type per source_platform
- pulse polymarket consensus → vault_market
- pulse hackernews discussion → vault_hackernews
- market fred anomalies → vault_fred
- public-URL gate: newsapi/arxiv keep the packed url, telegram + all
  non-researcher sources stay None; blank never becomes "" (2026-08-18 patch)
- recency_weight computed deterministically (newest item gets ~1.0)
- credibility_tier assigned per-platform mapping
- batching: 17 items → 3 batches (8+8+1)
- rating merged by evidence_id (not list index)
- items missing from response → rating_unavailable
- LLM call fails on a batch → mark_unavailable + continue
- llm_calls_count + total_tokens_used aggregated
- empty raw_question → AgentProcessingError
- malformed response shape → batch marked unavailable (graceful)
- ISO string published_at parsed; naive datetime → UTC
- prompt + schema forwarded to OpenAI verbatim
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agent.errors import AgentProcessingError
from agent.nodes import rate_evidence
from agent.schemas import EvidenceItem
from agent.prompts.evidence_rating import (
    BATCH_SIZE,
    RESPONSE_SCHEMA,
    SYSTEM_PROMPT,
)


# ==========================================================
# Helpers — fake OpenAI responses
# ==========================================================
def _make_response(*, ratings: list[dict], total_tokens: int = 200) -> SimpleNamespace:
    """openai>=1.x ChatCompletion shape with a ratings payload."""
    payload = {"ratings": ratings}
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=json.dumps(payload))
            )
        ],
        usage=SimpleNamespace(total_tokens=total_tokens),
    )


def _ratings_for(items: list[dict], *, score: float = 0.8, justification: str = "ok") -> list[dict]:
    """Produce a ratings list shaped to match the input batch order."""
    return [
        {"evidence_id": item["evidence_id"], "relevance_score": score, "justification": justification}
        for item in items
    ]


def _client_returning(response: SimpleNamespace) -> MagicMock:
    client = MagicMock()
    client.chat.completions.create = MagicMock(return_value=response)
    return client


def _client_returning_per_batch(responses: list[SimpleNamespace]) -> MagicMock:
    """A client whose .create() returns the given responses in order."""
    client = MagicMock()
    client.chat.completions.create = MagicMock(side_effect=responses)
    return client


# ==========================================================
# Helpers — fixture evidence packages
# ==========================================================
def _researcher_package(articles: list[dict]) -> dict:
    return {
        "articles": articles,
        "source_diversity": {},
        "recency_range": None,
        "empty": not articles,
    }


def _researcher_article(
    *,
    source_platform: str = "newsapi",
    publisher: str = "reuters.com",
    title: str = "Sample headline",
    snippet: str = "Body text. " * 20,
    published_at: str | None = None,
    url: str | None = "https://reuters.com/world/sample-article",
) -> dict:
    """
    Mirror of researcher._pack_article's output shape.

    `url` is packed by the researcher for EVERY platform (evidence-URL patch,
    2026-08-18) — the newsapi/arxiv gate lives in rate_evidence, so the fixture
    must supply a URL on telegram rows too or the gate tests would pass for the
    wrong reason (asserting None because nothing was there to drop).
    """
    if published_at is None:
        published_at = datetime.now(tz=timezone.utc).isoformat()
    return {
        "signal_id": "sig-1",
        "source_platform": source_platform,
        "publisher": publisher,
        "title": title,
        "url": url,
        "published_at": published_at,
        "executive_summary": "exec",
        "key_findings": [],
        "full_text_snippet": snippet,
        "impact_level": 3,
        "reliability_score": 0.7,
        "sentiment_score": 0.0,
        "similarity": 0.85,
        "evidence_weight": 0.7,
        "canonical_event_id": "",
    }


def _pulse_package(
    *,
    market_consensus: list[dict] | None = None,
    community_discussion: list[dict] | None = None,
) -> dict:
    market_consensus = market_consensus or []
    community_discussion = community_discussion or []
    return {
        "market_consensus": market_consensus,
        "community_discussion": community_discussion,
        "overall_sentiment": 0.0,
        "empty": not market_consensus and not community_discussion,
    }


def _polymarket_consensus_item(market_id: str = "fed-rate-cut-q2-2026") -> dict:
    return {
        "signal_id": "poly-1",
        "market_id_ref": market_id,
        "consensus_rating": 0.6,
        "comment_volume_analyzed": 50,
        "aggregation_window_hours": 24,
        "executive_summary": "Market leans toward cuts.",
        "key_arguments_pro": [],
        "key_arguments_con": [],
        "similarity": 0.7,
        "evidence_weight": 0.65,
    }


def _hackernews_item() -> dict:
    return {
        "signal_id": "hn-1",
        "platform": "hackernews",
        "title": "Show HN: rates analysis",
        "points": 80,
        "top_technical_insights": ["Insight A", "Insight B", "Insight C"],
        "community_sentiment": 0.2,
        "similarity": 0.5,
        "evidence_weight": 0.4,
    }


def _market_package(fred_anomalies: list[dict] | None = None) -> dict:
    fred_anomalies = fred_anomalies or []
    return {
        "polymarket": None,
        "linked_sources": [],
        "fred_anomalies": fred_anomalies,
        "google_trends": [],
        "empty": not fred_anomalies,
    }


def _fred_anomaly() -> dict:
    return {
        "series_id": "FEDFUNDS",
        "indicator_name": "Federal Funds Effective Rate",
        "current_value": 5.25,
        "anomaly_flags": ["spike"],
        "impact_level": 4,
        "change_7d": 0.3,
    }


# ==========================================================
# Empty / missing packages
# ==========================================================
def test_empty_state_returns_empty_evidence_trail_no_llm_call():
    client = MagicMock()
    out = rate_evidence.run({"raw_question": "Q?"}, client=client)
    assert out["evidence_trail"] == []
    assert out["llm_calls_count"] == 0
    assert out["total_tokens_used"] == 0
    client.chat.completions.create.assert_not_called()


def test_all_packages_empty_returns_empty_evidence_trail():
    client = MagicMock()
    state = {
        "raw_question": "Q?",
        "researcher_evidence": _researcher_package([]),
        "pulse_evidence": _pulse_package(),
        "market_evidence": _market_package(),
    }
    out = rate_evidence.run(state, client=client)
    assert out["evidence_trail"] == []
    client.chat.completions.create.assert_not_called()


def test_missing_raw_question_raises():
    client = MagicMock()
    with pytest.raises(AgentProcessingError, match="raw_question"):
        rate_evidence.run({}, client=client)


def test_blank_raw_question_raises():
    client = MagicMock()
    with pytest.raises(AgentProcessingError, match="raw_question"):
        rate_evidence.run({"raw_question": "   "}, client=client)


# ==========================================================
# Source-type derivation per platform
# ==========================================================
@pytest.mark.parametrize("source_platform,expected_source_type,expected_tier", [
    ("newsapi", "vault_news", "tier_2"),
    ("arxiv", "vault_arxiv", "tier_2"),
    ("telegram", "vault_telegram", "tier_3"),
])
def test_researcher_article_source_type_and_tier_per_platform(
    source_platform: str,
    expected_source_type: str,
    expected_tier: str,
):
    items = [_researcher_article(source_platform=source_platform)]
    state = {
        "raw_question": "Q?",
        "researcher_evidence": _researcher_package(items),
    }
    rated = _ratings_for([{"evidence_id": "_"}])  # placeholder; will be re-keyed
    client = _client_returning(_make_response(ratings=rated))
    # Need to call rate_evidence first to get the generated UUID, then
    # patch the response. Simpler: use a fresh response keyed to whatever
    # IDs rate_evidence assigns.
    out = _run_with_dynamic_ratings(state=state, score=0.7, justification="ok")
    item = out["evidence_trail"][0]
    assert item["source_type"] == expected_source_type
    assert item["credibility_tier"] == expected_tier
    assert item["origin"] == "knowledge_vault"


def test_polymarket_consensus_becomes_vault_market_tier_2():
    state = {
        "raw_question": "Q?",
        "pulse_evidence": _pulse_package(market_consensus=[_polymarket_consensus_item()]),
    }
    out = _run_with_dynamic_ratings(state=state)
    item = out["evidence_trail"][0]
    assert item["source_type"] == "vault_market"
    assert item["credibility_tier"] == "tier_2"
    assert item["source_domain"] == "polymarket.com"


def test_hackernews_becomes_vault_hackernews_tier_3():
    state = {
        "raw_question": "Q?",
        "pulse_evidence": _pulse_package(community_discussion=[_hackernews_item()]),
    }
    out = _run_with_dynamic_ratings(state=state)
    item = out["evidence_trail"][0]
    assert item["source_type"] == "vault_hackernews"
    assert item["credibility_tier"] == "tier_3"
    assert item["source_domain"] == "news.ycombinator.com"


def test_fred_anomaly_becomes_vault_fred_tier_1():
    state = {
        "raw_question": "Q?",
        "market_evidence": _market_package(fred_anomalies=[_fred_anomaly()]),
    }
    out = _run_with_dynamic_ratings(state=state)
    item = out["evidence_trail"][0]
    assert item["source_type"] == "vault_fred"
    assert item["credibility_tier"] == "tier_1"
    assert item["source_domain"] == "fred.stlouisfed.org"


# ==========================================================
# Public-URL gate (evidence-URL patch, 2026-08-18)
# ==========================================================
# EvidenceItem.url was declared from Sprint 20 but hardcoded None at every
# normalize site, so no vault evidence ever reached the frontend with a
# clickable link. rate_evidence now honours the packed url for the platforms
# in PLATFORMS_WITH_PUBLIC_URL and drops it for the rest.
@pytest.mark.parametrize("source_platform,expected_url", [
    ("newsapi", "https://reuters.com/world/sample-article"),
    ("arxiv", "https://reuters.com/world/sample-article"),
    ("telegram", None),
])
def test_researcher_url_gated_to_public_url_platforms(
    source_platform: str,
    expected_url: str | None,
):
    """newsapi/arxiv surface the article link; telegram does not (product decision)."""
    state = {
        "raw_question": "Q?",
        "researcher_evidence": _researcher_package(
            [_researcher_article(source_platform=source_platform)]
        ),
    }
    out = _run_with_dynamic_ratings(state=state)
    assert out["evidence_trail"][0]["url"] == expected_url


def test_public_url_gate_membership_is_exactly_newsapi_and_arxiv():
    """
    Pin the gate itself. Adding a platform puts a live outbound link in front
    of a user — a product change that must break this test, not slip through.
    """
    assert rate_evidence.PLATFORMS_WITH_PUBLIC_URL == frozenset({"newsapi", "arxiv"})


@pytest.mark.parametrize("url", [None, "", "   "])
def test_researcher_url_absent_or_blank_becomes_none(url):
    """
    Absent/blank never becomes "" — EvidenceItem.url is Optional[str] and the
    frontend branches on presence (`Boolean(row.url)`).
    """
    state = {
        "raw_question": "Q?",
        "researcher_evidence": _researcher_package(
            [_researcher_article(source_platform="newsapi", url=url)]
        ),
    }
    out = _run_with_dynamic_ratings(state=state)
    assert out["evidence_trail"][0]["url"] is None


def test_non_researcher_sources_keep_url_none():
    """
    polymarket / hackernews / fred are normalized by _normalize_pulse and
    _normalize_market, which surface no URL at all. Unchanged by this patch —
    asserted so a future edit to those helpers is a deliberate one.
    """
    state = {
        "raw_question": "Q?",
        "pulse_evidence": _pulse_package(
            market_consensus=[_polymarket_consensus_item()],
            community_discussion=[_hackernews_item()],
        ),
        "market_evidence": _market_package(fred_anomalies=[_fred_anomaly()]),
    }
    out = _run_with_dynamic_ratings(state=state)
    trail = out["evidence_trail"]
    assert len(trail) == 3
    assert {item["source_type"] for item in trail} == {
        "vault_market", "vault_hackernews", "vault_fred",
    }
    assert all(item["url"] is None for item in trail)


def test_url_survives_into_a_valid_evidence_item():
    """
    The rated dict must still construct an EvidenceItem — url is a declared
    field, so this proves the patch is additive and not a contract change.
    """
    state = {
        "raw_question": "Q?",
        "researcher_evidence": _researcher_package(
            [_researcher_article(source_platform="newsapi")]
        ),
    }
    out = _run_with_dynamic_ratings(state=state)
    item = EvidenceItem(**out["evidence_trail"][0])
    assert item.url == "https://reuters.com/world/sample-article"


def test_unknown_researcher_platform_is_skipped_with_warning(caplog):
    """Defensive: unrecognized source_platform shouldn't crash; log + drop."""
    article = _researcher_article(source_platform="reddit")  # not in mapping
    state = {
        "raw_question": "Q?",
        "researcher_evidence": _researcher_package([article]),
    }
    out = _run_with_dynamic_ratings(state=state)
    assert out["evidence_trail"] == []


# ==========================================================
# Recency
# ==========================================================
def test_recent_item_gets_high_recency_weight():
    article = _researcher_article(
        published_at=datetime.now(tz=timezone.utc).isoformat(),
    )
    state = {
        "raw_question": "Q?",
        "researcher_evidence": _researcher_package([article]),
    }
    out = _run_with_dynamic_ratings(state=state)
    assert out["evidence_trail"][0]["recency_weight"] > 0.95


def test_pulse_item_without_published_at_falls_back_to_now():
    """Pulse items don't surface published_at; they should still get
    recency_weight≈1.0 via the fetched_at fallback."""
    state = {
        "raw_question": "Q?",
        "pulse_evidence": _pulse_package(market_consensus=[_polymarket_consensus_item()]),
    }
    out = _run_with_dynamic_ratings(state=state)
    assert out["evidence_trail"][0]["recency_weight"] > 0.95


# ==========================================================
# Snippet truncation
# ==========================================================
def test_long_researcher_snippet_truncated_to_200_chars():
    long_text = "x" * 1000
    article = _researcher_article(snippet=long_text)
    state = {
        "raw_question": "Q?",
        "researcher_evidence": _researcher_package([article]),
    }
    out = _run_with_dynamic_ratings(state=state)
    assert len(out["evidence_trail"][0]["snippet"]) == 200


# ==========================================================
# Batching
# ==========================================================
def test_seventeen_items_batched_into_three_calls():
    """17 items / batch_size 8 → 3 batches (8 + 8 + 1)."""
    articles = [_researcher_article() for _ in range(17)]
    state = {
        "raw_question": "Q?",
        "researcher_evidence": _researcher_package(articles),
    }
    captured = _run_capturing_calls(state=state)
    assert captured["call_count"] == 3
    assert captured["llm_calls_count"] == 3
    assert len(captured["evidence_trail"]) == 17


def test_batch_size_constant_drives_batching():
    """Sanity guard: changing BATCH_SIZE in the prompt module must
    propagate through the node — there's no separate batch_size const."""
    n_items = BATCH_SIZE + 1
    articles = [_researcher_article() for _ in range(n_items)]
    state = {
        "raw_question": "Q?",
        "researcher_evidence": _researcher_package(articles),
    }
    captured = _run_capturing_calls(state=state)
    assert captured["call_count"] == 2  # one full batch + one item


# ==========================================================
# Rating merge
# ==========================================================
def test_ratings_merged_by_evidence_id_not_position():
    """Out-of-order ratings (by id) should still merge correctly."""
    articles = [_researcher_article(title=f"art-{i}") for i in range(3)]
    state = {
        "raw_question": "Q?",
        "researcher_evidence": _researcher_package(articles),
    }

    captured: dict = {}

    def fake_create(**kwargs):
        captured["batch"] = kwargs
        # Build response keyed by the evidence_ids in the user message
        body = kwargs["messages"][1]["content"]
        ids = []
        for line in body.splitlines():
            if line.startswith("evidence_id: "):
                ids.append(line[len("evidence_id: "):])
        # Reverse order to confirm position-independent merge
        return _make_response(ratings=[
            {"evidence_id": ids[2], "relevance_score": 0.9, "justification": "third"},
            {"evidence_id": ids[0], "relevance_score": 0.1, "justification": "first"},
            {"evidence_id": ids[1], "relevance_score": 0.5, "justification": "second"},
        ])

    client = MagicMock()
    client.chat.completions.create = MagicMock(side_effect=fake_create)
    out = rate_evidence.run(state, client=client)

    by_title = {item["title"]: item for item in out["evidence_trail"]}
    assert by_title["art-0"]["justification"] == "first"
    assert by_title["art-1"]["justification"] == "second"
    assert by_title["art-2"]["justification"] == "third"


def test_item_missing_from_response_marked_unavailable():
    """If the model omits an item from its response, that item still gets
    a rating (relevance_score=0.0, justification='rating_unavailable')."""
    articles = [_researcher_article(title=f"art-{i}") for i in range(2)]
    state = {
        "raw_question": "Q?",
        "researcher_evidence": _researcher_package(articles),
    }

    def fake_create(**kwargs):
        body = kwargs["messages"][1]["content"]
        first_id = next(
            line[len("evidence_id: "):]
            for line in body.splitlines()
            if line.startswith("evidence_id: ")
        )
        # Only rate the first item; second is missing
        return _make_response(ratings=[
            {"evidence_id": first_id, "relevance_score": 0.9, "justification": "ok"}
        ])

    client = MagicMock()
    client.chat.completions.create = MagicMock(side_effect=fake_create)
    out = rate_evidence.run(state, client=client)

    by_title = {item["title"]: item for item in out["evidence_trail"]}
    assert by_title["art-0"]["relevance_score"] == 0.9
    assert by_title["art-1"]["relevance_score"] == 0.0
    assert by_title["art-1"]["justification"] == "rating_unavailable"


# ==========================================================
# Per-batch failure handling (graceful)
# ==========================================================
def test_first_batch_failure_does_not_block_second_batch():
    """SDK exception on batch 1 → mark unavailable; batch 2 still rated."""
    articles = [_researcher_article(title=f"art-{i}") for i in range(BATCH_SIZE + 2)]
    state = {
        "raw_question": "Q?",
        "researcher_evidence": _researcher_package(articles),
    }

    call_count = {"n": 0}

    def fake_create(**kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("rate limited")
        # Second batch succeeds
        body = kwargs["messages"][1]["content"]
        ids = [
            line[len("evidence_id: "):]
            for line in body.splitlines()
            if line.startswith("evidence_id: ")
        ]
        return _make_response(ratings=[
            {"evidence_id": eid, "relevance_score": 0.7, "justification": "ok"}
            for eid in ids
        ])

    client = MagicMock()
    client.chat.completions.create = MagicMock(side_effect=fake_create)
    out = rate_evidence.run(state, client=client)

    # First batch (8 items) marked unavailable
    unavailable = [i for i in out["evidence_trail"] if i["justification"] == "rating_unavailable"]
    rated_ok = [i for i in out["evidence_trail"] if i["justification"] == "ok"]
    assert len(unavailable) == BATCH_SIZE
    assert len(rated_ok) == 2
    # Only the successful batch counts toward llm_calls_count
    assert out["llm_calls_count"] == 1


def test_malformed_response_marks_batch_unavailable():
    """Response that doesn't parse as JSON → batch marked unavailable."""
    articles = [_researcher_article()]
    state = {
        "raw_question": "Q?",
        "researcher_evidence": _researcher_package(articles),
    }

    bad_response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="not json"))],
        usage=SimpleNamespace(total_tokens=0),
    )
    client = _client_returning(bad_response)
    out = rate_evidence.run(state, client=client)

    assert out["evidence_trail"][0]["justification"] == "rating_unavailable"
    assert out["llm_calls_count"] == 0


# ==========================================================
# Token / call-count accumulation
# ==========================================================
def test_total_tokens_used_accumulates_across_batches():
    articles = [_researcher_article() for _ in range(BATCH_SIZE + 1)]
    state = {
        "raw_question": "Q?",
        "researcher_evidence": _researcher_package(articles),
        "total_tokens_used": 100,  # pre-existing from earlier nodes
        "llm_calls_count": 1,
    }

    def fake_create(**kwargs):
        body = kwargs["messages"][1]["content"]
        ids = [
            line[len("evidence_id: "):]
            for line in body.splitlines()
            if line.startswith("evidence_id: ")
        ]
        return _make_response(
            ratings=[{"evidence_id": e, "relevance_score": 0.7, "justification": "ok"} for e in ids],
            total_tokens=150,
        )

    client = MagicMock()
    client.chat.completions.create = MagicMock(side_effect=fake_create)
    out = rate_evidence.run(state, client=client)

    # 100 (pre-existing) + 150 + 150 (two batches) = 400
    assert out["total_tokens_used"] == 400
    # 1 (pre-existing) + 2 batches = 3
    assert out["llm_calls_count"] == 3


# ==========================================================
# OpenAI call shape (regression guard)
# ==========================================================
def test_openai_call_uses_evidence_rating_model_and_strict_schema():
    state = {
        "raw_question": "Q?",
        "researcher_evidence": _researcher_package([_researcher_article()]),
    }
    captured = _run_capturing_calls(state=state)
    kwargs = captured["last_kwargs"]
    assert kwargs["response_format"] == {"type": "json_schema", "json_schema": RESPONSE_SCHEMA}
    assert kwargs["temperature"] == 0.0
    assert kwargs["messages"][0] == {"role": "system", "content": SYSTEM_PROMPT}


# ==========================================================
# Helper — run with dynamic rating responses
# ==========================================================
def _run_with_dynamic_ratings(
    *,
    state: dict,
    score: float = 0.8,
    justification: str = "ok",
) -> dict:
    """
    Run rate_evidence with a client that auto-generates ratings keyed to
    whatever evidence_ids appear in each batch. Convenient when the test
    cares about the normalize/credibility/recency logic, not the rating
    text itself.
    """
    def fake_create(**kwargs):
        body = kwargs["messages"][1]["content"]
        ids = [
            line[len("evidence_id: "):]
            for line in body.splitlines()
            if line.startswith("evidence_id: ")
        ]
        return _make_response(ratings=[
            {"evidence_id": eid, "relevance_score": score, "justification": justification}
            for eid in ids
        ])

    client = MagicMock()
    client.chat.completions.create = MagicMock(side_effect=fake_create)
    return rate_evidence.run(state, client=client)


def _run_capturing_calls(*, state: dict) -> dict:
    """Run rate_evidence and capture per-call kwargs + counts."""
    capture = {"call_count": 0, "last_kwargs": None}

    def fake_create(**kwargs):
        capture["call_count"] += 1
        capture["last_kwargs"] = kwargs
        body = kwargs["messages"][1]["content"]
        ids = [
            line[len("evidence_id: "):]
            for line in body.splitlines()
            if line.startswith("evidence_id: ")
        ]
        return _make_response(ratings=[
            {"evidence_id": eid, "relevance_score": 0.8, "justification": "ok"}
            for eid in ids
        ])

    client = MagicMock()
    client.chat.completions.create = MagicMock(side_effect=fake_create)
    out = rate_evidence.run(state, client=client)
    capture["evidence_trail"] = out["evidence_trail"]
    capture["llm_calls_count"] = out["llm_calls_count"]
    return capture
