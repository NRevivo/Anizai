"""
Gate 2 — the Polymarket HTTP client's pagination and retry behaviour.

`requests.get` is stubbed; no network. The pagination tests exist because of
a real defect found during the first live run on 2026-07-25: Gamma caps
`limit` server-side (asking for 500 returns 100), and the original loop
treated a short page as the last page — so discovery silently examined 100
markets out of thousands and reported "0 candidates" as though it had looked
at everything.
"""

from __future__ import annotations

import pytest

from calibration.polymarket import client


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 500:
            raise RuntimeError(f"server error {self.status_code}")


def _market(i: int) -> dict:
    return {"conditionId": f"0x{i:04d}", "question": f"Q{i}?", "slug": f"q-{i}"}


@pytest.fixture
def capture_requests(monkeypatch):
    """Stub requests.get and record the params of every call."""
    calls: list[dict] = []

    def _install(page_fn):
        def fake_get(url, params=None, timeout=None):
            calls.append(dict(params or {}))
            return _FakeResponse(page_fn(params or {}))

        monkeypatch.setattr(client.requests, "get", fake_get)
        return calls

    return _install


# ==========================================================
# Pagination — the server-side limit cap
# ==========================================================

def test_offset_advances_by_records_received_not_records_requested(capture_requests):
    """
    The defect this guards. Gamma returns 100 when asked for 500. Advancing
    the offset by 500 would skip four out of every five markets.
    """
    total = 250
    server_page_size = 100

    def pages(params):
        offset = int(params.get("offset", 0))
        return [_market(i) for i in range(offset, min(offset + server_page_size, total))]

    calls = capture_requests(pages)
    collected = client.fetch_active_markets_paged(max_markets=1_000)

    assert len(collected) == total
    assert [c["offset"] for c in calls] == [0, 100, 200, 250]


def test_a_short_page_is_not_treated_as_the_last_page(capture_requests):
    """Every page is short relative to what we ask for. Only empty means done."""
    def pages(params):
        offset = int(params.get("offset", 0))
        return [_market(i) for i in range(offset, min(offset + 10, 30))]

    capture_requests(pages)
    assert len(client.fetch_active_markets_paged(max_markets=1_000)) == 30


def test_walk_stops_on_an_empty_page(capture_requests):
    def pages(params):
        return [] if int(params.get("offset", 0)) >= 100 else [_market(i) for i in range(100)]

    capture_requests(pages)
    assert len(client.fetch_active_markets_paged(max_markets=1_000)) == 100


# ==========================================================
# Pagination — defensive against a broken endpoint
# ==========================================================

def test_an_endpoint_that_ignores_offset_does_not_loop_forever(capture_requests):
    """
    Without the seen-ids guard this would spin until max_markets, collecting
    the same page over and over and reporting a market count that is pure
    fiction.
    """
    capture_requests(lambda _params: [_market(i) for i in range(50)])

    collected = client.fetch_active_markets_paged(max_markets=10_000)
    assert len(collected) == 50


def test_duplicate_markets_across_pages_are_deduplicated(capture_requests):
    def pages(params):
        offset = int(params.get("offset", 0))
        if offset == 0:
            return [_market(i) for i in range(10)]
        if offset == 10:
            return [_market(i) for i in range(5, 15)]   # 5 overlap
        return []

    capture_requests(pages)
    collected = client.fetch_active_markets_paged(max_markets=1_000)

    condition_ids = [m["conditionId"] for m in collected]
    assert len(condition_ids) == len(set(condition_ids)) == 15


def test_max_markets_cap_is_respected_and_warned_about(capture_requests, caplog):
    """A silent truncation reads as full coverage. It must be logged."""
    def pages(params):
        offset = int(params.get("offset", 0))
        return [_market(i) for i in range(offset, offset + 100)]

    capture_requests(pages)
    with caplog.at_level("WARNING"):
        collected = client.fetch_active_markets_paged(max_markets=150)

    assert len(collected) == 150
    assert any("truncated" in r.message.lower() for r in caplog.records)


def test_active_and_unclosed_filters_are_always_sent(capture_requests):
    calls = capture_requests(lambda _p: [])
    client.fetch_active_markets_paged()
    assert calls[0]["active"] == "true"
    assert calls[0]["closed"] == "false"


def test_include_tag_is_always_sent(capture_requests):
    """
    Without it Gamma omits the `tags` key entirely (verified live 2026-07-25)
    and every market fails categorisation — discovery then reports having
    examined thousands of markets and found nothing, which looks like a
    genuine result rather than a broken request.
    """
    calls = capture_requests(lambda _p: [])
    client.fetch_active_markets_paged()
    assert calls[0]["include_tag"] == "true"


def test_slug_lookup_also_requests_tags(monkeypatch):
    """Manual-add validates the category too, so it needs the same field."""
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured.update(params or {})
        return _FakeResponse([_market(1)])

    monkeypatch.setattr(client.requests, "get", fake_get)
    client.fetch_market_by_slug("some-slug")
    assert captured["include_tag"] == "true"


# ==========================================================
# Response-shape tolerance
# ==========================================================

def test_a_wrapped_data_envelope_is_unwrapped(monkeypatch):
    monkeypatch.setattr(
        client.requests, "get",
        lambda *a, **k: _FakeResponse({"data": [_market(1), _market(2)]}),
    )
    assert len(client.fetch_active_markets()) == 2


def test_fetch_market_by_slug_returns_none_when_nothing_matches(monkeypatch):
    """None lets the caller name the slug instead of surfacing an IndexError."""
    monkeypatch.setattr(client.requests, "get", lambda *a, **k: _FakeResponse([]))
    assert client.fetch_market_by_slug("nope") is None


def test_fetch_market_by_slug_rejects_an_empty_slug():
    with pytest.raises(ValueError):
        client.fetch_market_by_slug("")


# ==========================================================
# Retry policy
# ==========================================================

def test_4xx_is_not_retried(monkeypatch):
    """
    A 404 on a condition id means the market is gone. Retrying just delays
    the correct conclusion.
    """
    calls = {"n": 0}

    def fake_get(*_a, **_k):
        calls["n"] += 1
        return _FakeResponse({"error": "not found"}, status_code=404)

    monkeypatch.setattr(client.requests, "get", fake_get)
    with pytest.raises(client.PolymarketError, match="404"):
        client.fetch_active_markets()
    assert calls["n"] == 1


def test_clob_404_is_absence_not_an_error(monkeypatch):
    """A market CLOB no longer knows about has not resolved — it is just gone."""
    monkeypatch.setattr(
        client.requests, "get",
        lambda *a, **k: _FakeResponse({"error": "not found"}, status_code=404),
    )
    assert client.fetch_clob_market("0xgone") is None


def test_transient_failures_are_retried_then_succeed(monkeypatch):
    monkeypatch.setattr(client.time, "sleep", lambda _s: None)
    attempts = {"n": 0}

    def fake_get(*_a, **_k):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise ConnectionError("connection reset")
        return _FakeResponse([_market(1)])

    monkeypatch.setattr(client.requests, "get", fake_get)
    assert len(client.fetch_active_markets()) == 1
    assert attempts["n"] == 3


def test_persistent_failure_raises_after_the_final_attempt(monkeypatch):
    monkeypatch.setattr(client.time, "sleep", lambda _s: None)

    def fake_get(*_a, **_k):
        raise ConnectionError("down")

    monkeypatch.setattr(client.requests, "get", fake_get)
    with pytest.raises(client.PolymarketError, match="after 3 attempts"):
        client.fetch_active_markets()


def test_clob_requires_a_condition_id():
    with pytest.raises(ValueError):
        client.fetch_clob_market("")


# ==========================================================
# The offset ceiling
# ==========================================================

def test_offset_ceiling_ends_the_walk_and_keeps_what_was_collected(
    capture_requests, caplog
):
    """
    Gamma refuses offsets past ~2000 with a 422. Measured live 2026-07-25:
    offset=2000 succeeds, offset=3000 does not. Crashing there would discard
    every page already collected.
    """
    def pages(params):
        offset = int(params.get("offset", 0))
        if offset >= 200:
            return {"__422__": "offset too large, use /markets/keyset"}
        return [_market(i) for i in range(offset, offset + 100)]

    def fake_get(url, params=None, timeout=None):
        payload = pages(params or {})
        if isinstance(payload, dict) and "__422__" in payload:
            return _FakeResponse({"error": payload["__422__"]}, status_code=422)
        return _FakeResponse(payload)

    import calibration.polymarket.client as mod

    original = mod.requests.get
    mod.requests.get = fake_get
    try:
        with caplog.at_level("WARNING"):
            collected = client.fetch_active_markets_paged(max_markets=10_000)
    finally:
        mod.requests.get = original

    assert len(collected) == 200
    assert any("offset ceiling" in r.message for r in caplog.records)


def test_a_non_ceiling_4xx_still_propagates(monkeypatch):
    """Only the offset-ceiling 422 is swallowed; other 4xx are real errors."""
    monkeypatch.setattr(
        client.requests, "get",
        lambda *a, **k: _FakeResponse({"error": "bad request"}, status_code=400),
    )
    with pytest.raises(client.PolymarketError):
        client.fetch_active_markets_paged()


# ==========================================================
# Windowed fetch — how discovery actually queries
# ==========================================================

def test_window_fetch_pushes_every_filter_to_the_server(capture_requests):
    """
    Server-side filtering is not an optimisation here. The offset ceiling
    means an unfiltered scan can never reach the whole exchange, so anything
    the server can filter, it must.
    """
    calls = capture_requests(lambda _p: [])
    client.fetch_markets_in_window(
        end_date_min="2026-08-01T00:00:00Z",
        end_date_max="2026-08-05T23:59:59Z",
        volume_min=50_000,
    )
    sent = calls[0]
    assert sent["end_date_min"] == "2026-08-01T00:00:00Z"
    assert sent["end_date_max"] == "2026-08-05T23:59:59Z"
    assert sent["volume_num_min"] == 50_000
    assert sent["order"] == "volumeNum"
    assert sent["ascending"] == "false"
    assert sent["include_tag"] == "true"


def test_window_fetch_paginates_within_the_window(capture_requests):
    def pages(params):
        offset = int(params.get("offset", 0))
        return [_market(i) for i in range(offset, min(offset + 100, 150))]

    capture_requests(pages)
    collected = client.fetch_markets_in_window(
        end_date_min="a", end_date_max="b", volume_min=0, max_markets=500
    )
    assert len(collected) == 150


def test_window_fetch_respects_its_own_cap(capture_requests):
    capture_requests(lambda p: [_market(i) for i in range(int(p.get("offset", 0)),
                                                         int(p.get("offset", 0)) + 100)])
    collected = client.fetch_markets_in_window(
        end_date_min="a", end_date_max="b", volume_min=0, max_markets=120
    )
    assert len(collected) == 120
