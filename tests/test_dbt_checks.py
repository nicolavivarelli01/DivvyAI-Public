import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from divvyai.dbt_checks import main


@pytest.mark.parametrize(
    ("freshness_code", "quality_code", "expected_failures"),
    [
        (0, 0, []),
        (1, 0, ["source_freshness"]),
        (0, 1, ["snapshot_quality"]),
        (2, 1, ["source_freshness", "snapshot_quality"]),
    ],
)
def test_combines_results_and_runs_quality_even_if_freshness_fails(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    freshness_code: int,
    quality_code: int,
    expected_failures: list[str],
) -> None:
    with patch("divvyai.dbt_checks.subprocess.run") as run:
        run.side_effect = [
            subprocess.CompletedProcess([], freshness_code),
            subprocess.CompletedProcess([], quality_code),
        ]

        code = main(["--project-dir", str(tmp_path), "--profiles-dir", str(tmp_path)])

    assert code == (1 if expected_failures else 0)
    assert run.call_count == 2
    assert run.call_args_list[0].args[0][3:8] == [
        "source",
        "freshness",
        "--select",
        "source:divvy.station_records",
        "--project-dir",
    ]
    assert run.call_args_list[1].args[0][3:7] == [
        "test",
        "--select",
        "assert_station_snapshot_quality",
        "--project-dir",
    ]
    for invocation in run.call_args_list:
        assert invocation.args[0][1] == "--warn-error-options"
        assert json.loads(invocation.args[0][2]) == {
            "error": ["NoNodesForSelectionCriteria", "NothingToDo"]
        }
    summary = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert summary["status"] == ("failed" if expected_failures else "passed")
    assert summary["failed_checks"] == expected_failures


@pytest.mark.parametrize(
    ("error", "expected_reason"),
    [
        (subprocess.TimeoutExpired("dbt", 120), "timed_out"),
        (FileNotFoundError("dbt missing"), "could_not_start"),
    ],
)
def test_execution_errors_cannot_report_success(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    error: Exception,
    expected_reason: str,
) -> None:
    with patch("divvyai.dbt_checks.subprocess.run") as run:
        run.side_effect = [error, subprocess.CompletedProcess([], 0)]

        code = main(["--project-dir", str(tmp_path), "--profiles-dir", str(tmp_path)])

    assert code == 1
    assert run.call_count == 2
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert events[1]["reason"].startswith(expected_reason)
    assert events[-1]["failed_checks"] == ["source_freshness"]
