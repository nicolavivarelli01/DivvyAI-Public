import json
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import Mock

import pytest
from google.cloud.bigquery import Client

from eval_availability import MAXIMUM_BYTES_BILLED, estimate, load_cases
from eval_cases import build_cases

TODAY = date(2026, 10, 25)


@pytest.fixture
def questions(tmp_path: Path) -> Path:
    path = tmp_path / "questions.json"
    cases = build_cases(datetime(2026, 10, 25, 12, tzinfo=UTC))
    path.write_text(
        json.dumps({"schema_version": 1, "cases": [asdict(case) for case in cases]})
    )
    return path


def test_loads_saved_windows_and_normalizes_offsets(questions: Path) -> None:
    document = json.loads(questions.read_text())
    arguments = document["cases"][4]["expected_arguments"]
    arguments.update(start="2026-10-23T08:00:00-05:00", end="2026-10-23T09:00:00-05:00")
    questions.write_text(json.dumps(document))
    cases = load_cases(questions, today=TODAY)
    assert len(cases) == 6
    assert cases[4].start == datetime(2026, 10, 23, 13, tzinfo=UTC)
    offset = cases[4].start.utcoffset()
    assert offset is not None and offset.total_seconds() == 0
    assert cases[5].start == datetime(2026, 10, 22, 23, tzinfo=UTC)


def test_one_dry_run_preserves_case_parameters_and_scan_cap(questions: Path) -> None:
    client = Mock(spec=Client)
    client.query.return_value.total_bytes_processed = 100
    assert estimate(client, load_cases(questions, today=TODAY), today=TODAY) == 100
    client.query.assert_called_once()
    call = client.query.call_args
    config = call.kwargs["job_config"]
    assert config.dry_run and not config.use_query_cache and not config.use_legacy_sql
    assert config.maximum_bytes_billed == MAXIMUM_BYTES_BILLED
    parameters = config.to_api_repr()["query"]["queryParameters"]
    assert len(parameters[0]["parameterValue"]["arrayValues"]) == 6
    assert parameters[1]["parameterValue"]["value"] == "2026-10-22"
    assert parameters[2]["parameterValue"]["value"] == "2026-10-25"
    assert call.kwargs["job_retry"] is None and call.kwargs["timeout"] == 30
    operation = Mock(side_effect=RuntimeError("offline failure"))
    with pytest.raises(RuntimeError, match="offline failure"):
        call.kwargs["retry"](operation)()
    operation.assert_called_once()
    client.query.return_value.result.assert_not_called()


@pytest.mark.parametrize(
    "size, expected_message",
    [
        (None, "Scan estimate is missing: BigQuery returned None."),
        (-1, "Scan estimate is invalid: -1 bytes."),
        (
            MAXIMUM_BYTES_BILLED + 1,
            "Scan estimate is above the cap: 536870913 bytes "
            "(512.00 MiB); maximum is 536870912 bytes (512 MiB).",
        ),
    ],
)
def test_unusable_scan_estimates_stop_without_execution(
    questions: Path, size: int | None, expected_message: str
) -> None:
    client = Mock(spec=Client)
    client.query.return_value.total_bytes_processed = size
    with pytest.raises(ValueError) as error:
        estimate(client, load_cases(questions, today=TODAY), today=TODAY)
    assert str(error.value) == expected_message
    client.query.return_value.result.assert_not_called()


@pytest.mark.parametrize("size", [382_841_106, 536_870_912])
def test_reviewed_scan_and_512_mib_boundary_are_accepted(
    questions: Path, size: int
) -> None:
    client = Mock(spec=Client)
    client.query.return_value.total_bytes_processed = size
    assert estimate(client, load_cases(questions, today=TODAY), today=TODAY) == size
    config = client.query.call_args.kwargs["job_config"]
    assert config.maximum_bytes_billed == 536_870_912
    assert config.dry_run is True
    client.query.return_value.result.assert_not_called()


@pytest.mark.parametrize(
    "changes",
    [
        {"start": "2026-10-23T00:01:00Z"},
        {"end": "2026-10-23T00:00:00Z"},
        {"start": "2026-10-23T00:00:00"},
        {"start": "2026-10-17T00:00:00Z"},
        {"end": "2026-10-25T01:00:00Z"},
        {"station_id": " "},
    ],
)
def test_invalid_windows_are_rejected(questions: Path, changes: dict[str, str]) -> None:
    document = json.loads(questions.read_text())
    document["cases"][0]["expected_arguments"].update(changes)
    questions.write_text(json.dumps(document))
    with pytest.raises(ValueError):
        load_cases(questions, today=TODAY)


@pytest.mark.parametrize("problem", ["version", "duplicate", "missing"])
def test_bad_question_sets_are_rejected(questions: Path, problem: str) -> None:
    document = json.loads(questions.read_text())
    if problem == "version":
        document["schema_version"] = 2
    elif problem == "duplicate":
        document["cases"][1]["case_id"] = document["cases"][0]["case_id"]
    else:
        document["cases"][0]["expected_tool"] = "rank_stations"
    questions.write_text(json.dumps(document))
    with pytest.raises(ValueError):
        load_cases(questions, today=TODAY)
