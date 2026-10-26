"""Dry-run independent SQL reference answers for eight saved ranking cases."""

import argparse
from contextlib import closing
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from google.cloud import bigquery
from pydantic import BaseModel, ConfigDict, Field

from eval_availability import (
    LOCATION,
    MAXIMUM_BYTES_BILLED,
    NO_RETRY,
    PROJECT,
    QuestionSet,
)

CHICAGO = ZoneInfo("America/Chicago")
REFERENCE_SQL = """
WITH requested AS (
    SELECT * FROM UNNEST(@cases)
), latest_feed AS (
    SELECT
        _PARTITIONDATE AS ingestion_date,
        capture_id, station_id, feed_name, received_at,
        SAFE_CAST(JSON_VALUE(payload, '$.num_bikes_available') AS INT64) AS bikes,
        SAFE_CAST(JSON_VALUE(payload, '$.num_docks_available') AS INT64) AS docks,
        SAFE_CAST(JSON_VALUE(payload, '$.lat') AS FLOAT64) AS latitude,
        SAFE_CAST(JSON_VALUE(payload, '$.lon') AS FLOAT64) AS longitude
    FROM `divvy-ai.divvyai_raw.station_records`
    WHERE _PARTITIONDATE BETWEEN @first_ingestion_date AND @last_ingestion_date
      AND feed_name IN ('station_status', 'station_information')
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY _PARTITIONDATE, capture_id, station_id, feed_name
        ORDER BY received_at DESC
    ) = 1
), paired AS (
    SELECT
        ingestion_date, capture_id, station_id,
        MAX(IF(feed_name = 'station_status', received_at, NULL)) AS status_at,
        MAX(IF(feed_name = 'station_status', bikes, NULL)) AS bikes,
        MAX(IF(feed_name = 'station_status', docks, NULL)) AS docks,
        MAX(IF(feed_name = 'station_information', latitude, NULL)) AS latitude,
        MAX(IF(feed_name = 'station_information', longitude, NULL)) AS longitude
    FROM latest_feed
    GROUP BY ingestion_date, capture_id, station_id
), observations AS (
    SELECT
        c.case_id, c.metric, p.station_id,
        DATE(p.status_at, 'America/Chicago') AS sample_day,
        IF(c.metric IN ('empty_bikes_rate', 'lowest_avg_bikes'),
            p.bikes, p.docks) AS available
    FROM paired AS p
    JOIN requested AS c
      ON p.ingestion_date >= DATE(TIMESTAMP(c.start_date, 'America/Chicago'))
      AND p.status_at >= TIMESTAMP(c.start_date, 'America/Chicago')
      AND p.status_at < TIMESTAMP(c.end_date, 'America/Chicago')
      AND EXTRACT(HOUR FROM DATETIME(p.status_at, 'America/Chicago')) = c.hour
    WHERE (c.day_type = 'all'
        OR (c.day_type = 'weekdays' AND EXTRACT(DAYOFWEEK FROM
            DATE(p.status_at, 'America/Chicago')) BETWEEN 2 AND 6)
        OR (c.day_type = 'weekends' AND EXTRACT(DAYOFWEEK FROM
            DATE(p.status_at, 'America/Chicago')) IN (1, 7)))
      AND (c.area = 'all' OR (
          p.latitude BETWEEN 41.85 AND 41.91
          AND p.longitude BETWEEN -87.66 AND -87.60))
), totals AS (
    SELECT
        case_id, metric, station_id,
        COUNTIF(available >= 0) AS valid_sample_count,
        COUNTIF(available = 0) AS zero_sample_count,
        SUM(IF(available >= 0, available, NULL)) AS total_available,
        COUNT(DISTINCT IF(available >= 0, sample_day, NULL)) AS sampled_days
    FROM observations
    GROUP BY case_id, metric, station_id
), scores AS (
    SELECT *,
        IF(metric IN ('empty_bikes_rate', 'empty_docks_rate'),
            SAFE_DIVIDE(zero_sample_count, valid_sample_count),
            SAFE_DIVIDE(total_available, valid_sample_count)) AS metric_value
    FROM totals
    WHERE valid_sample_count > 0
), ranked AS (
    SELECT *, ROW_NUMBER() OVER (
        PARTITION BY case_id
        ORDER BY IF(metric IN ('empty_bikes_rate', 'empty_docks_rate'),
            -metric_value, metric_value), valid_sample_count DESC, station_id
    ) AS position
    FROM scores
)
SELECT
    c.case_id,
    IF(COUNT(r.station_id) > 0, 'ok', 'no_data') AS status,
    IFNULL(ARRAY_AGG(
        IF(r.station_id IS NOT NULL,
            STRUCT(r.station_id, r.metric_value, r.zero_sample_count,
                   r.valid_sample_count, r.sampled_days), NULL)
        IGNORE NULLS ORDER BY r.position
    ), []) AS stations
FROM requested AS c
LEFT JOIN ranked AS r
  ON c.case_id = r.case_id AND r.position <= c.result_limit
GROUP BY c.case_id
ORDER BY c.case_id
"""


class RankingCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str
    metric: Literal[
        "empty_bikes_rate", "empty_docks_rate", "lowest_avg_bikes", "lowest_avg_docks"
    ]
    hour: int = Field(strict=True, ge=0, le=23)
    limit: int = Field(strict=True, ge=1, le=20)
    start_date: date
    end_date: date
    day_type: Literal["all", "weekdays", "weekends"]
    area: Literal["all", "downtown_demo"]


def ingestion_bounds(cases: list[RankingCase], now: datetime) -> tuple[date, date]:
    if now.utcoffset() is None:
        raise ValueError("now must include a timezone.")
    if len(cases) != 8 or len({case.case_id for case in cases}) != 8:
        raise ValueError("Expected eight unique ranking cases.")
    today = now.astimezone(CHICAGO).date()
    for case in cases:
        if not today - timedelta(days=7) <= case.start_date < case.end_date <= today:
            raise ValueError("Use completed Chicago dates within the previous week.")
    first = min(
        datetime.combine(case.start_date, time.min, CHICAGO).astimezone(UTC).date()
        for case in cases
    )
    last = now.astimezone(UTC).date()
    if not 0 <= (last - first).days <= 8:
        raise ValueError("Ranking references exceed nine ingestion dates.")
    return first, last


def load_cases(path: Path, *, now: datetime) -> list[RankingCase]:
    document = QuestionSet.model_validate_json(path.read_bytes())
    if len({case.case_id for case in document.cases}) != 20:
        raise ValueError("Saved case IDs must be unique.")
    cases = [
        RankingCase.model_validate({**case.expected_arguments, "case_id": case.case_id})
        for case in document.cases
        if case.expected_tool == "rank_stations"
    ]
    ingestion_bounds(cases, now)
    return cases


def query_config(cases: list[RankingCase], *, now: datetime) -> bigquery.QueryJobConfig:
    first, last = ingestion_bounds(cases, now)
    fields = {
        "case_id": "STRING",
        "metric": "STRING",
        "hour": "INT64",
        "limit": "INT64",
        "start_date": "DATE",
        "end_date": "DATE",
        "day_type": "STRING",
        "area": "STRING",
    }
    return bigquery.QueryJobConfig(
        use_legacy_sql=False,
        dry_run=True,
        use_query_cache=False,
        maximum_bytes_billed=MAXIMUM_BYTES_BILLED,
        query_parameters=[
            bigquery.ArrayQueryParameter(
                "cases",
                "STRUCT",
                [
                    bigquery.StructQueryParameter(
                        None,
                        *[
                            bigquery.ScalarQueryParameter(
                                "result_limit" if name == "limit" else name,
                                sql_type,
                                getattr(case, name),
                            )
                            for name, sql_type in fields.items()
                        ],
                    )
                    for case in cases
                ],
            ),
            bigquery.ScalarQueryParameter("first_ingestion_date", "DATE", first),
            bigquery.ScalarQueryParameter("last_ingestion_date", "DATE", last),
        ],
    )


def estimate(
    client: bigquery.Client, cases: list[RankingCase], *, now: datetime
) -> int:
    job = client.query(
        REFERENCE_SQL,
        job_config=query_config(cases, now=now),
        location=LOCATION,
        timeout=30,
        retry=NO_RETRY,
        job_retry=None,
    )
    size = job.total_bytes_processed
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        raise ValueError(f"Missing or invalid scan estimate: {size!r}.")
    if size > MAXIMUM_BYTES_BILLED:
        raise ValueError(
            f"Ranking reference estimate: {size} bytes ({size / 1048576:.2f} MiB); "
            f"exceeds the {MAXIMUM_BYTES_BILLED / 1048576:.0f} MiB cap."
        )
    return size


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("questions", type=Path)
    args = parser.parse_args()
    now = datetime.now(UTC)
    cases = load_cases(args.questions, now=now)
    first, last = ingestion_bounds(cases, now)
    with closing(bigquery.Client(project=PROJECT)) as client:
        size = estimate(client, cases, now=now)
    print(f"Ranking cases: {len(cases)}")
    print(f"UTC ingestion dates: {first} through {last}, inclusive")
    print(f"Estimated scan: {size} bytes ({size / 1048576:.2f} MiB)")
    print(f"Reference query cap: {MAXIMUM_BYTES_BILLED / 1048576:.0f} MiB")
    print("Dry run only; no data query, model call or reference file was produced.")
