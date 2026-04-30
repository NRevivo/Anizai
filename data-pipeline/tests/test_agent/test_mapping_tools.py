"""
Gate 1 unit tests for agent/tools/mapping_tools.py (Sprint 19 T19.1).

Sprint 19 uses lookup_by_canonical only (find_similar_and_link is Phase-7
deferred — audit §4 D-5).
"""

from unittest.mock import patch

from agent.tools import mapping_tools


def test_lookup_by_canonical_delegates_with_platform():
    expected = [
        {"mapping_id": "x", "platform": "polymarket", "platform_specific_id": "slug-1"},
    ]
    with patch.object(
        mapping_tools.mapping_dict,
        "lookup_by_canonical",
        return_value=expected,
    ) as mock_lookup:
        result = mapping_tools.lookup_by_canonical(
            "evt-123", platform="polymarket"
        )

    mock_lookup.assert_called_once_with(
        canonical_event_id="evt-123",
        platform="polymarket",
        limit=100,
    )
    assert result is expected


def test_lookup_by_canonical_default_platform_none():
    with patch.object(
        mapping_tools.mapping_dict,
        "lookup_by_canonical",
        return_value=[],
    ) as mock_lookup:
        mapping_tools.lookup_by_canonical("evt-456")

    mock_lookup.assert_called_once_with(
        canonical_event_id="evt-456",
        platform=None,
        limit=100,
    )


def test_lookup_by_canonical_returns_empty_when_unlinked():
    """Pre-Phase-7, most canonical_event_ids have no mapping rows."""
    with patch.object(
        mapping_tools.mapping_dict,
        "lookup_by_canonical",
        return_value=[],
    ):
        rows = mapping_tools.lookup_by_canonical("evt-no-links")
    assert rows == []


def test_lookup_by_canonical_passes_custom_limit():
    with patch.object(
        mapping_tools.mapping_dict,
        "lookup_by_canonical",
        return_value=[],
    ) as mock_lookup:
        mapping_tools.lookup_by_canonical("evt-1", limit=25)

    _, kwargs = mock_lookup.call_args
    assert kwargs["limit"] == 25
