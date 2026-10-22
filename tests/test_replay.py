import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from google.cloud.bigquery import Client, LoadJob

from divvyai.replay import main


def replay_args(archive_path: Path) -> list[str]:
    return [
        "--project",
        "test-project",
        "--table",
        "test-project.raw.station_records",
        "--archive",
        str(archive_path),
        "--job-id",
        "original-job-id",
    ]


@pytest.mark.parametrize(
    ("argv", "exit_code"),
    [([], 2), (["--help"], 0)],
)
def test_help_and_missing_arguments_do_not_connect(
    argv: list[str], exit_code: int
) -> None:
    with patch("divvyai.replay.Client") as client:
        with pytest.raises(SystemExit) as raised:
            main(argv)

    assert raised.value.code == exit_code
    client.assert_not_called()


def test_replay_passes_archive_and_reports_original_completion(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    archive_path = tmp_path / "capture.zip"
    archive_path.write_bytes(b"saved-archive-bytes")
    completed_at = datetime(2026, 10, 13, 6, 8, 49, tzinfo=UTC)
    job = Mock(spec=LoadJob, output_rows=4, ended=completed_at)
    client = Mock(spec=Client)

    with (
        patch("divvyai.replay.Client", return_value=client) as create_client,
        patch("divvyai.replay.recover_archive", return_value=job) as recover,
    ):
        main(replay_args(archive_path))

    create_client.assert_called_once_with(
        project="test-project", location="us-central1"
    )
    recover.assert_called_once()
    args, kwargs = recover.call_args
    assert args == (client, "test-project.raw.station_records", b"saved-archive-bytes")
    checked_at = kwargs.pop("now")
    assert isinstance(checked_at, datetime)
    assert checked_at.tzinfo == UTC
    assert kwargs == {"job_id": "original-job-id", "location": "us-central1"}
    client.close.assert_called_once()
    assert json.loads(capsys.readouterr().out) == {
        "event": "recovery_completed",
        "schema_version": 1,
        "archive_path": str(archive_path),
        "destination_table": "test-project.raw.station_records",
        "job_project": "test-project",
        "job_location": "us-central1",
        "job_id": "original-job-id",
        "output_rows": 4,
        "load_completed_at": completed_at.isoformat(),
    }


@pytest.mark.parametrize(
    "outcome",
    [
        TimeoutError("Import outcome still unknown"),
        Mock(spec=LoadJob, output_rows=None, ended=None),
    ],
)
def test_replay_failure_closes_client_and_prints_no_success(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    outcome: object,
) -> None:
    archive_path = tmp_path / "capture.zip"
    archive_path.write_bytes(b"saved-archive-bytes")
    client = Mock(spec=Client)
    with (
        patch("divvyai.replay.Client", return_value=client),
        patch("divvyai.replay.recover_archive") as recover,
    ):
        if isinstance(outcome, Exception):
            recover.side_effect = outcome
            expected_error = type(outcome)
        else:
            recover.return_value = outcome
            expected_error = RuntimeError
        with pytest.raises(expected_error):
            main(replay_args(archive_path))

    client.close.assert_called_once()
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    ("flag", "value"),
    [
        ("--table", "another-project.raw.station_records"),
        ("--job-id", " "),
    ],
)
def test_invalid_target_fails_before_connecting(
    tmp_path: Path, flag: str, value: str
) -> None:
    argv = replay_args(tmp_path / "unused.zip")
    argv[argv.index(flag) + 1] = value
    with patch("divvyai.replay.Client") as client:
        with pytest.raises(SystemExit) as raised:
            main(argv)

    assert raised.value.code == 2
    client.assert_not_called()
