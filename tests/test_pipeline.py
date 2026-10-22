import json
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, patch
from zipfile import ZipFile

import pytest
from google.api_core.exceptions import Conflict, PreconditionFailed
from google.cloud.bigquery import Client as BigQueryClient
from google.cloud.bigquery import LoadJob
from google.cloud.storage import Client as StorageClient

from divvyai.capture import FeedCapture
from divvyai.identity import identify_capture
from divvyai.pipeline import write_cloud_capture

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
STORAGE_CLIENT = Mock(spec=StorageClient)
BIGQUERY_CLIENT = Mock(spec=BigQueryClient)
BUCKET_NAME = "test-bucket"
TABLE_ID = "test-project.raw.station_records"
JOB_ID = "test-capture-load"


@pytest.fixture
def cloud_calls() -> Iterator[tuple[Mock, Mock]]:
    with (
        patch("divvyai.pipeline.upload_archive") as upload,
        patch("divvyai.pipeline.load_raw_jsonl") as load,
    ):
        load.return_value = Mock(spec=LoadJob)
        yield upload, load


def run_capture(
    information: FeedCapture = INFORMATION,
    status: FeedCapture = STATUS,
    *,
    job_id: str = JOB_ID,
) -> LoadJob:
    return write_cloud_capture(
        information,
        status,
        storage_client=STORAGE_CLIENT,
        bigquery_client=BIGQUERY_CLIENT,
        bucket_name=BUCKET_NAME,
        table_id=TABLE_ID,
        job_id=job_id,
        location="us-central1",
    )


def test_archives_original_feeds_before_loading_matching_rows(
    cloud_calls: tuple[Mock, Mock],
) -> None:
    upload, load = cloud_calls
    events = Mock()
    events.attach_mock(upload, "upload")
    events.attach_mock(load, "load")

    result = run_capture()

    assert [event[0] for event in events.mock_calls] == ["upload", "load"]
    assert result is load.return_value
    identity = identify_capture(INFORMATION, STATUS)
    storage, bucket, uploaded_identity, archive_bytes = upload.call_args.args
    assert storage is STORAGE_CLIENT
    assert bucket == BUCKET_NAME
    assert uploaded_identity == identity
    with ZipFile(BytesIO(archive_bytes)) as archive:
        assert archive.read("station_information.json") == INFORMATION.raw
        assert archive.read("station_status.json") == STATUS.raw
        manifest = json.loads(archive.read("manifest.json"))
    assert [feed["received_at"] for feed in manifest["feeds"]] == [
        INFORMATION.received_at.isoformat(),
        STATUS.received_at.isoformat(),
    ]

    bigquery, table, jsonl = load.call_args.args
    assert bigquery is BIGQUERY_CLIENT
    assert table == TABLE_ID
    assert load.call_args.kwargs == {"job_id": JOB_ID, "location": "us-central1"}
    rows = [json.loads(line) for line in jsonl.splitlines()]
    assert len(rows) == 4
    assert {row["capture_id"] for row in rows} == {identity.capture_id}
    assert {row["archive_object_name"] for row in rows} == {identity.object_name}
    for capture in (INFORMATION, STATUS):
        feed_rows = [row for row in rows if row["feed_name"] == capture.feed_name]
        expected_stations = json.loads(capture.raw)["data"]["stations"]
        assert [row["payload"] for row in feed_rows] == expected_stations
        assert {row["received_at"] for row in feed_rows} == {
            capture.received_at.isoformat()
        }
        assert {row["source_url"] for row in feed_rows} == {capture.source_url}


@pytest.mark.parametrize("job_id", ["", " \t"])
def test_blank_job_id_fails_before_cloud_calls(
    cloud_calls: tuple[Mock, Mock], job_id: str
) -> None:
    upload, load = cloud_calls

    with pytest.raises(ValueError, match="job_id must not be blank"):
        run_capture(job_id=job_id)

    upload.assert_not_called()
    load.assert_not_called()


def test_wrong_feed_order_fails_before_cloud_calls(
    cloud_calls: tuple[Mock, Mock],
) -> None:
    upload, load = cloud_calls

    with pytest.raises(ValueError, match="Expected station_information"):
        run_capture(STATUS, INFORMATION)

    upload.assert_not_called()
    load.assert_not_called()


def test_upload_failure_stops_before_warehouse_loading(
    cloud_calls: tuple[Mock, Mock],
) -> None:
    upload, load = cloud_calls
    error = PreconditionFailed("Archive already exists")
    upload.side_effect = error

    with pytest.raises(PreconditionFailed) as raised:
        run_capture()

    assert raised.value is error
    upload.assert_called_once()
    load.assert_not_called()


@pytest.mark.parametrize(
    "raw",
    [
        b"not-json\n",
        b'{"data":{"stations":[{"station_id":"001","num_bikes_available":NaN}]}}',
    ],
)
def test_failed_row_preparation_leaves_original_bytes_archived(
    cloud_calls: tuple[Mock, Mock], raw: bytes
) -> None:
    upload, load = cloud_calls

    with pytest.raises(ValueError):
        run_capture(status=replace(STATUS, raw=raw))

    upload.assert_called_once()
    with ZipFile(BytesIO(upload.call_args.args[3])) as archive:
        assert archive.read("station_status.json") == raw
    load.assert_not_called()


@pytest.mark.parametrize(
    "error",
    [Conflict("Load job already exists"), TimeoutError("Load outcome unknown")],
)
def test_load_failure_propagates_after_one_archive_upload(
    cloud_calls: tuple[Mock, Mock], error: Exception
) -> None:
    upload, load = cloud_calls
    load.side_effect = error

    with pytest.raises(type(error)) as raised:
        run_capture()

    assert raised.value is error
    upload.assert_called_once()
    load.assert_called_once()
