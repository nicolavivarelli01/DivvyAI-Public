"""Fixed, bounded historical queries; callers validate whole-hour UTC windows."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from google.api_core.retry import Retry
from google.cloud import bigquery
from mcp.server.mcpserver.exceptions import ToolError

from models import Availability, Hour

PROJECT = "divvy-ai"
LOCATION = "us-central1"
MAXIMUM_BYTES_BILLED = 268435456
MAXIMUM_REQUEST_BYTES = 1073741824
NO_RETRY = Retry(predicate=lambda _: False)

RAW_SQL = """
WITH observations AS (
    SELECT
        TIMESTAMP_TRUNC(received_at, HOUR, 'UTC') AS hour_started_at_utc,
        SAFE_CAST(JSON_VALUE(payload, '$.num_bikes_available') AS INT64) AS bikes,
        SAFE_CAST(JSON_VALUE(payload, '$.num_docks_available') AS INT64) AS docks
    FROM `divvy-ai.divvyai_raw.station_records`
    WHERE _PARTITIONDATE = @ingestion_date
      AND feed_name = 'station_status'
      AND station_id = @station_id
), hourly AS (
    SELECT
        hour_started_at_utc,
        COUNT(*) AS snapshot_count,
        COUNT(bikes) AS bike_sample_count,
        COUNT(docks) AS dock_sample_count,
        SUM(bikes) AS bike_sum,
        SUM(docks) AS dock_sum
    FROM observations
    GROUP BY hour_started_at_utc
)
SELECT
    COUNT(*) > 0 AS station_seen,
    IFNULL(ARRAY_AGG(
        IF(hour_started_at_utc >= @start AND hour_started_at_utc < @end,
            STRUCT(hour_started_at_utc, snapshot_count, bike_sample_count,
                   dock_sample_count, bike_sum, dock_sum), NULL)
        IGNORE NULLS ORDER BY hour_started_at_utc
    ), []) AS hours
FROM hourly
"""


def history_dates(start: datetime, today: date) -> list[date]:
    """Include later ingestion days, so delayed loads are not silently omitted."""
    if start.utcoffset() is None:
        raise ToolError("Historical timestamps must include a timezone.")
    first = start.astimezone(UTC).date()
    span = (today - first).days
    if not 0 <= span <= 7:
        raise ToolError("Historical reads must fit within eight ingestion dates.")
    return [first + timedelta(days=offset) for offset in range(span + 1)]


def _config(
    station_id: str,
    start: datetime,
    end: datetime,
    ingestion_date: date,
    *,
    dry_run: bool,
    maximum_bytes_billed: int = MAXIMUM_BYTES_BILLED,
) -> bigquery.QueryJobConfig:
    return bigquery.QueryJobConfig(
        use_legacy_sql=False,
        dry_run=dry_run,
        use_query_cache=not dry_run,
        maximum_bytes_billed=maximum_bytes_billed,
        query_parameters=[
            bigquery.ScalarQueryParameter("station_id", "STRING", station_id),
            bigquery.ScalarQueryParameter("start", "TIMESTAMP", start),
            bigquery.ScalarQueryParameter("end", "TIMESTAMP", end),
            bigquery.ScalarQueryParameter("ingestion_date", "DATE", ingestion_date),
        ],
    )


def estimate_history(
    client: bigquery.Client,
    station_id: str,
    start: datetime,
    end: datetime,
    *,
    today: date,
) -> int:
    estimated = 0
    for day in history_dates(start, today):
        job = client.query(
            RAW_SQL,
            job_config=_config(station_id, start, end, day, dry_run=True),
            location=LOCATION,
            timeout=30,
            retry=NO_RETRY,
            job_retry=None,
        )
        size = job.total_bytes_processed
        if size is None or size < 0:
            raise ToolError("BigQuery did not provide a usable dry-run estimate.")
        if size > MAXIMUM_BYTES_BILLED:
            raise ToolError("A historical partition exceeds the 256 MiB query cap.")
        estimated += size
        if estimated > MAXIMUM_REQUEST_BYTES:
            raise ToolError("Historical estimates exceed the 1 GiB request cap.")
    return estimated


@dataclass
class _Totals:
    snapshots: int = 0
    bikes: int = 0
    docks: int = 0
    bike_sum: int = 0
    dock_sum: int = 0


def read_history(
    client: bigquery.Client,
    station_id: str,
    start: datetime,
    end: datetime,
    *,
    today: date,
) -> Availability:
    estimate_history(client, station_id, start, end, today=today)
    totals: dict[datetime, _Totals] = {}
    billed = processed = 0
    station_seen = False
    days = history_dates(start, today)
    for day in days:
        remaining = MAXIMUM_REQUEST_BYTES - billed
        if remaining <= 0:
            raise ToolError("The 1 GiB historical request budget is exhausted.")
        job = client.query(
            RAW_SQL,
            job_config=_config(
                station_id,
                start,
                end,
                day,
                dry_run=False,
                maximum_bytes_billed=min(MAXIMUM_BYTES_BILLED, remaining),
            ),
            location=LOCATION,
            timeout=30,
            retry=NO_RETRY,
            job_retry=None,
        )
        rows = list(job.result(timeout=60, retry=None, job_retry=None))
        if job.total_bytes_billed is None or job.total_bytes_processed is None:
            raise ToolError("Historical query accounting is missing; stopping.")
        billed += job.total_bytes_billed
        processed += job.total_bytes_processed
        if billed > MAXIMUM_REQUEST_BYTES:
            raise ToolError("Historical query accounting exceeded the request cap.")
        if len(rows) != 1:
            raise ToolError("BigQuery returned an unexpected historical result.")
        station_seen = station_seen or bool(rows[0]["station_seen"])
        for hour in rows[0]["hours"]:
            item = totals.setdefault(hour["hour_started_at_utc"], _Totals())
            item.snapshots += int(hour["snapshot_count"])
            item.bikes += int(hour["bike_sample_count"])
            item.docks += int(hour["dock_sample_count"])
            item.bike_sum += int(hour["bike_sum"] or 0)
            item.dock_sum += int(hour["dock_sum"] or 0)
    if not station_seen:
        raise ToolError(
            "Station ID not found in the scanned ingestion dates. "
            "This does not prove that the station never existed."
        )
    hours = [
        Hour(
            hour_started_at_utc=hour,
            snapshot_count=item.snapshots,
            bike_sample_count=item.bikes,
            dock_sample_count=item.docks,
            avg_bikes_available=item.bike_sum / item.bikes if item.bikes else None,
            avg_docks_available=item.dock_sum / item.docks if item.docks else None,
        )
        for hour, item in sorted(totals.items())
    ]
    return Availability(
        station_id=station_id,
        start=start,
        end=end,
        status="ok" if hours else "no_data",
        hours=hours,
        bytes_processed=processed,
        bytes_billed=billed,
        source="raw_history",
        query_count=len(days),
        note=(
            "UTC hourly sample averages from retained raw records; start inclusive, "
            "end exclusive. Missing hours and null means are unknown. Later ingestion "
            "dates are included for delayed loads. query_count excludes dry runs."
        ),
    )
