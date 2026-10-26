"""Read completed capture intervals from the existing two-day dbt view."""

import math
from typing import Literal

from google.api_core.exceptions import BadRequest, Forbidden, GoogleAPICallError
from google.auth.exceptions import DefaultCredentialsError
from google.cloud import bigquery
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import AwareDatetime, BaseModel, Field

from history import LOCATION, MAXIMUM_BYTES_BILLED, NO_RETRY, PROJECT

GAPS_SQL = """
SELECT
    TIMESTAMP(DATE_SUB(CURRENT_DATE('UTC'), INTERVAL 1 DAY)) AS window_start_utc,
    CURRENT_TIMESTAMP() AS window_end_utc,
    COUNTIF(feed_name = 'station_information' AND gap_minutes IS NOT NULL)
        AS information_intervals_checked,
    COUNTIF(feed_name = 'station_status' AND gap_minutes IS NOT NULL)
        AS status_intervals_checked,
    COUNTIF(gap_minutes >= @min_minutes) AS matching_intervals,
    IFNULL(ARRAY_AGG(
        IF(gap_minutes >= @min_minutes,
            STRUCT(feed_name, capture_id, previous_received_at,
                   capture_received_at, gap_minutes), NULL)
        IGNORE NULLS
        ORDER BY gap_minutes DESC, capture_received_at, feed_name, capture_id
        LIMIT 50
    ), []) AS gaps
FROM `divvy-ai.divvyai_analytics.mart_capture_intervals`
WHERE capture_received_at >=
    TIMESTAMP(DATE_SUB(CURRENT_DATE('UTC'), INTERVAL 1 DAY))
  AND capture_received_at < CURRENT_TIMESTAMP()
"""


class CaptureGap(BaseModel):
    feed_name: Literal["station_information", "station_status"]
    capture_id: str
    previous_received_at: AwareDatetime
    capture_received_at: AwareDatetime
    gap_minutes: float


class CaptureGapReport(BaseModel):
    min_minutes: float
    window_start_utc: AwareDatetime
    window_end_utc: AwareDatetime
    status: Literal["gaps_found", "no_gaps", "insufficient_data"]
    information_intervals_checked: int
    status_intervals_checked: int
    matching_intervals: int
    gaps: list[CaptureGap] = Field(max_length=50)
    truncated: bool
    bytes_processed: int
    bytes_billed: int | None
    note: str = (
        "Intervals at least min_minutes, from yesterday's UTC midnight to query "
        "time; this is not a rolling 48-hour history. Largest gaps appear first. "
        "Both feeds are reported separately and can describe the same "
        "collection delay. These are intervals between observed receipts, not proven "
        "failed executions. No matches does not establish healthy collection or "
        "detect an ongoing outage after the last capture."
    )


def validate_gap_parameters(min_minutes: float, limit: int) -> None:
    if (
        isinstance(min_minutes, bool)
        or not isinstance(min_minutes, (int, float))
        or not math.isfinite(min_minutes)
        or not 0 < min_minutes <= 1440
    ):
        raise ToolError("min_minutes must be a finite number above 0 and at most 1440.")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
        raise ToolError("limit must be an integer from 1 to 50.")


def gaps_config(
    min_minutes: float, *, dry_run: bool = False
) -> bigquery.QueryJobConfig:
    return bigquery.QueryJobConfig(
        use_legacy_sql=False,
        maximum_bytes_billed=MAXIMUM_BYTES_BILLED,
        dry_run=dry_run,
        use_query_cache=not dry_run,
        query_parameters=[
            bigquery.ScalarQueryParameter("min_minutes", "FLOAT64", min_minutes)
        ],
    )


def read_capture_gaps(
    min_minutes: float = 30.0,
    limit: int = 20,
    *,
    client: bigquery.Client | None = None,
) -> CaptureGapReport:
    validate_gap_parameters(min_minutes, limit)
    try:
        client = client if client is not None else bigquery.Client(project=PROJECT)
        job = client.query(
            GAPS_SQL,
            job_config=gaps_config(min_minutes),
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
            "BigQuery rejected the gap query. It may exceed the 256 MiB scan cap; "
            "the tool will not raise that cap automatically."
        ) from exc
    except (GoogleAPICallError, TimeoutError) as exc:
        raise ToolError(
            "BigQuery did not complete. No answer is available; retry later."
        ) from exc

    if len(rows) != 1:
        raise ToolError("BigQuery returned an unexpected capture-interval result.")
    if job.total_bytes_processed is None:
        raise ToolError("BigQuery did not provide gap-query scan accounting.")
    row = rows[0]
    information_count = int(row["information_intervals_checked"])
    status_count = int(row["status_intervals_checked"])
    matching = int(row["matching_intervals"])
    gaps = [CaptureGap.model_validate(dict(item)) for item in row["gaps"][:limit]]
    status: Literal["gaps_found", "no_gaps", "insufficient_data"]
    if matching:
        status = "gaps_found"
    elif information_count == 0 or status_count == 0:
        status = "insufficient_data"
    else:
        status = "no_gaps"
    return CaptureGapReport(
        min_minutes=min_minutes,
        window_start_utc=row["window_start_utc"],
        window_end_utc=row["window_end_utc"],
        status=status,
        information_intervals_checked=information_count,
        status_intervals_checked=status_count,
        matching_intervals=matching,
        gaps=gaps,
        truncated=matching > len(gaps),
        bytes_processed=job.total_bytes_processed,
        bytes_billed=job.total_bytes_billed,
    )
