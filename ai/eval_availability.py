"""Validate or save independent SQL answers for six availability cases."""

import argparse
import json
import math
import os
from contextlib import closing
from datetime import UTC, date, datetime, time, timedelta
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal

from google.api_core.retry import Retry
from google.cloud import bigquery
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from models import Hour

PROJECT = "divvy-ai"
LOCATION = "us-central1"
MAXIMUM_BYTES_BILLED = 512 * 1024 * 1024
NO_RETRY = Retry(predicate=lambda _: False)

REFERENCE_SQL = """
WITH requested AS (
    SELECT * FROM UNNEST(@cases)
), observations AS (
    SELECT
        _PARTITIONDATE AS ingestion_date, station_id, received_at,
        SAFE_CAST(JSON_VALUE(payload, '$.num_bikes_available') AS INT64) AS bikes,
        SAFE_CAST(JSON_VALUE(payload, '$.num_docks_available') AS INT64) AS docks
    FROM `divvy-ai.divvyai_raw.station_records`
    WHERE _PARTITIONDATE BETWEEN @first_ingestion_date AND @last_ingestion_date
      AND feed_name = 'station_status'
), hourly AS (
    SELECT
        c.case_id,
        IF(o.received_at >= c.start_at AND o.received_at < c.end_at,
            TIMESTAMP_TRUNC(o.received_at, HOUR, 'UTC'), NULL)
            AS hour_started_at_utc,
        COUNT(o.station_id) AS snapshot_count,
        COUNT(o.bikes) AS bike_sample_count,
        COUNT(o.docks) AS dock_sample_count,
        AVG(o.bikes) AS avg_bikes_available,
        AVG(o.docks) AS avg_docks_available
    FROM requested AS c
    LEFT JOIN observations AS o
      ON o.station_id = c.station_id
      AND o.ingestion_date >= DATE(c.start_at, 'UTC')
    GROUP BY 1, 2
)
SELECT
    case_id,
    LOGICAL_OR(snapshot_count > 0) AS station_seen,
    IF(COUNTIF(hour_started_at_utc IS NOT NULL) > 0, 'ok', 'no_data') AS status,
    IFNULL(ARRAY_AGG(
        IF(hour_started_at_utc IS NOT NULL,
            STRUCT(hour_started_at_utc, snapshot_count, bike_sample_count,
                   dock_sample_count, avg_bikes_available, avg_docks_available),
            NULL)
        IGNORE NULLS ORDER BY hour_started_at_utc
    ), []) AS hours
FROM hourly
GROUP BY case_id
ORDER BY case_id
"""


class SavedCase(BaseModel):
    case_id: str
    expected_tool: str
    expected_arguments: dict[str, object]


class QuestionSet(BaseModel):
    schema_version: Literal[1]
    cases: list[SavedCase] = Field(min_length=20, max_length=20)


class AvailabilityCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str
    station_id: str = Field(min_length=1, max_length=128)
    start: AwareDatetime
    end: AwareDatetime


def load_cases(path: Path, *, today: date) -> list[AvailabilityCase]:
    document = QuestionSet.model_validate_json(path.read_bytes())
    if len({case.case_id for case in document.cases}) != 20:
        raise ValueError("Saved case IDs must be unique.")
    cases = [
        AvailabilityCase.model_validate(
            {**case.expected_arguments, "case_id": case.case_id}
        )
        for case in document.cases
        if case.expected_tool == "get_station_availability"
    ]
    if len(cases) != 6:
        raise ValueError("Expected exactly six saved availability cases.")
    midnight = datetime.combine(today, time.min, UTC)
    for case in cases:
        case.start, case.end = case.start.astimezone(UTC), case.end.astimezone(UTC)
        if not case.station_id.strip() or any(
            (value.minute, value.second, value.microsecond) != (0, 0, 0)
            for value in (case.start, case.end)
        ):
            raise ValueError("Use nonblank station IDs and whole-hour boundaries.")
        if not midnight - timedelta(days=7) <= case.start < case.end <= midnight:
            raise ValueError("Use completed UTC hours within the previous seven days.")
    return cases


def query_config(
    cases: list[AvailabilityCase], *, today: date, dry_run: bool = True
) -> bigquery.QueryJobConfig:
    return bigquery.QueryJobConfig(
        use_legacy_sql=False,
        dry_run=dry_run,
        use_query_cache=False,
        maximum_bytes_billed=MAXIMUM_BYTES_BILLED,
        query_parameters=[
            bigquery.ArrayQueryParameter(
                "cases",
                "STRUCT",
                [
                    bigquery.StructQueryParameter(
                        None,
                        bigquery.ScalarQueryParameter(
                            "case_id", "STRING", case.case_id
                        ),
                        bigquery.ScalarQueryParameter(
                            "station_id", "STRING", case.station_id
                        ),
                        bigquery.ScalarQueryParameter(
                            "start_at", "TIMESTAMP", case.start
                        ),
                        bigquery.ScalarQueryParameter("end_at", "TIMESTAMP", case.end),
                    )
                    for case in cases
                ],
            ),
            bigquery.ScalarQueryParameter(
                "first_ingestion_date", "DATE", min(case.start.date() for case in cases)
            ),
            bigquery.ScalarQueryParameter("last_ingestion_date", "DATE", today),
        ],
    )


def estimate(
    client: bigquery.Client, cases: list[AvailabilityCase], *, today: date
) -> int:
    job = client.query(
        REFERENCE_SQL,
        job_config=query_config(cases, today=today),
        location=LOCATION,
        timeout=30,
        retry=NO_RETRY,
        job_retry=None,
    )
    size = job.total_bytes_processed
    if size is None:
        raise ValueError("Scan estimate is missing: BigQuery returned None.")
    if size < 0:
        raise ValueError(f"Scan estimate is invalid: {size} bytes.")
    if size > MAXIMUM_BYTES_BILLED:
        raise ValueError(
            f"Scan estimate is above the cap: {size} bytes "
            f"({size / 1048576:.2f} MiB); maximum is "
            f"{MAXIMUM_BYTES_BILLED} bytes "
            f"({MAXIMUM_BYTES_BILLED / 1048576:.0f} MiB)."
        )
    return int(size)


def save_answers(
    client: bigquery.Client,
    cases: list[AvailabilityCase],
    output: Path,
    *,
    today: date,
    questions_sha256: str,
) -> dict[str, object]:
    if output.exists() or output.is_symlink():
        raise ValueError("Output already exists; choose a new filename.")
    output.parent.mkdir(parents=True, exist_ok=True)
    estimated = estimate(client, cases, today=today)
    job = client.query(
        REFERENCE_SQL,
        job_config=query_config(cases, today=today, dry_run=False),
        location=LOCATION,
        timeout=30,
        retry=NO_RETRY,
        job_retry=None,
    )
    rows = list(job.result(timeout=60, retry=None, job_retry=None))
    processed, billed = job.total_bytes_processed, job.total_bytes_billed
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value < 0
        for value in (processed, billed)
    ):
        raise ValueError("Query scan accounting is missing or invalid.")
    if billed > MAXIMUM_BYTES_BILLED:
        raise ValueError("Query accounting exceeded the 512 MiB cap.")
    expected = {case.case_id: case for case in cases}
    answers: dict[str, dict[str, object]] = {}
    for row in rows:
        case_id = str(row["case_id"])
        if case_id not in expected or case_id in answers:
            raise ValueError("Reference rows contain unknown or duplicate case IDs.")
        if row["station_seen"] is not True:
            raise ValueError(f"{case_id}: station was not found; no reference saved.")
        case = expected[case_id]
        hours = [Hour.model_validate(dict(hour)) for hour in row["hours"]]
        times = [hour.hour_started_at_utc.astimezone(UTC) for hour in hours]
        if times != sorted(set(times)) or row["status"] != (
            "ok" if hours else "no_data"
        ):
            raise ValueError(f"{case_id}: inconsistent status or hourly ordering.")
        for hour, instant in zip(hours, times, strict=True):
            if not case.start <= instant < case.end or (
                instant.minute,
                instant.second,
                instant.microsecond,
            ) != (0, 0, 0):
                raise ValueError(f"{case_id}: hour falls outside its requested window.")
            if not (
                0 <= hour.bike_sample_count <= hour.snapshot_count
                and 0 <= hour.dock_sample_count <= hour.snapshot_count
                and hour.snapshot_count > 0
            ):
                raise ValueError(f"{case_id}: invalid sample counts.")
            for mean, count in (
                (hour.avg_bikes_available, hour.bike_sample_count),
                (hour.avg_docks_available, hour.dock_sample_count),
            ):
                if (mean is None) != (count == 0) or (
                    mean is not None and not math.isfinite(mean)
                ):
                    raise ValueError(f"{case_id}: invalid average or null handling.")
            hour.hour_started_at_utc = instant
        answers[case_id] = {
            "status": row["status"],
            "hours": [hour.model_dump(mode="json") for hour in hours],
        }
    if set(answers) != set(expected):
        raise ValueError("The query did not return every expected availability case.")
    document: dict[str, object] = {
        "schema_version": 1,
        "reference_kind": "independent_sql_availability",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "questions_sha256": questions_sha256,
        "sql_sha256": sha256(REFERENCE_SQL.encode()).hexdigest(),
        "ingestion_start_date": min(case.start.date() for case in cases).isoformat(),
        "ingestion_end_date": today.isoformat(),
        "query_job_id": job.job_id,
        "query_project": job.project,
        "query_location": job.location,
        "query_started_at_utc": job.started.isoformat() if job.started else None,
        "query_ended_at_utc": job.ended.isoformat() if job.ended else None,
        "estimated_bytes_processed": estimated,
        "bytes_processed": processed,
        "bytes_billed": billed,
        "cases": [
            {
                "case_id": case.case_id,
                "expected_tool": "get_station_availability",
                "expected_arguments": case.model_dump(mode="json", exclude={"case_id"}),
                "expected_answer": answers[case.case_id],
            }
            for case in cases
        ],
        "note": "Data visible to this query; later uploads can change historical answers.",
    }
    text = json.dumps(document, indent=2, allow_nan=False) + "\n"
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=output.parent, delete=False
        ) as destination:
            temporary = Path(destination.name)
            destination.write(text)
        os.link(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return document


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("questions", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.execute != (args.output is not None):
        parser.error("Use --execute and --output together.")
    if args.output is not None and (args.output.exists() or args.output.is_symlink()):
        parser.error("Output already exists; choose a new filename.")
    today = datetime.now(UTC).date()
    cases = load_cases(args.questions, today=today)
    with closing(bigquery.Client(project=PROJECT)) as client:
        if args.execute:
            report = save_answers(
                client,
                cases,
                args.output,
                today=today,
                questions_sha256=sha256(args.questions.read_bytes()).hexdigest(),
            )
            print(f"Saved {len(cases)} SQL reference answers to {args.output}")
            print(f"Bytes billed: {report['bytes_billed']}")
            raise SystemExit(0)
        size = estimate(client, cases, today=today)
    first = min(case.start.date() for case in cases)
    print(f"Availability cases: {len(cases)}")
    print(f"UTC ingestion dates: {first} through {today}, inclusive")
    print(f"Estimated scan: {size} bytes ({size / 1048576:.2f} MiB)")
    print(
        f"Query cap: {MAXIMUM_BYTES_BILLED} bytes ({MAXIMUM_BYTES_BILLED / 1048576:.0f} MiB)"
    )
    print("Dry run only; no reference answers were calculated or saved.")
