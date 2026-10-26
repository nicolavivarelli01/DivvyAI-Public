import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from google.cloud.bigquery import Client

from agent import MODEL_ID
from eval_finish import GAP_CAP, gap_answer, gap_config, grade, same, save, sql_query

NOW = datetime(2026, 10, 26, 0, 30, tzinfo=UTC)


def fixture() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    case = {
        "case_id": "ranking_01",
        "prompt": "synthetic question",
        "expected_tool": "rank_stations",
        "expected_arguments": {
            "metric": "empty_bikes_rate",
            "hour": 8,
            "limit": 2,
            "day_type": "weekdays",
            "area": "all",
        },
    }
    answer = {
        "status": "ok",
        "stations": [
            {
                "station_id": "a",
                "metric_value": 0.75,
                "valid_sample_count": 4,
                "zero_sample_count": 3,
                "sampled_days": 1,
            },
            {
                "station_id": "b",
                "metric_value": 0.5,
                "valid_sample_count": 4,
                "zero_sample_count": 2,
                "sampled_days": 1,
            },
        ],
    }
    report = {
        "question": case["prompt"],
        "model_id": MODEL_ID,
        "stop_reason": "end_turn",
        "model_calls": 2,
        "admitted_tool_calls": 1,
        "model_limit_reached": False,
        "tool_limit_reached": False,
        "answer": json.dumps(answer),
        "tool_requests": [
            {
                "name": "rank_stations",
                "arguments": deepcopy(case["expected_arguments"]),
                "cancelled": False,
                "error_type": None,
                "result": {
                    "status": "success",
                    "isError": False,
                    "structuredContent": deepcopy(answer),
                },
            }
        ],
    }
    return case, report, answer


def test_correct_answers_and_semantic_defaults_pass() -> None:
    case, report, answer = fixture()
    del report["tool_requests"][0]["arguments"]["area"]
    assert grade(case, report, answer)["passed"] is True


@pytest.mark.parametrize(
    "fault",
    [
        "tool",
        "argument",
        "answer",
        "data",
        "order",
        "limit",
        "error",
        "cancelled",
        "prompt",
        "model",
        "json",
    ],
)
def test_corrupt_evidence_fails(fault: str) -> None:
    case, report, answer = fixture()
    trace = report["tool_requests"][0]
    if fault == "tool":
        trace["name"] = "find_capture_gaps"
    elif fault == "argument":
        trace["arguments"]["hour"] = 9
    elif fault == "answer":
        wrong = deepcopy(answer)
        wrong["stations"][0]["metric_value"] = 0.5
        report["answer"] = json.dumps(wrong)
    elif fault == "data":
        trace["result"]["structuredContent"]["stations"][0]["valid_sample_count"] = 3
    elif fault == "order":
        trace["result"]["structuredContent"]["stations"].reverse()
    elif fault == "limit":
        report["model_limit_reached"] = True
    elif fault == "error":
        trace["result"]["isError"] = True
    elif fault == "cancelled":
        trace["cancelled"] = True
    elif fault == "prompt":
        report["question"] = "different question"
    elif fault == "model":
        report["model_id"] = "different model"
    else:
        report["answer"] = "not JSON"
    assert grade(case, report, answer)["passed"] is False


@pytest.mark.parametrize(
    "actual,expected,correct",
    [
        ("2026-10-25T19:30:00-05:00", "2026-10-26T00:30:00Z", True),
        ("2026-10-26T00:30:00", "2026-10-26T00:30:00Z", False),
        (0.3333333333333333, 1 / 3, True),
        (0.33, 1 / 3, False),
        (float("nan"), 0.5, False),
        (True, 1, False),
        (4.0, 4, False),
        (None, 0, False),
        ([2, 1], [1, 2], False),
        ({"x": 1, "extra": 2}, {"x": 1}, True),
    ],
)
def test_comparison_rules(actual: Any, expected: Any, correct: bool) -> None:
    assert same(actual, expected) is correct


def test_existing_files_and_dangling_symlinks_are_preserved(tmp_path: Path) -> None:
    path = tmp_path / "existing.json"
    path.write_text("original")
    with pytest.raises(FileExistsError):
        save(path, {"replacement": True})
    assert path.read_text() == "original"
    link = tmp_path / "link.json"
    link.symlink_to(tmp_path / "absent")
    with pytest.raises(FileExistsError):
        save(link, {})
    assert not (tmp_path / "absent").exists()


def test_invalid_json_values_publish_nothing(tmp_path: Path) -> None:
    path = tmp_path / "invalid.json"
    with pytest.raises(ValueError):
        save(path, {"number": float("nan")})
    assert not path.exists()


def test_gap_status_and_limit_handling() -> None:
    row: dict[str, Any] = {
        "information_count": 4,
        "status_count": 4,
        "matching_intervals": 3,
        "gaps": [{"capture_id": str(i)} for i in range(3)],
    }
    assert gap_answer(row, 2) == {
        "status": "gaps_found",
        "matching_intervals": 3,
        "gaps": row["gaps"][:2],
    }
    row.update(matching_intervals=0, gaps=[])
    assert gap_answer(row, 2)["status"] == "no_gaps"
    row["status_count"] = 0
    assert gap_answer(row, 2)["status"] == "insufficient_data"


def test_reference_queries_preview_first_and_keep_as_of_time() -> None:
    client = Mock(spec=Client)
    live = Mock(
        total_bytes_processed=100,
        total_bytes_billed=10_485_760,
        job_id="synthetic",
        project="divvy-ai",
        location="us-central1",
        started=NOW,
        ended=NOW,
    )
    live.result.return_value = [{"at": NOW, "n": 4}]
    client.query.side_effect = [Mock(total_bytes_processed=100), live]
    config = gap_config(NOW, 15.0)
    rows, evidence = sql_query(client, "SELECT synthetic", config)
    assert rows == [{"at": NOW.isoformat(), "n": 4}]
    assert evidence["bytes_billed"] == 10_485_760
    calls = client.query.call_args_list
    assert calls[0].kwargs["job_config"].dry_run is True
    assert calls[1].kwargs["job_config"].dry_run is False
    assert calls[1].kwargs["job_config"].maximum_bytes_billed == GAP_CAP
    parameters = evidence["query_parameters"]
    assert parameters[0]["parameterValue"]["value"].startswith("2026-10-26 00:30:00")
    assert calls[1].kwargs["job_retry"] is None
    live.result.assert_called_once_with(timeout=60, retry=None, job_retry=None)


@pytest.mark.parametrize("size", [None, -1, True, GAP_CAP + 1])
def test_failed_reference_preview_prevents_data_query(size: Any) -> None:
    client = Mock(spec=Client)
    client.query.return_value.total_bytes_processed = size
    with pytest.raises(ValueError, match="Reference scan rejected"):
        sql_query(client, "SELECT synthetic", gap_config(NOW, 15.0))
    client.query.assert_called_once()
    client.query.return_value.result.assert_not_called()


@pytest.mark.parametrize("model_failure", [False, True])
def test_full_run_reuses_six_reports_and_calls_model_only_fourteen_times(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, model_failure: bool
) -> None:
    from dataclasses import asdict
    from hashlib import sha256

    from eval_cases import build_cases
    from eval_finish import run
    from eval_ranking import REFERENCE_SQL

    base = tmp_path / "evaluation"
    archive = base / "availability-run-v1"
    archive.mkdir(parents=True)
    cases = [{**asdict(c), "prompt": c.prompt} for c in build_cases(datetime.now(UTC))]
    questions = base / "questions-v1.json"
    save(questions, {"schema_version": 1, "cases": cases})
    cases_by_prompt = {c["prompt"]: c for c in cases}
    expected: dict[str, dict[str, Any]] = {
        "get_station_availability": {"status": "no_data", "hours": []},
        "rank_stations": {"status": "no_data", "stations": []},
        "find_capture_gaps": {"status": "no_gaps", "matching_intervals": 0, "gaps": []},
    }
    saved_references = []
    original_bytes = {}
    for case in cases[:6]:
        saved_references.append(
            {
                "case_id": case["case_id"],
                "expected_answer": expected[case["expected_tool"]],
            }
        )

    save(
        base / "availability-reference-v1.json",
        {
            "questions_sha256": sha256(questions.read_bytes()).hexdigest(),
            "cases": saved_references,
        },
    )

    def fake_ask(question: str, *, report_path: Path) -> str:
        case = cases_by_prompt[question]
        if model_failure and case["case_id"] == "gaps_02":
            raise RuntimeError("synthetic model failure")
        _, report, _ = fixture()
        answer = expected[case["expected_tool"]]
        report.update(question=question, answer=json.dumps(answer), elapsed_seconds=1.0)
        trace = report["tool_requests"][0]
        trace.update(name=case["expected_tool"], arguments=case["expected_arguments"])
        trace["result"]["structuredContent"] = {
            **answer,
            "window_end_utc": NOW.isoformat(),
        }
        save(report_path, report)
        return str(report["answer"])

    for case in cases[:6]:
        path = archive / f"{case['case_id']}.json"
        fake_ask(case["prompt"], report_path=path)
        original_bytes[path] = path.read_bytes()

    def fake_sql(
        client: Client, sql: str, config: Any
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if sql == REFERENCE_SQL:
            rows = [
                {"case_id": case["case_id"], **expected["rank_stations"]}
                for case in cases[6:14]
            ]
        else:
            rows = [
                {
                    "information_count": 4,
                    "status_count": 4,
                    "matching_intervals": 0,
                    "gaps": [],
                }
            ]
        return rows, {"bytes_billed": 10_485_760}

    client = Mock(spec=Client)
    client.query.return_value.total_bytes_processed = 100
    model = Mock(side_effect=fake_ask)
    monkeypatch.setattr("eval_finish.bigquery.Client", Mock(return_value=client))
    monkeypatch.setattr("eval_finish.ask", model)
    monkeypatch.setattr("eval_finish.sql_query", Mock(side_effect=fake_sql))
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-not-a-real-key")
    output = base / "complete-run"
    run(base, output)
    summary = json.loads((output / "summary.json").read_text())
    assert model.call_count == 14
    assert summary["total_cases"] == 20
    assert summary["totals"]["passed"] == (19 if model_failure else 20)
    assert all(path.read_bytes() == content for path, content in original_bytes.items())
    with pytest.raises(FileExistsError):
        run(base, output)
    assert model.call_count == 14
