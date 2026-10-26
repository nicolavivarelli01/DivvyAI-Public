"""Server-side logic for reading station availability from BigQuery."""

from datetime import UTC, date, datetime, timedelta
from typing import Annotated

from google.api_core.exceptions import BadRequest, Forbidden, GoogleAPICallError
from google.api_core.retry import Retry
from google.auth.exceptions import DefaultCredentialsError
from google.cloud import bigquery
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import AwareDatetime, Field

from gaps import CaptureGapReport, read_capture_gaps
from history import read_history
from models import Availability, Hour
from ranking import Area, DayType, RankingReport, RankMetric, read_rankings

PROJECT = "divvy-ai"
LOCATION = "us-central1"
MAXIMUM_BYTES_BILLED = 268435456
NO_RETRY = Retry(predicate=lambda _: False)

SQL = """
SELECT
    CURRENT_DATE('UTC') AS data_date_utc,
    COUNT(*) > 0 AS station_seen_today,
    IFNULL(ARRAY_AGG(
        IF(hour_started_at_utc >= @start AND hour_started_at_utc < @end,
            STRUCT(hour_started_at_utc, snapshot_count, bike_sample_count,
                   dock_sample_count, avg_bikes_available, avg_docks_available),
            NULL)
        IGNORE NULLS ORDER BY hour_started_at_utc
    ), []) AS hours
FROM `divvy-ai.divvyai_analytics.mart_station_hourly_availability`
WHERE station_id = @station_id
  AND DATE(hour_started_at_utc, 'UTC') = CURRENT_DATE('UTC')
"""


def validate_window(
    station_id: str, start: datetime, end: datetime, *, today: date
) -> tuple[datetime, datetime]:
    if not station_id.strip() or len(station_id) > 128:
        raise ToolError("station_id must contain 1 to 128 nonblank characters.")
    if start.utcoffset() is None or end.utcoffset() is None:
        raise ToolError("Include a timezone in start and end, such as +00:00.")
    start, end = start.astimezone(UTC), end.astimezone(UTC)
    if start >= end:
        raise ToolError("start must be earlier than end.")
    if any(
        (value.minute, value.second, value.microsecond) != (0, 0, 0)
        for value in (start, end)
    ):
        raise ToolError("Use whole-hour boundaries, for example 08:00:00+00:00.")
    midnight = datetime.combine(today, datetime.min.time(), tzinfo=UTC)
    if end - start > timedelta(days=7):
        raise ToolError("Request at most seven days at a time.")
    if start < midnight - timedelta(days=7) or end > midnight + timedelta(days=1):
        raise ToolError("Use recent dates: the previous seven UTC days or today.")
    return start, end


def query_config(
    station_id: str, start: datetime, end: datetime, *, dry_run: bool = False
) -> bigquery.QueryJobConfig:
    return bigquery.QueryJobConfig(
        use_legacy_sql=False,
        maximum_bytes_billed=MAXIMUM_BYTES_BILLED,
        dry_run=dry_run,
        use_query_cache=not dry_run,
        query_parameters=[
            bigquery.ScalarQueryParameter("station_id", "STRING", station_id),
            bigquery.ScalarQueryParameter("start", "TIMESTAMP", start),
            bigquery.ScalarQueryParameter("end", "TIMESTAMP", end),
        ],
    )


def read_availability(
    station_id: str,
    start: datetime,
    end: datetime,
    *,
    client: bigquery.Client | None = None,
    today: date | None = None,
) -> Availability:
    today = today or datetime.now(UTC).date()
    start, end = validate_window(station_id, start, end, today=today)
    try:
        client = client if client is not None else bigquery.Client(project=PROJECT)
        if start.date() < today:
            return read_history(client, station_id, start, end, today=today)
        job = client.query(
            SQL,
            job_config=query_config(station_id, start, end),
            location=LOCATION,
            timeout=30,
            retry=NO_RETRY,
            job_retry=None,
        )
        rows = list(job.result(timeout=60, retry=None, job_retry=None))
    except DefaultCredentialsError as exc:
        raise ToolError("ADC is unavailable. Refresh your Google Cloud login.") from exc
    except Forbidden as exc:
        raise ToolError(
            "BigQuery access was denied. Check the reader's permissions."
        ) from exc
    except BadRequest as exc:
        raise ToolError(
            "BigQuery rejected the query. It may exceed the 256 MiB scan cap; "
            "the tool will not raise that cap automatically."
        ) from exc
    except (GoogleAPICallError, TimeoutError) as exc:
        raise ToolError(
            "BigQuery did not complete. No answer is available; retry later."
        ) from exc

    if len(rows) != 1 or rows[0]["data_date_utc"] != today:
        raise ToolError("The UTC day changed or the result was unexpected. Retry.")
    row = rows[0]
    if not row["station_seen_today"]:
        raise ToolError(
            "Station ID not found in today's observations. Check the ID and "
            "collection freshness; this does not prove the station never existed."
        )
    hours = [Hour.model_validate(dict(hour)) for hour in row["hours"]]
    return Availability(
        station_id=station_id,
        start=start,
        end=end,
        status="ok" if hours else "no_data",
        hours=hours,
        bytes_processed=int(job.total_bytes_processed or 0),
        bytes_billed=job.total_bytes_billed,
    )


def build_server() -> MCPServer[None]:
    server = MCPServer[None]("DivvyAI read-only analytics")

    @server.tool(
        annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False),
        structured_output=True,
    )
    def get_station_availability(
        station_id: str, start: AwareDatetime, end: AwareDatetime
    ) -> Availability:
        """Read one station's hourly bike/dock averages and sample counts.

        Supply an exact station ID and timezone-aware, whole-hour timestamps.
        Start is inclusive; end is exclusive. Request at most seven days, with
        start in the previous seven UTC days or today. Returns at most 168 hours.
        Today uses the hourly view; history reads existing raw observations,
        including later uploads that are visible now. Missing hours and null
        averages mean unknown. These are sampled averages, not predictions.
        History uses at most eight partition queries, capped at 256 MiB each
        and 1 GiB billed across the request. No data is modified.
        """
        return read_availability(station_id, start, end)

    @server.tool(
        annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False),
        structured_output=True,
    )
    def find_capture_gaps(
        min_minutes: Annotated[
            float, Field(gt=0, le=1440, strict=True, allow_inf_nan=False)
        ] = 30.0,
        limit: Annotated[int, Field(ge=1, le=50, strict=True)] = 20,
    ) -> CaptureGapReport:
        """Find per-feed capture intervals at least min_minutes long.

        Covers yesterday 00:00 UTC through the query time, using the existing
        capture-interval view. Returns the largest gaps first, up to limit.
        The two feeds can describe the same collection gap; do not count those
        as independent failed jobs. Empty results do not prove collection is
        fresh: only closed intervals between observed captures are measured.
        Use 0 < min_minutes <= 1440 and an integer limit from 1 to 50.
        """
        return read_capture_gaps(min_minutes, limit)

    @server.tool(
        annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False),
        structured_output=True,
    )
    def rank_stations(
        metric: RankMetric,
        hour: Annotated[int, Field(ge=0, le=23, strict=True)],
        limit: Annotated[int, Field(ge=1, le=20, strict=True)] = 5,
        start_date: date | None = None,
        end_date: date | None = None,
        day_type: DayType = "all",
        area: Area = "all",
    ) -> RankingReport:
        """Rank observed station availability for one Chicago local hour.

        empty_bikes_rate and empty_docks_rate rank highest observed zero-count
        fractions first. lowest_avg_bikes and lowest_avg_docks rank lowest
        sampled counts first. Rates are fractions from 0 to 1, not percentages.
        Hour 8 includes 08:00 through 08:59 in America/Chicago. Dates are local,
        start inclusive and end exclusive; supply both or neither. Defaults to
        the previous three completed local dates; at most seven recent completed
        dates are allowed. day_type can select weekdays or weekends.
        downtown_demo is an illustrative rectangle (latitude 41.85 to 41.91,
        longitude -87.66 to -87.60), not an official downtown boundary.
        Returns sample/day counts; missing counts are unknown. Station operating
        flags are not filtered. These are receipt-time observations, not exact
        empty durations or predictions. No data is modified.
        """
        return read_rankings(
            metric,
            hour,
            limit,
            start_date=start_date,
            end_date=end_date,
            day_type=day_type,
            area=area,
        )

    return server


if __name__ == "__main__":
    build_server().run(transport="stdio")
