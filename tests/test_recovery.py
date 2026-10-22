import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from google.cloud.bigquery import Client, LoadJob

from divvyai.archive import build_capture_zip
from divvyai.capture import FeedCapture
from divvyai.identity import identify_capture
from divvyai.recovery import recover_archive

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
ARCHIVE = build_capture_zip(INFORMATION, STATUS)
IDENTITY = identify_capture(INFORMATION, STATUS)
TABLE_ID = "test-project.raw.station_records"
JOB_KEY = f"{TABLE_ID}\n{IDENTITY.capture_id}"
JOB_ID = f"divvyai_cloud_raw_v1_{sha256(JOB_KEY.encode('utf-8')).hexdigest()}"
LOCATION = "us-central1"
NOW = datetime(2026, 10, 13, tzinfo=UTC)


@pytest.fixture
def cloud_calls() -> Iterator[tuple[Mock, Mock, Mock]]:
    client = Mock(spec=Client)
    client.project = "test-project"
    with (
        patch("divvyai.recovery.get_existing_load") as lookup,
        patch("divvyai.recovery.load_raw_jsonl") as load,
    ):
        lookup.return_value = None
        load.return_value = Mock(spec=LoadJob)
        yield client, lookup, load


def test_completed_import_is_reused(cloud_calls: tuple[Mock, Mock, Mock]) -> None:
    client, lookup, load = cloud_calls
    existing = Mock(spec=LoadJob)
    lookup.return_value = existing

    result = recover_archive(
        client, TABLE_ID, ARCHIVE, job_id=JOB_ID, location=LOCATION, now=NOW
    )

    assert result is existing
    lookup.assert_called_once_with(client, TABLE_ID, job_id=JOB_ID, location=LOCATION)
    load.assert_not_called()


def test_missing_import_loads_original_capture(
    cloud_calls: tuple[Mock, Mock, Mock],
) -> None:
    client, lookup, load = cloud_calls

    result = recover_archive(
        client, TABLE_ID, ARCHIVE, job_id=JOB_ID, location=LOCATION, now=NOW
    )

    assert result is load.return_value
    lookup.assert_called_once()
    load.assert_called_once()
    args, kwargs = load.call_args
    assert args[:2] == (client, TABLE_ID)
    assert kwargs == {"job_id": JOB_ID, "location": LOCATION}
    rows = [json.loads(line) for line in args[2].splitlines()]
    assert len(rows) == 4
    assert {row["capture_id"] for row in rows} == {IDENTITY.capture_id}
    assert {row["archive_object_name"] for row in rows} == {IDENTITY.object_name}
    for capture in (INFORMATION, STATUS):
        feed_rows = [row for row in rows if row["feed_name"] == capture.feed_name]
        assert [row["payload"] for row in feed_rows] == json.loads(capture.raw)["data"][
            "stations"
        ]
        assert {row["received_at"] for row in feed_rows} == {
            capture.received_at.isoformat()
        }


def test_wrong_original_job_id_fails_before_cloud_calls(
    cloud_calls: tuple[Mock, Mock, Mock],
) -> None:
    client, lookup, load = cloud_calls

    with pytest.raises(ValueError, match="Original job ID does not match"):
        recover_archive(
            client, TABLE_ID, ARCHIVE, job_id="another-job", location=LOCATION, now=NOW
        )

    lookup.assert_not_called()
    load.assert_not_called()


def test_wrong_project_fails_before_cloud_calls(
    cloud_calls: tuple[Mock, Mock, Mock],
) -> None:
    client, lookup, load = cloud_calls
    client.project = "another-project"

    with pytest.raises(ValueError, match="must use the same project"):
        recover_archive(
            client, TABLE_ID, ARCHIVE, job_id=JOB_ID, location=LOCATION, now=NOW
        )

    lookup.assert_not_called()
    load.assert_not_called()


@pytest.mark.parametrize(
    "now",
    [
        STATUS.received_at + timedelta(days=91),
        INFORMATION.received_at - timedelta(seconds=1),
    ],
)
def test_missing_job_outside_recovery_window_is_not_loaded(
    cloud_calls: tuple[Mock, Mock, Mock], now: datetime
) -> None:
    client, _lookup, load = cloud_calls

    with pytest.raises(ValueError, match="last 90 days"):
        recover_archive(
            client, TABLE_ID, ARCHIVE, job_id=JOB_ID, location=LOCATION, now=now
        )

    load.assert_not_called()


def test_uncertain_lookup_does_not_start_an_import(
    cloud_calls: tuple[Mock, Mock, Mock],
) -> None:
    client, lookup, load = cloud_calls
    lookup.side_effect = TimeoutError("Existing import is still running")

    with pytest.raises(TimeoutError):
        recover_archive(
            client, TABLE_ID, ARCHIVE, job_id=JOB_ID, location=LOCATION, now=NOW
        )

    load.assert_not_called()
