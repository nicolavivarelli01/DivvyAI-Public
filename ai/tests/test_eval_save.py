import json
import subprocess
import sys
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from google.cloud.bigquery import Client

from eval_availability import MAXIMUM_BYTES_BILLED, AvailabilityCase, save_answers

START = datetime(2026, 10, 23, tzinfo=UTC)
TODAY = date(2026, 10, 25)


@pytest.mark.parametrize("flags", [["--execute"], ["--output", "unused.json"]])
def test_execution_and_output_flags_must_be_paired(
    tmp_path: Path, flags: list[str]
) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            str(Path(__file__).parents[1] / "eval_availability.py"),
            str(tmp_path / "missing-questions.json"),
            *flags,
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 2
    assert "Use --execute and --output together." in result.stderr


@pytest.fixture
def cases() -> list[AvailabilityCase]:
    return [
        AvailabilityCase(
            case_id=f"availability_{number:02}",
            station_id="synthetic-s1",
            start=START,
            end=START + timedelta(hours=2),
        )
        for number in range(1, 7)
    ]


@pytest.fixture
def reader(cases: list[AvailabilityCase]) -> tuple[Mock, Mock, list[dict[str, Any]]]:
    rows = [
        {
            "case_id": case.case_id,
            "station_seen": True,
            "status": "ok",
            "hours": [
                {
                    "hour_started_at_utc": START,
                    "snapshot_count": 4,
                    "bike_sample_count": 4,
                    "dock_sample_count": 0,
                    "avg_bikes_available": 1.25,
                    "avg_docks_available": None,
                }
            ],
        }
        for case in cases
    ]
    live = Mock(
        total_bytes_processed=100,
        total_bytes_billed=10485760,
        job_id="offline-job",
        project="divvy-ai",
        location="us-central1",
        started=START,
        ended=START + timedelta(seconds=1),
    )
    live.result.return_value = rows
    client = Mock(spec=Client)
    client.query.side_effect = [Mock(total_bytes_processed=100), live]
    return client, live, rows


def test_saves_all_answers_with_nulls_provenance_and_query_limits(
    tmp_path: Path,
    cases: list[AvailabilityCase],
    reader: tuple[Mock, Mock, list[dict[str, Any]]],
) -> None:
    client, live, _ = reader
    output = tmp_path / "reference.json"
    report = save_answers(client, cases, output, today=TODAY, questions_sha256="a" * 64)
    assert json.loads(output.read_text()) == report
    assert report["questions_sha256"] == "a" * 64
    assert report["query_job_id"] == "offline-job"
    saved = json.loads(output.read_text())["cases"]
    assert len(saved) == 6
    hour = saved[0]["expected_answer"]["hours"][0]
    assert hour["avg_bikes_available"] == 1.25
    assert hour["avg_docks_available"] is None
    assert hour["hour_started_at_utc"] == "2026-10-23T00:00:00Z"
    configs = [call.kwargs["job_config"] for call in client.query.call_args_list]
    assert [config.dry_run for config in configs] == [True, False]
    assert all(
        config.maximum_bytes_billed == MAXIMUM_BYTES_BILLED for config in configs
    )
    live.result.assert_called_once_with(timeout=60, retry=None, job_retry=None)
    assert list(tmp_path.iterdir()) == [output]


@pytest.mark.parametrize("kind", ["file", "symlink"])
def test_existing_output_is_preserved_before_queries(
    tmp_path: Path, cases: list[AvailabilityCase], kind: str
) -> None:
    output = tmp_path / "reference.json"
    if kind == "file":
        output.write_text("original")
    else:
        output.symlink_to(tmp_path / "missing.json")
    client = Mock(spec=Client)
    with pytest.raises(ValueError, match="already exists"):
        save_answers(client, cases, output, today=TODAY, questions_sha256="a" * 64)
    client.query.assert_not_called()
    assert (
        output.is_symlink() if kind == "symlink" else output.read_text() == "original"
    )


@pytest.mark.parametrize(
    "fault",
    [
        "provider",
        "unknown",
        "missing",
        "duplicate",
        "unordered",
        "outside_window",
        "status",
        "negative_count",
        "null_count_mismatch",
        "missing_bytes",
        "over_cap",
        "invalid_bytes",
        "nonfinite",
    ],
)
def test_failed_or_inconsistent_results_publish_nothing(
    tmp_path: Path,
    cases: list[AvailabilityCase],
    reader: tuple[Mock, Mock, list[dict[str, Any]]],
    fault: str,
) -> None:
    client, live, rows = reader
    if fault == "provider":
        live.result.side_effect = RuntimeError("synthetic provider failure")
    elif fault == "unknown":
        rows[0]["station_seen"] = False
    elif fault == "missing":
        rows.pop()
    elif fault == "duplicate":
        rows[-1]["case_id"] = rows[0]["case_id"]
    elif fault in {"missing_bytes", "over_cap", "invalid_bytes"}:
        live.total_bytes_billed = {
            "missing_bytes": None,
            "over_cap": MAXIMUM_BYTES_BILLED + 1,
            "invalid_bytes": -1,
        }[fault]
    else:
        hours = list(rows[0]["hours"])
        hour = dict(hours[0])
        if fault == "unordered":
            later = deepcopy(hour)
            later["hour_started_at_utc"] = START + timedelta(hours=1)
            rows[0]["hours"] = [later, hour]
        elif fault == "outside_window":
            hour["hour_started_at_utc"] = START - timedelta(hours=1)
        elif fault == "status":
            rows[0]["status"] = "no_data"
        elif fault == "negative_count":
            hour["bike_sample_count"] = -1
        elif fault == "null_count_mismatch":
            hour["avg_docks_available"] = 2.0
        else:
            hour["avg_bikes_available"] = float("nan")
        if fault != "unordered":
            rows[0]["hours"] = [hour]
    with pytest.raises((ValueError, RuntimeError)):
        save_answers(
            client,
            cases,
            tmp_path / "reference.json",
            today=TODAY,
            questions_sha256="a" * 64,
        )
    assert list(tmp_path.iterdir()) == []


def test_known_station_without_hours_preserves_no_data(
    tmp_path: Path,
    cases: list[AvailabilityCase],
    reader: tuple[Mock, Mock, list[dict[str, Any]]],
) -> None:
    client, _, rows = reader
    rows[0].update(status="no_data", hours=[])
    output = tmp_path / "reference.json"
    save_answers(client, cases, output, today=TODAY, questions_sha256="a" * 64)
    assert json.loads(output.read_text())["cases"][0]["expected_answer"] == {
        "status": "no_data",
        "hours": [],
    }


def test_failed_preflight_prevents_execution(
    tmp_path: Path, cases: list[AvailabilityCase]
) -> None:
    client = Mock(spec=Client)
    client.query.return_value.total_bytes_processed = MAXIMUM_BYTES_BILLED + 1
    with pytest.raises(ValueError, match="above the cap"):
        save_answers(
            client,
            cases,
            tmp_path / "reference.json",
            today=TODAY,
            questions_sha256="a" * 64,
        )
    client.query.assert_called_once()
    assert list(tmp_path.iterdir()) == []


def test_publish_failure_removes_temporary_file(
    tmp_path: Path,
    cases: list[AvailabilityCase],
    reader: tuple[Mock, Mock, list[dict[str, Any]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _, _ = reader
    monkeypatch.setattr(
        "eval_availability.os.link", Mock(side_effect=OSError("synthetic"))
    )
    with pytest.raises(OSError, match="synthetic"):
        save_answers(
            client,
            cases,
            tmp_path / "reference.json",
            today=TODAY,
            questions_sha256="a" * 64,
        )
    assert list(tmp_path.iterdir()) == []


def test_publish_race_preserves_other_output(
    tmp_path: Path,
    cases: list[AvailabilityCase],
    reader: tuple[Mock, Mock, list[dict[str, Any]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "reference.json"

    def conflict(source: Path, destination: Path) -> None:
        destination.write_text("other output")
        raise FileExistsError("synthetic race")

    monkeypatch.setattr("eval_availability.os.link", conflict)
    with pytest.raises(FileExistsError):
        save_answers(reader[0], cases, output, today=TODAY, questions_sha256="a" * 64)
    assert output.read_text() == "other output"
    assert list(tmp_path.iterdir()) == [output]
