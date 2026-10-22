from io import BytesIO
from unittest.mock import Mock

import pytest
from google.api_core.exceptions import (
    BadRequest,
    Conflict,
    Forbidden,
    NotFound,
    ServiceUnavailable,
)
from google.cloud.bigquery import Client, LoadJob, QueryJob, TableReference

from divvyai.cloud_bigquery import get_existing_load, load_raw_jsonl

TABLE_ID = "test-project.test_dataset.station_records"
JOB_ID = "divvyai_test_capture_001"
LOCATION = "us-central1"
JSONL_BYTES = (
    b'{"capture_id":"test-capture","archive_object_name":"raw/test.zip",'
    b'"feed_name":"station_information","source_url":"https://example.test/info",'
    b'"received_at":"2026-10-12T00:00:00+00:00","station_id":"001",'
    b'"feed_metadata":{},"payload":{"station_id":"001","name":"Caf\xc3\xa9"}}\n'
)


@pytest.fixture
def load_client() -> Mock:
    client = Mock(spec=Client)
    client.load_table_from_file.return_value = Mock(spec=LoadJob)
    return client


def test_load_preserves_bytes_and_waits_for_completion(load_client: Mock) -> None:
    job: Mock = load_client.load_table_from_file.return_value

    def inspect_upload(source: BytesIO, *_args: object, **_kwargs: object) -> Mock:
        assert source.read() == JSONL_BYTES
        return job

    load_client.load_table_from_file.side_effect = inspect_upload

    result = load_raw_jsonl(
        load_client, TABLE_ID, JSONL_BYTES, job_id=JOB_ID, location=LOCATION
    )

    load_client.load_table_from_file.assert_called_once()
    args, kwargs = load_client.load_table_from_file.call_args
    assert args[0].closed
    assert args[1] == TABLE_ID
    config = kwargs.pop("job_config")
    assert kwargs == {
        "job_id": JOB_ID,
        "location": LOCATION,
        "size": len(JSONL_BYTES),
        "num_retries": 0,
        "timeout": 30,
    }
    assert config.to_api_repr() == {
        "load": {
            "sourceFormat": "NEWLINE_DELIMITED_JSON",
            "writeDisposition": "WRITE_APPEND",
            "createDisposition": "CREATE_NEVER",
            "autodetect": False,
            "maxBadRecords": 0,
            "ignoreUnknownValues": False,
        }
    }
    job.result.assert_called_once_with(timeout=60, retry=None)
    assert result is job


@pytest.mark.parametrize(
    ("jsonl", "job_id", "message"),
    [
        (JSONL_BYTES, "", "job_id must not be blank"),
        (JSONL_BYTES, " \t", "job_id must not be blank"),
        (b"", JOB_ID, "jsonl must contain records"),
    ],
)
def test_invalid_input_does_not_submit_a_job(
    load_client: Mock, jsonl: bytes, job_id: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        load_raw_jsonl(load_client, TABLE_ID, jsonl, job_id=job_id, location=LOCATION)

    load_client.load_table_from_file.assert_not_called()


@pytest.mark.parametrize(
    "error",
    [
        Conflict("Job already exists"),
        Forbidden("Permission denied"),
        ServiceUnavailable("Submission outcome unknown"),
    ],
)
def test_submission_error_propagates_without_another_attempt(
    load_client: Mock, error: Exception
) -> None:
    job = load_client.load_table_from_file.return_value
    load_client.load_table_from_file.side_effect = error

    with pytest.raises(type(error)) as raised:
        load_raw_jsonl(
            load_client, TABLE_ID, JSONL_BYTES, job_id=JOB_ID, location=LOCATION
        )

    assert raised.value is error
    load_client.load_table_from_file.assert_called_once()
    job.result.assert_not_called()


@pytest.mark.parametrize(
    "error",
    [
        BadRequest("Load rejected"),
        TimeoutError("Still waiting for the load"),
    ],
)
def test_wait_error_propagates_without_resubmitting(
    load_client: Mock, error: Exception
) -> None:
    job = load_client.load_table_from_file.return_value
    job.result.side_effect = error

    with pytest.raises(type(error)) as raised:
        load_raw_jsonl(
            load_client, TABLE_ID, JSONL_BYTES, job_id=JOB_ID, location=LOCATION
        )

    assert raised.value is error
    load_client.load_table_from_file.assert_called_once()
    job.result.assert_called_once_with(timeout=60, retry=None)


def test_existing_load_is_checked_without_resubmitting(load_client: Mock) -> None:
    job = Mock(
        spec=LoadJob,
        destination=TableReference.from_string(TABLE_ID),
        state="RUNNING",
    )
    load_client.get_job.return_value = job

    result = get_existing_load(load_client, TABLE_ID, job_id=JOB_ID, location=LOCATION)

    assert result is job
    load_client.get_job.assert_called_once_with(JOB_ID, location=LOCATION, timeout=30)
    job.result.assert_called_once_with(timeout=60, retry=None)
    load_client.load_table_from_file.assert_not_called()


def test_missing_load_returns_none(load_client: Mock) -> None:
    load_client.get_job.side_effect = NotFound("Job not found")

    result = get_existing_load(load_client, TABLE_ID, job_id=JOB_ID, location=LOCATION)

    assert result is None
    load_client.load_table_from_file.assert_not_called()


@pytest.mark.parametrize(
    ("job", "message"),
    [
        (Mock(spec=QueryJob), "Expected a load job"),
        (
            Mock(
                spec=LoadJob,
                destination=TableReference.from_string(
                    "test-project.other_dataset.other_table"
                ),
                state="DONE",
            ),
            "Expected a load job",
        ),
        (
            Mock(
                spec=LoadJob,
                destination=TableReference.from_string(TABLE_ID),
                state=None,
            ),
            "Existing job is missing its state",
        ),
    ],
)
def test_unexpected_job_is_rejected(load_client: Mock, job: Mock, message: str) -> None:
    load_client.get_job.return_value = job

    with pytest.raises(ValueError, match=message):
        get_existing_load(load_client, TABLE_ID, job_id=JOB_ID, location=LOCATION)

    job.result.assert_not_called()
    load_client.load_table_from_file.assert_not_called()


def test_lookup_permission_error_is_not_treated_as_missing(load_client: Mock) -> None:
    error = Forbidden("Permission denied")
    load_client.get_job.side_effect = error

    with pytest.raises(Forbidden) as raised:
        get_existing_load(load_client, TABLE_ID, job_id=JOB_ID, location=LOCATION)

    assert raised.value is error
    load_client.load_table_from_file.assert_not_called()


@pytest.mark.parametrize(
    "error",
    [BadRequest("Load failed"), TimeoutError("Load is still running")],
)
def test_existing_load_failure_or_timeout_propagates(
    load_client: Mock, error: Exception
) -> None:
    job = Mock(
        spec=LoadJob,
        destination=TableReference.from_string(TABLE_ID),
        state="DONE",
    )
    job.result.side_effect = error
    load_client.get_job.return_value = job

    with pytest.raises(type(error)) as raised:
        get_existing_load(load_client, TABLE_ID, job_id=JOB_ID, location=LOCATION)

    assert raised.value is error
    load_client.load_table_from_file.assert_not_called()
