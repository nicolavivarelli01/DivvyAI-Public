"""Run the remaining 14 saved questions and score all 20; explicit execution only."""

import argparse
import json
import math
import os
from collections import Counter
from contextlib import closing
from copy import deepcopy
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from typing import Any

from google.cloud import bigquery

from agent import MODEL_ID, ask
from eval_availability import LOCATION, NO_RETRY, PROJECT
from eval_ranking import REFERENCE_SQL, load_cases, query_config

GAP_CAP = 256 * 1024 * 1024
GAP_SQL = """
WITH receipts AS (
    SELECT feed_name, capture_id, MAX(received_at) AS capture_received_at
    FROM `divvy-ai.divvyai_raw.station_records`
      FOR SYSTEM_TIME AS OF @as_of
    WHERE _PARTITIONDATE BETWEEN DATE_SUB(DATE(@as_of), INTERVAL 1 DAY)
                             AND DATE(@as_of)
      AND DATE(received_at) BETWEEN DATE_SUB(DATE(@as_of), INTERVAL 1 DAY)
                               AND DATE(@as_of)
      AND feed_name IN ('station_information', 'station_status')
    GROUP BY feed_name, capture_id
), predecessors AS (
    SELECT *, LEAD(capture_received_at) OVER (
        PARTITION BY feed_name ORDER BY capture_received_at DESC, capture_id DESC
    ) AS previous_received_at
    FROM receipts
), intervals AS (
    SELECT *, TIMESTAMP_DIFF(capture_received_at, previous_received_at, SECOND)
        / 60.0 AS gap_minutes
    FROM predecessors
    WHERE capture_received_at < @as_of
)
SELECT
    COUNTIF(feed_name = 'station_information' AND previous_received_at IS NOT NULL)
        AS information_count,
    COUNTIF(feed_name = 'station_status' AND previous_received_at IS NOT NULL)
        AS status_count,
    COUNTIF(gap_minutes >= @min_minutes) AS matching_intervals,
    IFNULL(ARRAY_AGG(
        IF(gap_minutes >= @min_minutes,
            STRUCT(feed_name, capture_id, previous_received_at,
                   capture_received_at, gap_minutes), NULL)
        IGNORE NULLS
        ORDER BY gap_minutes DESC, capture_received_at, feed_name, capture_id
        LIMIT 50
    ), []) AS gaps
FROM intervals
"""


def json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if hasattr(value, "items"):
        return dict(value.items())
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def save(path: Path, value: Any) -> None:
    text = json.dumps(value, default=json_default, indent=2, allow_nan=False) + "\n"
    with path.open("x", encoding="utf-8") as destination:
        destination.write(text)


def read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def instant(text: str) -> datetime:
    value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if value.utcoffset() is None:
        raise ValueError("Timestamp must include a timezone.")
    return value.astimezone(UTC)


def same(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and same(actual[key], value)
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(same(a, e) for a, e in zip(actual, expected, strict=True))
        )
    if type(expected) is int:
        return type(actual) is int and actual == expected
    if isinstance(expected, float):
        return (
            type(actual) in (int, float)
            and math.isfinite(actual)
            and math.isfinite(expected)
            and math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-9)
        )
    if isinstance(expected, str) and expected[:4].isdigit() and "T" in expected:
        try:
            return isinstance(actual, str) and instant(actual) == instant(expected)
        except ValueError:
            return False
    return type(actual) is type(expected) and actual == expected


def payload(report: dict[str, Any], tool: str) -> dict[str, Any]:
    for trace in reversed(report.get("tool_requests", [])):
        if trace.get("name") == tool:
            value = trace.get("result", {}).get("structuredContent", {})
            return value if isinstance(value, dict) else {}
    return {}


def grade(
    case: dict[str, Any], report: dict[str, Any], expected: dict[str, Any]
) -> dict[str, Any]:
    tool = case["expected_tool"]
    traces = report.get("tool_requests", [])
    healthy = (
        report.get("question") == case["prompt"]
        and report.get("model_id") == MODEL_ID
        and report.get("stop_reason") == "end_turn"
        and report.get("model_limit_reached") is False
        and report.get("tool_limit_reached") is False
        and 1 <= report.get("model_calls", 0) <= 3
        and 1 <= len(traces) == report.get("admitted_tool_calls", 0) <= 2
        and all(
            not t.get("cancelled")
            and t.get("error_type") is None
            and t.get("result", {}).get("status") == "success"
            and not t.get("result", {}).get("isError", False)
            for t in traces
        )
    )
    tool_ok = bool(traces) and all(t.get("name") == tool for t in traces)
    defaults: dict[str, Any] = {}
    if tool == "rank_stations":
        defaults = {"limit": 5, "day_type": "all", "area": "all"}
    elif tool == "find_capture_gaps":
        defaults = {"min_minutes": 30.0, "limit": 20}
    args_ok = tool_ok and all(
        same({**defaults, **t.get("arguments", {})}, case["expected_arguments"])
        for t in traces
    )
    try:
        answer = json.loads(report.get("answer", ""))
    except (ValueError, TypeError):
        answer = None
    checks = {
        "tool_correct": tool_ok,
        "arguments_correct": args_ok,
        "tool_data_correct": healthy
        and args_ok
        and same(payload(report, tool), expected),
        "answer_correct": healthy and same(answer, expected),
    }
    return {
        "case_id": case["case_id"],
        "tool": tool,
        "run_complete": healthy,
        **checks,
        "passed": healthy and all(checks.values()),
    }


def preview_sql(
    client: bigquery.Client, sql: str, config: bigquery.QueryJobConfig
) -> int:
    preview_config = deepcopy(config)
    preview_config.dry_run, preview_config.use_query_cache = True, False
    job = client.query(
        sql,
        job_config=preview_config,
        location=LOCATION,
        timeout=30,
        retry=NO_RETRY,
        job_retry=None,
    )
    size, cap = job.total_bytes_processed, config.maximum_bytes_billed
    if type(size) is not int or cap is None or not 0 <= size <= cap:
        raise ValueError(f"Reference scan rejected: {size!r} bytes; cap {cap!r}.")
    return size


def sql_query(
    client: bigquery.Client, sql: str, config: bigquery.QueryJobConfig
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    size = preview_sql(client, sql, config)
    config = deepcopy(config)
    config.dry_run, config.use_query_cache = False, False
    cap = config.maximum_bytes_billed
    assert cap is not None
    job = client.query(
        sql,
        job_config=config,
        location=LOCATION,
        timeout=30,
        retry=NO_RETRY,
        job_retry=None,
    )
    rows = list(job.result(timeout=60, retry=None, job_retry=None))
    processed, billed = job.total_bytes_processed, job.total_bytes_billed
    if any(type(v) is not int or v < 0 for v in (processed, billed)):
        raise ValueError("Reference query accounting is missing or invalid.")
    if billed > cap:
        raise ValueError("Reference query exceeded its byte cap.")
    result = json.loads(json.dumps([dict(row) for row in rows], default=json_default))
    return result, {
        "sql_sha256": sha256(sql.encode()).hexdigest(),
        "query_job_id": job.job_id,
        "project": job.project,
        "location": job.location,
        "started_at_utc": job.started,
        "ended_at_utc": job.ended,
        "estimated_bytes_processed": size,
        "bytes_processed": processed,
        "bytes_billed": billed,
        "query_parameters": config.to_api_repr()["query"]["queryParameters"],
    }


def gap_config(as_of: datetime, minimum: float) -> bigquery.QueryJobConfig:
    return bigquery.QueryJobConfig(
        use_legacy_sql=False,
        maximum_bytes_billed=GAP_CAP,
        query_parameters=[
            bigquery.ScalarQueryParameter("as_of", "TIMESTAMP", as_of),
            bigquery.ScalarQueryParameter("min_minutes", "FLOAT64", minimum),
        ],
    )


def gap_answer(row: dict[str, Any], limit: int) -> dict[str, Any]:
    matching = row["matching_intervals"]
    status = (
        "gaps_found"
        if matching
        else "insufficient_data"
        if not row["information_count"] or not row["status_count"]
        else "no_gaps"
    )
    return {
        "status": status,
        "matching_intervals": matching,
        "gaps": row["gaps"][:limit],
    }


def run(base: Path, output: Path) -> None:
    questions = base / "questions-v1.json"
    question_hash = sha256(questions.read_bytes()).hexdigest()
    cases = read(questions)["cases"]
    counts = Counter(case["expected_tool"] for case in cases)
    if len({c["case_id"] for c in cases}) != 20 or counts != {
        "get_station_availability": 6,
        "rank_stations": 8,
        "find_capture_gaps": 6,
    }:
        raise ValueError(
            "Expected the original 20 unique cases: 6 availability, 8 ranking, 6 gaps."
        )
    availability = read(base / "availability-reference-v1.json")
    if availability["questions_sha256"] != question_hash:
        raise ValueError("Saved availability references belong to different questions.")
    answers = {c["case_id"]: c["expected_answer"] for c in availability["cases"]}
    reports = {}
    for case in cases:
        if case["expected_tool"] == "get_station_availability":
            reports[case["case_id"]] = read(
                base / "availability-run-v1" / f"{case['case_id']}.json"
            )
            if case["case_id"] not in answers:
                raise ValueError("An availability reference is missing.")
    if not os.environ.get("OPENAI_API_KEY", "").strip():
        raise ValueError("Export OPENAI_API_KEY privately in this terminal first.")
    now = datetime.now(UTC)
    ranking_cases = load_cases(questions, now=now)
    output.mkdir(parents=True, exist_ok=False)
    save(
        output / "manifest.json",
        {
            "created_at_utc": now,
            "questions_sha256": question_hash,
            "reused_availability_cases": 6,
            "new_agent_questions": 14,
            "model_id": MODEL_ID,
            "grader_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
            "note": "One structured-JSON benchmark; six earlier availability runs plus 14 new runs.",
        },
    )
    with closing(bigquery.Client(project=PROJECT)) as client:
        preview_sql(client, GAP_SQL, gap_config(now, 15.0))
        print("Calculating eight independent ranking references...", flush=True)
        rows, provenance = sql_query(
            client, REFERENCE_SQL, query_config(ranking_cases, now=now)
        )
        wanted = {c.case_id for c in ranking_cases}
        if len(rows) != 8 or {r["case_id"] for r in rows} != wanted:
            raise ValueError("Ranking SQL did not return all eight unique cases.")
        for row in rows:
            answers[row["case_id"]] = {
                "status": row["status"],
                "stations": row["stations"],
            }
        save(
            output / "ranking-reference.json",
            {"questions_sha256": question_hash, **provenance, "cases": rows},
        )
        results = []
        for case in cases:
            case_id, tool = case["case_id"], case["expected_tool"]
            if tool != "get_station_availability":
                print(f"Running {case_id}...", flush=True)
                started = perf_counter()
                try:
                    ask(case["prompt"], report_path=output / f"{case_id}.json")
                except Exception as error:
                    if not (output / f"{case_id}.json").exists():
                        save(
                            output / f"{case_id}.json",
                            {
                                "question": case["prompt"],
                                "failure_type": type(error).__name__,
                                "elapsed_seconds": perf_counter() - started,
                            },
                        )
                reports[case_id] = read(output / f"{case_id}.json")
            report = reports[case_id]
            returned = payload(report, tool)
            if tool == "find_capture_gaps" and "window_end_utc" in returned:
                as_of = instant(returned["window_end_utc"])
                arguments = case["expected_arguments"]
                rows, provenance = sql_query(
                    client, GAP_SQL, gap_config(as_of, arguments["min_minutes"])
                )
                if len(rows) != 1:
                    raise ValueError(
                        "Gap SQL did not return exactly one aggregate row."
                    )
                answers[case_id] = gap_answer(rows[0], arguments["limit"])
                save(
                    output / f"{case_id}-reference.json",
                    {
                        "questions_sha256": question_hash,
                        "as_of_utc": as_of,
                        **provenance,
                        "expected_answer": answers[case_id],
                        "source_row": rows[0],
                    },
                )
            if case_id in answers:
                result = grade(case, report, answers[case_id])
            else:
                result = {
                    "case_id": case_id,
                    "tool": tool,
                    "run_complete": False,
                    "tool_correct": False,
                    "arguments_correct": False,
                    "tool_data_correct": False,
                    "answer_correct": False,
                    "passed": False,
                }
            results.append(result)
            print(f"{case_id}: {'PASS' if result['passed'] else 'FAIL'}", flush=True)
    metrics = [
        "tool_correct",
        "arguments_correct",
        "tool_data_correct",
        "answer_correct",
        "passed",
    ]
    totals = {name: sum(row[name] for row in results) for name in metrics}
    groups = {
        tool: {
            "cases": counts[tool],
            **{
                name: sum(row[name] for row in results if row["tool"] == tool)
                for name in metrics
            },
        }
        for tool in counts
    }
    summary = {
        "questions_sha256": question_hash,
        "total_cases": 20,
        "totals": totals,
        "by_tool": groups,
        "cases": results,
        "mean_elapsed_seconds": sum(r["elapsed_seconds"] for r in reports.values())
        / 20,
        "token_usage_reported": {
            name: sum(
                r.get("token_usage_reported", {}).get(name, 0) for r in reports.values()
            )
            for name in ("inputTokens", "outputTokens", "totalTokens")
        },
        "usage_reports_missing": [
            key for key, r in reports.items() if "token_usage_reported" not in r
        ],
        "note": "Scores cover these 20 structured questions. Extra JSON fields are ignored; list order and counts must match. Float tolerance: 1e-9. Usage is SDK-reported, not an invoice.",
    }
    save(output / "summary.json", summary)
    for name, count in totals.items():
        print(f"{name}: {count}/20 ({count / 20:.0%})")
    print(f"Saved results: {output / 'summary.json'}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--data", type=Path, default=Path("../data/evaluation"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.execute:
        parser.error("Execution requires --execute; this makes paid model requests.")
    run(args.data, args.output)
