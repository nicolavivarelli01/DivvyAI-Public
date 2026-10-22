import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from divvyai.capture import FeedCapture
from divvyai.identity import identify_capture
from divvyai.warehouse import build_raw_rows, serialize_raw_rows

FIXTURES = Path(__file__).parent / "fixtures"
INFORMATION = FeedCapture(
    feed_name="station_information",
    source_url="https://example.test/station_information.json",
    received_at=datetime(2026, 10, 9, 14, 5, tzinfo=UTC),
    raw=(FIXTURES / "station_information.json").read_bytes(),
)
STATUS = FeedCapture(
    feed_name="station_status",
    source_url="https://example.test/station_status.json",
    received_at=datetime(2026, 10, 9, 14, 5, 2, tzinfo=UTC),
    raw=(FIXTURES / "station_status.json").read_bytes(),
)
EMPTY = b'{"data":{"stations":[]}}'


def test_rows_preserve_station_fields_header_and_provenance() -> None:
    rows = build_raw_rows(INFORMATION, STATUS)
    identity = identify_capture(INFORMATION, STATUS)

    assert [(row["feed_name"], row["station_id"]) for row in rows] == [
        ("station_information", "001"),
        ("station_information", "002"),
        ("station_status", "001"),
        ("station_status", "002"),
    ]
    assert rows[0] == {
        "capture_id": identity.capture_id,
        "archive_object_name": identity.object_name,
        "feed_name": "station_information",
        "source_url": "https://example.test/station_information.json",
        "received_at": "2026-10-09T14:05:00+00:00",
        "station_id": "001",
        "feed_metadata": {"last_updated": 1791503940, "ttl": 86400, "version": "1.1"},
        "payload": {
            "station_id": "001",
            "name": "Synthetic station",
            "lat": 41.88,
            "lon": -87.63,
            "capacity": 10,
        },
    }
    assert {row["capture_id"] for row in rows} == {identity.capture_id}
    assert {row["archive_object_name"] for row in rows} == {identity.object_name}
    assert rows[2]["received_at"] == "2026-10-09T14:05:02+00:00"
    assert rows[2]["feed_metadata"] == {
        "last_updated": 1791504000,
        "ttl": 60,
        "version": "1.1",
    }
    assert rows[3]["payload"]["num_bikes_available"] == 0
    assert rows[3]["payload"]["last_reported"] == 1791503980


def test_two_empty_feeds_produce_no_station_rows() -> None:
    assert (
        build_raw_rows(replace(INFORMATION, raw=EMPTY), replace(STATUS, raw=EMPTY))
        == []
    )


@pytest.mark.parametrize(
    ("empty_feed", "expected_feeds"),
    [
        ("station_information", ["station_status", "station_status"]),
        ("station_status", ["station_information", "station_information"]),
    ],
)
def test_empty_feed_does_not_discard_other_feeds_rows(
    empty_feed: str, expected_feeds: list[str]
) -> None:
    information = (
        replace(INFORMATION, raw=EMPTY)
        if empty_feed == "station_information"
        else INFORMATION
    )
    status = replace(STATUS, raw=EMPTY) if empty_feed == "station_status" else STATUS

    assert [row["feed_name"] for row in build_raw_rows(information, status)] == (
        expected_feeds
    )


@pytest.mark.parametrize("feed_name", ["station_information", "station_status"])
@pytest.mark.parametrize(
    "raw",
    [
        b"not-json",
        b"\xff",
        b'{"data":{}}',
        b'{"data":{"stations":[{"station_id":"001"},{"station_id":"001"}]}}',
    ],
)
def test_invalid_feed_prevents_returning_a_batch(feed_name: str, raw: bytes) -> None:
    information = (
        replace(INFORMATION, raw=raw)
        if feed_name == "station_information"
        else INFORMATION
    )
    status = replace(STATUS, raw=raw) if feed_name == "station_status" else STATUS

    with pytest.raises(ValueError):
        build_raw_rows(information, status)


def test_nested_source_fields_cannot_overwrite_row_provenance() -> None:
    payload = {
        "station_id": "001",
        "capture_id": "upstream-value",
        "received_at": "upstream-time",
        "extra": {"labels": ["synthetic"]},
    }
    document = {
        "publisher_note": {"text": "synthetic"},
        "data": {"stations": [payload]},
    }
    status = replace(STATUS, raw=json.dumps(document).encode("utf-8"))
    row = build_raw_rows(INFORMATION, status)[-1]

    assert row["capture_id"] == identify_capture(INFORMATION, status).capture_id
    assert row["received_at"] == "2026-10-09T14:05:02+00:00"
    assert row["payload"] == payload
    assert row["feed_metadata"] == {"publisher_note": {"text": "synthetic"}}


def test_reconstructed_capture_produces_the_same_rows() -> None:
    assert build_raw_rows(replace(INFORMATION), replace(STATUS)) == build_raw_rows(
        INFORMATION, STATUS
    )


def test_missing_header_metadata_stays_missing() -> None:
    status = replace(STATUS, raw=b'{"data":{"stations":[{"station_id":"001"}]}}')

    assert build_raw_rows(INFORMATION, status)[-1]["feed_metadata"] == {}


def test_swapped_feeds_are_rejected() -> None:
    with pytest.raises(ValueError, match="Expected station_information"):
        build_raw_rows(STATUS, INFORMATION)


def test_serialization_produces_one_complete_json_object_per_line() -> None:
    rows = build_raw_rows(INFORMATION, STATUS)

    result = serialize_raw_rows(tuple(rows))

    assert result.endswith(b"\n")
    assert result.count(b"\n") == 4
    assert [json.loads(line) for line in result.splitlines()] == rows


def test_serialization_of_empty_batch_is_empty_bytes() -> None:
    assert serialize_raw_rows([]) == b""


def test_serialization_preserves_unicode_and_escapes_embedded_newlines() -> None:
    row = build_raw_rows(INFORMATION, STATUS)[0]
    row["payload"]["name"] = 'Café "North"\nStation'
    row["payload"]["extra"] = {"synthetic": True, "note": None, "tags": ["sample"]}
    row["feed_metadata"]["note"] = "🚲\nSynthetic"

    result = serialize_raw_rows([row])

    assert result.count(b"\n") == 1
    assert b"\\n" in result
    assert "Café".encode() in result
    assert "🚲".encode() in result
    assert json.loads(result) == row


def test_serialization_is_stable_when_json_field_order_changes() -> None:
    row = build_raw_rows(INFORMATION, STATUS)[0]
    reordered = row.copy()
    reordered["payload"] = dict(reversed(list(row["payload"].items())))
    reordered["feed_metadata"] = dict(reversed(list(row["feed_metadata"].items())))

    assert serialize_raw_rows([row]) == serialize_raw_rows([reordered])


@pytest.mark.parametrize("field", ["payload", "feed_metadata"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_serialization_rejects_nonfinite_numbers(field: str, value: float) -> None:
    rows = build_raw_rows(INFORMATION, STATUS)
    if field == "payload":
        rows[-1]["payload"]["bad_number"] = value
    else:
        rows[-1]["feed_metadata"]["bad_number"] = value

    with pytest.raises(ValueError):
        serialize_raw_rows(rows)


def test_serialization_rejects_values_that_are_not_json_serializable() -> None:
    rows = build_raw_rows(INFORMATION, STATUS)
    rows[-1]["payload"]["bad_value"] = datetime(2026, 10, 9, tzinfo=UTC)

    with pytest.raises(TypeError):
        serialize_raw_rows(rows)
