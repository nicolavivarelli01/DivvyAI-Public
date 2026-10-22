import json
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock, call, patch
from urllib.error import URLError

import pytest
from google.cloud.bigquery import Client as BigQueryClient
from google.cloud.bigquery import LoadJob
from google.cloud.storage import Client as StorageClient

from divvyai.capture import FeedCapture
from divvyai.cloud import main
from divvyai.identity import identify_capture
from divvyai.local import INFORMATION_URL, STATUS_URL

FIXTURES = Path(__file__).parent / "fixtures"
INFORMATION = FeedCapture(
    feed_name="station_information",
    source_url=INFORMATION_URL,
    received_at=datetime(2026, 10, 12, 12, 0, 1, tzinfo=UTC),
    raw=(FIXTURES / "station_information.json").read_bytes(),
)
STATUS = FeedCapture(
    feed_name="station_status",
    source_url=STATUS_URL,
    received_at=datetime(2026, 10, 12, 12, 0, 3, tzinfo=UTC),
    raw=(FIXTURES / "station_status.json").read_bytes(),
)
COMPLETED_AT = datetime(2026, 10, 12, 12, 0, 10, tzinfo=UTC)
ARGS = [
    "--project",
    "test-project",
    "--bucket",
    "test-bucket",
    "--table",
    "test-project.raw.station_records",
]


@pytest.fixture
def services() -> Iterator[tuple[Mock, Mock, Mock, Mock]]:
    with (
        patch("divvyai.cloud.fetch_feed") as fetch,
        patch("divvyai.cloud.StorageClient") as storage,
        patch("divvyai.cloud.BigQueryClient") as bigquery,
        patch("divvyai.cloud.write_cloud_capture") as write,
    ):
        captures = {"station_information": INFORMATION, "station_status": STATUS}
        fetch.side_effect = lambda name, url: captures[name]
        storage.return_value = Mock(spec=StorageClient)
        bigquery.return_value = Mock(spec=BigQueryClient)
        job = Mock(spec=LoadJob)
        job.output_rows = 4
        job.ended = COMPLETED_AT
        write.return_value = job
        yield fetch, storage, bigquery, write


@pytest.mark.parametrize(("argv", "exit_code"), [([], 2), (["--help"], 0)])
def test_missing_arguments_and_help_do_no_external_work(
    services: tuple[Mock, Mock, Mock, Mock], argv: list[str], exit_code: int
) -> None:
    with pytest.raises(SystemExit) as raised:
        main(argv)

    assert raised.value.code == exit_code
    for service in services:
        service.assert_not_called()


@pytest.mark.parametrize(
    ("flag", "value"),
    [
        ("--project", ""),
        ("--bucket", ""),
        ("--table", ""),
        ("--location", ""),
        ("--project", " \t"),
        ("--bucket", "gs://test-bucket"),
        ("--bucket", "test-bucket/raw"),
        ("--table", "raw.station_records"),
        ("--table", "other-project.raw.station_records"),
    ],
)
def test_invalid_targets_fail_before_external_work(
    services: tuple[Mock, Mock, Mock, Mock], flag: str, value: str
) -> None:
    argv = ARGS.copy()
    if flag in argv:
        argv[argv.index(flag) + 1] = value
    else:
        argv.extend([flag, value])

    with pytest.raises(SystemExit) as raised:
        main(argv)

    assert raised.value.code == 2
    for service in services:
        service.assert_not_called()


def test_fetches_both_feeds_and_calls_pipeline_with_explicit_targets(
    services: tuple[Mock, Mock, Mock, Mock], capsys: pytest.CaptureFixture[str]
) -> None:
    fetch, storage, bigquery, write = services

    main(ARGS)

    assert fetch.call_args_list == [
        call("station_information", INFORMATION_URL),
        call("station_status", STATUS_URL),
    ]
    storage.assert_called_once_with(project="test-project")
    bigquery.assert_called_once_with(project="test-project", location="us-central1")
    write.assert_called_once()
    assert write.call_args.args == (INFORMATION, STATUS)
    kwargs = write.call_args.kwargs
    assert kwargs["storage_client"] is storage.return_value
    assert kwargs["bigquery_client"] is bigquery.return_value
    assert kwargs["bucket_name"] == "test-bucket"
    assert kwargs["table_id"] == "test-project.raw.station_records"
    assert kwargs["location"] == "us-central1"
    assert kwargs["job_id"].startswith("divvyai_cloud_raw_v1_")
    storage.return_value.close.assert_called_once()
    bigquery.return_value.close.assert_called_once()
    identity = identify_capture(INFORMATION, STATUS)
    output = capsys.readouterr().out
    assert f"Capture ID: {identity.capture_id}" in output
    assert f"Archive target: gs://test-bucket/{identity.object_name}" in output
    assert f"Planned load job: test-project:us-central1.{kwargs['job_id']}" in output
    receipt = json.loads(output.splitlines()[-1])
    assert receipt == {
        "event": "capture_loaded",
        "schema_version": 1,
        "capture_id": identity.capture_id,
        "archive_uri": f"gs://test-bucket/{identity.object_name}",
        "destination_table": "test-project.raw.station_records",
        "job_project": "test-project",
        "job_location": "us-central1",
        "job_id": kwargs["job_id"],
        "output_rows": 4,
        "load_completed_at": COMPLETED_AT.isoformat(),
    }


def test_forwards_explicit_location(
    services: tuple[Mock, Mock, Mock, Mock],
) -> None:
    _, _, bigquery, write = services

    main([*ARGS, "--location", "us-east1"])

    bigquery.assert_called_once_with(project="test-project", location="us-east1")
    assert write.call_args.kwargs["location"] == "us-east1"


@pytest.mark.parametrize("failed_request", [1, 2])
def test_fetch_failure_stops_before_creating_clients(
    services: tuple[Mock, Mock, Mock, Mock], failed_request: int
) -> None:
    fetch, storage, bigquery, write = services
    error = URLError("Offline")
    fetch.side_effect = [error] if failed_request == 1 else [INFORMATION, error]

    with pytest.raises(URLError) as raised:
        main(ARGS)

    assert raised.value is error
    assert fetch.call_count == failed_request
    storage.assert_not_called()
    bigquery.assert_not_called()
    write.assert_not_called()


def test_write_failure_keeps_job_identifier_visible_and_closes_clients(
    services: tuple[Mock, Mock, Mock, Mock], capsys: pytest.CaptureFixture[str]
) -> None:
    _, storage, bigquery, write = services
    error = TimeoutError("Load outcome unknown")
    write.side_effect = error

    with pytest.raises(TimeoutError) as raised:
        main(ARGS)

    assert raised.value is error
    write.assert_called_once()
    storage.return_value.close.assert_called_once()
    bigquery.return_value.close.assert_called_once()
    output = capsys.readouterr().out
    assert write.call_args.kwargs["job_id"] in output
    assert '"event": "capture_loaded"' not in output


def test_job_id_is_stable_for_capture_and_destination(
    services: tuple[Mock, Mock, Mock, Mock],
) -> None:
    fetch, _, _, write = services

    main(ARGS)
    first_id = write.call_args.kwargs["job_id"]
    main(ARGS)
    assert write.call_args.kwargs["job_id"] == first_id
    different_table = ARGS.copy()
    different_table[-1] = "test-project.raw.other_records"
    main(different_table)
    assert write.call_args.kwargs["job_id"] != first_id
    fetch.side_effect = [INFORMATION, replace(STATUS, received_at=COMPLETED_AT)]
    main(ARGS)
    assert write.call_args.kwargs["job_id"] != first_id


def test_bigquery_client_creation_failure_closes_storage_client(
    services: tuple[Mock, Mock, Mock, Mock],
) -> None:
    _, storage, bigquery, write = services
    bigquery.side_effect = RuntimeError("Client construction failed")

    with pytest.raises(RuntimeError, match="Client construction failed"):
        main(ARGS)

    storage.return_value.close.assert_called_once()
    write.assert_not_called()


@pytest.mark.parametrize("missing_field", ["output_rows", "ended"])
def test_missing_completion_metadata_fails_without_success_report(
    services: tuple[Mock, Mock, Mock, Mock],
    capsys: pytest.CaptureFixture[str],
    missing_field: str,
) -> None:
    _, storage, bigquery, write = services
    setattr(write.return_value, missing_field, None)

    with pytest.raises(RuntimeError, match="missing row count or completion time"):
        main(ARGS)

    storage.return_value.close.assert_called_once()
    bigquery.return_value.close.assert_called_once()
    assert '"event": "capture_loaded"' not in capsys.readouterr().out


def test_module_entrypoint_displays_help() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "divvyai.cloud", "--help"],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )

    assert "--project" in result.stdout
    assert "--bucket" in result.stdout
    assert "--table" in result.stdout
