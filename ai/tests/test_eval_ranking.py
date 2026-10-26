import json
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import Mock

import pytest
from google.cloud.bigquery import Client

from eval_availability import MAXIMUM_BYTES_BILLED
from eval_cases import build_cases
from eval_ranking import estimate, ingestion_bounds, load_cases

NOW = datetime(2026, 10, 26, 0, 30, tzinfo=UTC)


@pytest.fixture
def questions(tmp_path: Path) -> Path:
    path = tmp_path / "questions.json"
    cases = build_cases(datetime(2026, 10, 25, 12, tzinfo=UTC))
    path.write_text(
        json.dumps({"schema_version": 1, "cases": [asdict(case) for case in cases]})
    )
    return path


def test_saved_ranking_cases_keep_original_parameters(questions: Path) -> None:
    cases = load_cases(questions, now=NOW)
    assert len(cases) == 8
    assert cases[0].model_dump() == {
        "case_id": "ranking_01",
        "metric": "empty_bikes_rate",
        "hour": 8,
        "limit": 3,
        "start_date": date(2026, 10, 22),
        "end_date": date(2026, 10, 25),
        "day_type": "weekdays",
        "area": "all",
    }
    assert cases[-1].metric == "lowest_avg_docks"
    assert cases[-1].area == "downtown_demo"
    assert cases[-1].hour == 17 and cases[-1].limit == 2
    assert ingestion_bounds(cases, NOW) == (date(2026, 10, 22), date(2026, 10, 26))


def test_only_one_dry_run_with_all_eight_cases(questions: Path) -> None:
    client = Mock(spec=Client)
    client.query.return_value.total_bytes_processed = 400_000_000
    assert estimate(client, load_cases(questions, now=NOW), now=NOW) == 400_000_000
    client.query.assert_called_once()
    call = client.query.call_args
    config = call.kwargs["job_config"]
    assert config.dry_run is True
    assert config.use_query_cache is False and config.use_legacy_sql is False
    assert config.maximum_bytes_billed == MAXIMUM_BYTES_BILLED == 536_870_912
    parameters = config.to_api_repr()["query"]["queryParameters"]
    values = parameters[0]["parameterValue"]["arrayValues"]
    assert len(values) == 8
    assert values[0]["structValues"]["start_date"]["value"] == "2026-10-22"
    assert values[-1]["structValues"]["result_limit"]["value"] == "2"
    assert parameters[1]["parameterValue"]["value"] == "2026-10-22"
    assert parameters[2]["parameterValue"]["value"] == "2026-10-26"
    assert call.kwargs["location"] == "us-central1"
    assert call.kwargs["timeout"] == 30 and call.kwargs["job_retry"] is None
    operation = Mock(side_effect=RuntimeError("offline failure"))
    with pytest.raises(RuntimeError, match="offline failure"):
        call.kwargs["retry"](operation)()
    operation.assert_called_once()
    client.query.return_value.result.assert_not_called()


@pytest.mark.parametrize("size", [None, -1, True, 1.5, MAXIMUM_BYTES_BILLED + 1])
def test_unusable_estimate_stops(questions: Path, size: object) -> None:
    client = Mock(spec=Client)
    client.query.return_value.total_bytes_processed = size
    with pytest.raises(ValueError):
        estimate(client, load_cases(questions, now=NOW), now=NOW)
    client.query.assert_called_once()
    client.query.return_value.result.assert_not_called()


def test_exact_cap_is_accepted(questions: Path) -> None:
    client = Mock(spec=Client)
    client.query.return_value.total_bytes_processed = MAXIMUM_BYTES_BILLED
    assert estimate(client, load_cases(questions, now=NOW), now=NOW) == 536_870_912


@pytest.mark.parametrize(
    "changes",
    [
        {"metric": "made_up"},
        {"hour": True},
        {"hour": 24},
        {"limit": 21},
        {"area": "Chicago"},
        {"day_type": "weekend"},
        {"start_date": "2026-10-17"},
        {"end_date": "2026-10-26"},
        {"end_date": "2026-10-22"},
        {"unexpected": 1},
    ],
)
def test_invalid_ranking_arguments_are_rejected(
    questions: Path, changes: dict[str, object]
) -> None:
    document = json.loads(questions.read_text())
    document["cases"][6]["expected_arguments"].update(changes)
    questions.write_text(json.dumps(document))
    with pytest.raises(ValueError):
        load_cases(questions, now=NOW)


@pytest.mark.parametrize("problem", ["version", "duplicate", "missing"])
def test_bad_question_sets_are_rejected(questions: Path, problem: str) -> None:
    document = json.loads(questions.read_text())
    if problem == "version":
        document["schema_version"] = 2
    elif problem == "duplicate":
        document["cases"][7]["case_id"] = document["cases"][6]["case_id"]
    else:
        document["cases"][6]["expected_tool"] = "find_capture_gaps"
    questions.write_text(json.dumps(document))
    with pytest.raises(ValueError):
        load_cases(questions, now=NOW)


def test_naive_clock_is_rejected(questions: Path) -> None:
    with pytest.raises(ValueError, match="timezone"):
        load_cases(questions, now=datetime(2026, 10, 25))
