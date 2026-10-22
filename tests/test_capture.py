from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone

import pytest

from divvyai.capture import FeedCapture

SOURCE_URL = "https://example.test/station_status.json"
RECEIVED_AT = datetime(2026, 10, 9, 14, 5, tzinfo=UTC)
RAW = b'{ "data": { "stations": [] } }\n'


@pytest.mark.parametrize("feed_name", ["station_information", "station_status"])
def test_preserves_capture_context_and_original_bytes(feed_name: str) -> None:
    source_url = f"https://example.test/{feed_name}.json"
    capture = FeedCapture(
        feed_name=feed_name,
        source_url=source_url,
        received_at=RECEIVED_AT,
        raw=RAW,
    )

    assert capture.feed_name == feed_name
    assert capture.source_url == source_url
    assert capture.received_at == RECEIVED_AT
    assert capture.raw == RAW


def test_normalizes_receipt_time_to_utc() -> None:
    local_time = datetime(2026, 10, 9, 9, 5, tzinfo=timezone(timedelta(hours=-5)))
    capture = FeedCapture(
        feed_name="station_status",
        source_url=SOURCE_URL,
        received_at=local_time,
        raw=RAW,
    )

    assert capture.received_at == RECEIVED_AT
    assert capture.received_at.tzinfo is UTC


def test_rejects_receipt_time_without_timezone() -> None:
    with pytest.raises(ValueError, match="received_at must be timezone-aware"):
        FeedCapture(
            feed_name="station_status",
            source_url=SOURCE_URL,
            received_at=datetime(2026, 10, 9, 14, 5),
            raw=RAW,
        )


@pytest.mark.parametrize("feed_name", ["gbfs", "station_status/other"])
def test_rejects_unsupported_feed_name(feed_name: str) -> None:
    with pytest.raises(ValueError, match="Unsupported station feed"):
        FeedCapture(
            feed_name=feed_name,
            source_url=SOURCE_URL,
            received_at=RECEIVED_AT,
            raw=RAW,
        )


@pytest.mark.parametrize("source_url", ["", "   "])
def test_rejects_blank_source_url(source_url: str) -> None:
    with pytest.raises(ValueError, match="source_url must not be blank"):
        FeedCapture(
            feed_name="station_status",
            source_url=source_url,
            received_at=RECEIVED_AT,
            raw=RAW,
        )


def test_keeps_malformed_response_for_later_archival() -> None:
    capture = FeedCapture(
        feed_name="station_status",
        source_url=SOURCE_URL,
        received_at=RECEIVED_AT,
        raw=b"not-json",
    )

    assert capture.raw == b"not-json"


def test_prevents_receipt_time_from_being_rewritten() -> None:
    capture = FeedCapture(
        feed_name="station_status",
        source_url=SOURCE_URL,
        received_at=RECEIVED_AT,
        raw=RAW,
    )
    later = RECEIVED_AT + timedelta(minutes=1)

    with pytest.raises(FrozenInstanceError):
        capture.received_at = later  # type: ignore[misc]
