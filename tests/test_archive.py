import json
from datetime import UTC, datetime
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from divvyai.archive import build_capture_zip, read_capture_zip
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


def test_zip_preserves_original_bytes_and_contains_only_expected_files() -> None:
    result = build_capture_zip(INFORMATION, STATUS)

    with ZipFile(BytesIO(result)) as archive:
        assert sorted(archive.namelist()) == [
            "manifest.json",
            "station_information.json",
            "station_status.json",
        ]
        assert archive.read("station_information.json") == INFORMATION.raw
        assert archive.read("station_status.json") == STATUS.raw
        assert all(entry.compress_type == ZIP_DEFLATED for entry in archive.infolist())


def test_manifest_records_each_feeds_source_and_receipt_time() -> None:
    result = build_capture_zip(INFORMATION, STATUS)

    with ZipFile(BytesIO(result)) as archive:
        manifest = json.loads(archive.read("manifest.json"))

    assert manifest == {
        "format_version": 1,
        "feeds": [
            {
                "feed_name": "station_information",
                "file_name": "station_information.json",
                "source_url": "https://example.test/station_information.json",
                "received_at": "2026-10-09T14:05:00+00:00",
            },
            {
                "feed_name": "station_status",
                "file_name": "station_status.json",
                "source_url": "https://example.test/station_status.json",
                "received_at": "2026-10-09T14:05:02+00:00",
            },
        ],
    }


@pytest.mark.parametrize(
    ("information", "status"),
    [(STATUS, STATUS), (INFORMATION, INFORMATION), (STATUS, INFORMATION)],
)
def test_rejects_duplicated_or_swapped_feeds(
    information: FeedCapture, status: FeedCapture
) -> None:
    with pytest.raises(ValueError, match="Expected station_"):
        build_capture_zip(information, status)


def test_archives_malformed_response_without_parsing_it() -> None:
    malformed_status = FeedCapture(
        feed_name="station_status",
        source_url=STATUS.source_url,
        received_at=STATUS.received_at,
        raw=b"not-json\n",
    )
    result = build_capture_zip(INFORMATION, malformed_status)

    with ZipFile(BytesIO(result)) as archive:
        assert archive.read("station_status.json") == b"not-json\n"


def test_reader_restores_original_captures_and_identity() -> None:
    restored = read_capture_zip(build_capture_zip(INFORMATION, STATUS))

    assert restored == (INFORMATION, STATUS)
    assert identify_capture(*restored) == identify_capture(INFORMATION, STATUS)


def test_reader_preserves_malformed_feed_bytes() -> None:
    malformed_status = FeedCapture(
        feed_name="station_status",
        source_url=STATUS.source_url,
        received_at=STATUS.received_at,
        raw=b"not-json\n",
    )

    restored_information, restored_status = read_capture_zip(
        build_capture_zip(INFORMATION, malformed_status)
    )

    assert restored_information == INFORMATION
    assert restored_status == malformed_status


def test_reader_rejects_unsupported_manifest_version() -> None:
    original = build_capture_zip(INFORMATION, STATUS)
    buffer = BytesIO()

    with ZipFile(BytesIO(original)) as source, ZipFile(buffer, "w") as changed:
        for name in source.namelist():
            contents = source.read(name)
            if name == "manifest.json":
                manifest = json.loads(contents)
                manifest["format_version"] = 2
                contents = json.dumps(manifest).encode("utf-8")
            changed.writestr(name, contents)

    with pytest.raises(ValueError, match="Unsupported archive format"):
        read_capture_zip(buffer.getvalue())
