from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from divvyai.capture import FeedCapture
from divvyai.identity import identify_capture

INFORMATION = FeedCapture(
    feed_name="station_information",
    source_url="https://example.test/station_information.json",
    received_at=datetime(2026, 10, 9, 14, 5, tzinfo=UTC),
    raw=b'{ "data": { "stations": [] } }\n',
)
STATUS = FeedCapture(
    feed_name="station_status",
    source_url="https://example.test/station_status.json",
    received_at=datetime(2026, 10, 9, 14, 5, 2, tzinfo=UTC),
    raw=b'{"data":{"stations":[]}}\n',
)


def test_reconstructed_capture_has_the_same_identity() -> None:
    original = identify_capture(INFORMATION, STATUS)
    reconstructed = identify_capture(replace(INFORMATION), replace(STATUS))

    assert reconstructed == original
    assert len(original.capture_id) == 64
    assert set(original.capture_id) <= set("0123456789abcdef")


def test_archive_name_contains_utc_date_hour_and_capture_id() -> None:
    identity = identify_capture(INFORMATION, STATUS)

    assert identity.object_name == (
        f"raw/date=2026-10-09/hour=14/{identity.capture_id}.zip"
    )


@pytest.mark.parametrize("feed_name", ["station_information", "station_status"])
@pytest.mark.parametrize("field", ["raw", "source_url", "received_at"])
def test_changing_either_feeds_body_or_metadata_changes_id(
    feed_name: str, field: str
) -> None:
    original = INFORMATION if feed_name == "station_information" else STATUS
    if field == "raw":
        changed = replace(original, raw=original.raw + b"\n")
    elif field == "source_url":
        changed = replace(original, source_url="https://other.test/feed.json")
    else:
        changed = replace(
            original, received_at=original.received_at + timedelta(seconds=1)
        )

    information = changed if feed_name == "station_information" else INFORMATION
    status = changed if feed_name == "station_status" else STATUS

    assert (
        identify_capture(information, status).capture_id
        != identify_capture(INFORMATION, STATUS).capture_id
    )


@pytest.mark.parametrize(
    ("information_time", "status_time", "expected_prefix"),
    [
        (
            datetime(2026, 10, 9, 14, 59, 59, tzinfo=UTC),
            datetime(2026, 10, 9, 15, 0, 1, tzinfo=UTC),
            "raw/date=2026-10-09/hour=15/",
        ),
        (
            datetime(2026, 10, 10, 0, 0, 1, tzinfo=UTC),
            datetime(2026, 10, 9, 23, 59, 59, tzinfo=UTC),
            "raw/date=2026-10-10/hour=00/",
        ),
    ],
)
def test_archive_uses_latest_receipt_when_captures_cross_a_boundary(
    information_time: datetime, status_time: datetime, expected_prefix: str
) -> None:
    information = replace(INFORMATION, received_at=information_time)
    status = replace(STATUS, received_at=status_time)

    assert identify_capture(information, status).object_name.startswith(expected_prefix)


def test_equivalent_timezone_representation_keeps_same_identity() -> None:
    offset = timezone(timedelta(hours=-5))
    local_information = replace(
        INFORMATION, received_at=INFORMATION.received_at.astimezone(offset)
    )
    local_status = replace(STATUS, received_at=STATUS.received_at.astimezone(offset))

    assert identify_capture(local_information, local_status) == identify_capture(
        INFORMATION, STATUS
    )


@pytest.mark.parametrize(
    ("information", "status"),
    [(STATUS, STATUS), (INFORMATION, INFORMATION), (STATUS, INFORMATION)],
)
def test_rejects_duplicated_or_swapped_feeds(
    information: FeedCapture, status: FeedCapture
) -> None:
    with pytest.raises(ValueError, match="Expected station_information"):
        identify_capture(information, status)


def test_can_identify_a_capture_with_a_malformed_response() -> None:
    malformed_status = replace(STATUS, raw=b"not-json\n")

    identity = identify_capture(INFORMATION, malformed_status)

    assert len(identity.capture_id) == 64
    assert identity.capture_id != identify_capture(INFORMATION, STATUS).capture_id
