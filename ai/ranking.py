"""Rank sampled station counts without modifying the collection pipeline."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal, get_args
from zoneinfo import ZoneInfo

from google.api_core.exceptions import BadRequest, Forbidden, GoogleAPICallError
from google.auth.exceptions import DefaultCredentialsError
from google.cloud import bigquery
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import AwareDatetime, BaseModel, Field

from history import (
    LOCATION,
    MAXIMUM_BYTES_BILLED,
    MAXIMUM_REQUEST_BYTES,
    NO_RETRY,
    PROJECT,
)

RankMetric = Literal[
    "empty_bikes_rate", "empty_docks_rate", "lowest_avg_bikes", "lowest_avg_docks"
]
DayType = Literal["all", "weekdays", "weekends"]
Area = Literal["all", "downtown_demo"]
CHICAGO = ZoneInfo("America/Chicago")
MAX_CANDIDATES = 10000
MAX_PARTITION_ROWS = MAX_CANDIDATES * 7

RANKING_SQL = """
WITH captures AS (
    SELECT
        capture_id, station_id,
        ARRAY_AGG(IF(feed_name = 'station_status',
            STRUCT(received_at, payload), NULL)
            IGNORE NULLS ORDER BY received_at DESC LIMIT 1)[SAFE_OFFSET(0)]
            AS status_record,
        ARRAY_AGG(IF(feed_name = 'station_information',
            STRUCT(received_at, payload), NULL)
            IGNORE NULLS ORDER BY received_at DESC LIMIT 1)[SAFE_OFFSET(0)]
            AS information_record
    FROM `divvy-ai.divvyai_raw.station_records`
    WHERE _PARTITIONDATE = @ingestion_date
      AND feed_name IN ('station_status', 'station_information')
    GROUP BY capture_id, station_id
), typed AS (
    SELECT
        capture_id, station_id,
        status_record.received_at AS received_at,
        SAFE_CAST(JSON_VALUE(status_record.payload,
            '$.num_bikes_available') AS INT64) AS bikes,
        SAFE_CAST(JSON_VALUE(status_record.payload,
            '$.num_docks_available') AS INT64) AS docks,
        JSON_VALUE(information_record.payload, '$.name') AS station_name,
        SAFE_CAST(JSON_VALUE(information_record.payload, '$.lat') AS FLOAT64)
            AS latitude,
        SAFE_CAST(JSON_VALUE(information_record.payload, '$.lon') AS FLOAT64)
            AS longitude
    FROM captures
    WHERE status_record IS NOT NULL
), eligible AS (
    SELECT *, DATE(received_at, 'America/Chicago') AS sample_day
    FROM typed
    WHERE received_at >= @start
      AND received_at < @end
      AND EXTRACT(HOUR FROM DATETIME(received_at, 'America/Chicago')) = @hour
      AND (@day_type = 'all'
        OR (@day_type = 'weekdays' AND EXTRACT(DAYOFWEEK FROM
            DATE(received_at, 'America/Chicago')) BETWEEN 2 AND 6)
        OR (@day_type = 'weekends' AND EXTRACT(DAYOFWEEK FROM
            DATE(received_at, 'America/Chicago')) IN (1, 7)))
      AND (@area = 'all' OR (
          latitude BETWEEN 41.85 AND 41.91
          AND longitude BETWEEN -87.66 AND -87.60))
)
SELECT
    station_id, sample_day,
    COUNT(*) AS observation_count,
    COUNTIF(bikes >= 0) AS bike_sample_count,
    COUNTIF(docks >= 0) AS dock_sample_count,
    SUM(IF(bikes >= 0, bikes, NULL)) AS bike_sum,
    SUM(IF(docks >= 0, docks, NULL)) AS dock_sum,
    COUNTIF(bikes = 0) AS zero_bike_count,
    COUNTIF(docks = 0) AS zero_dock_count,
    ARRAY_AGG(STRUCT(received_at, capture_id, station_name, latitude, longitude)
        ORDER BY received_at DESC, capture_id DESC LIMIT 1)[OFFSET(0)]
        AS latest_record
FROM eligible
GROUP BY station_id, sample_day
ORDER BY station_id, sample_day
LIMIT 70001
"""


class RankedStation(BaseModel):
    rank: int
    station_id: str
    station_name: str | None
    latitude: float | None
    longitude: float | None
    last_observed_at: AwareDatetime
    metric_value: float
    observation_count: int
    valid_sample_count: int
    invalid_sample_count: int
    zero_sample_count: int
    zero_sample_rate: float
    avg_available: float
    sampled_days: int


class RankingReport(BaseModel):
    metric: RankMetric
    hour: int
    start_date: date
    end_date: date
    day_type: DayType
    area: Area
    eligible_days: int
    status: Literal["ok", "no_data"]
    stations: list[RankedStation] = Field(max_length=20)
    total_eligible_stations: int
    truncated: bool
    query_count: int
    bytes_processed: int
    bytes_billed: int
    timezone: Literal["America/Chicago"] = "America/Chicago"
    source: Literal["raw_history"] = "raw_history"
    note: str = (
        "Local dates are start-inclusive/end-exclusive and completed Chicago days; "
        "hour uses daylight-saving rules. Rates count observed zero values among "
        "valid nonnegative samples, not hours or continuous availability. Operating "
        "flags and source-report age are not filtered. Metadata matches the same "
        "capture; area filtering happens per observation. downtown_demo is only "
        "an illustrative box: latitude 41.85..41.91, longitude -87.66..-87.60, "
        "not an official boundary. Later uploads visible now are included. "
        "Null/invalid samples are unknown. query_count excludes dry runs."
    )


@dataclass(frozen=True)
class _Window:
    start: date
    end: date
    days: list[date]
    eligible_days: int


def _window(
    metric: RankMetric,
    hour: int,
    limit: int,
    start_date: date | None,
    end_date: date | None,
    day_type: DayType,
    area: Area,
    now: datetime,
) -> _Window:
    if metric not in get_args(RankMetric):
        raise ToolError("Use one of the four documented ranking metrics.")
    if isinstance(hour, bool) or not isinstance(hour, int) or not 0 <= hour <= 23:
        raise ToolError("hour must be an integer from 0 to 23 in America/Chicago.")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
        raise ToolError("limit must be an integer from 1 to 20.")
    if day_type not in get_args(DayType) or area not in get_args(Area):
        raise ToolError("Use a documented day_type and area.")
    if now.utcoffset() is None:
        raise ToolError("The ranking clock must include a timezone.")
    if (start_date is None) != (end_date is None):
        raise ToolError("Provide start_date and end_date together, or omit both.")
    if any(
        value is not None and type(value) is not date
        for value in (start_date, end_date)
    ):
        raise ToolError("start_date and end_date must be calendar dates.")
    today = now.astimezone(CHICAGO).date()
    end = end_date if end_date is not None else today
    start = start_date if start_date is not None else end - timedelta(days=3)
    if not today - timedelta(days=7) <= start < end <= today:
        raise ToolError("Use up to seven completed Chicago dates from the last week.")
    local_days = [start + timedelta(days=n) for n in range((end - start).days)]
    eligible_days = sum(
        day_type == "all" or (day.weekday() < 5) == (day_type == "weekdays")
        for day in local_days
    )
    first = datetime.combine(start, time.min, CHICAGO).astimezone(UTC).date()
    span = (now.astimezone(UTC).date() - first).days
    if not 0 <= span <= 8:
        raise ToolError("The ranking exceeds its nine-ingestion-date bound.")
    return _Window(
        start, end, [first + timedelta(days=n) for n in range(span + 1)], eligible_days
    )


def _config(
    window: _Window,
    hour: int,
    day_type: DayType,
    area: Area,
    day: date,
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
            bigquery.ScalarQueryParameter("ingestion_date", "DATE", day),
            bigquery.ScalarQueryParameter(
                "start",
                "TIMESTAMP",
                datetime.combine(window.start, time.min, CHICAGO).astimezone(UTC),
            ),
            bigquery.ScalarQueryParameter(
                "end",
                "TIMESTAMP",
                datetime.combine(window.end, time.min, CHICAGO).astimezone(UTC),
            ),
            bigquery.ScalarQueryParameter("hour", "INT64", hour),
            bigquery.ScalarQueryParameter("day_type", "STRING", day_type),
            bigquery.ScalarQueryParameter("area", "STRING", area),
        ],
    )


@contextmanager
def _errors() -> Iterator[None]:
    try:
        yield
    except DefaultCredentialsError as exc:
        raise ToolError("ADC is unavailable. Refresh your Google Cloud login.") from exc
    except Forbidden as exc:
        raise ToolError(
            "BigQuery access was denied. Check reader permissions."
        ) from exc
    except BadRequest as exc:
        raise ToolError(
            "BigQuery rejected the ranking; its scan cap will not rise."
        ) from exc
    except (GoogleAPICallError, TimeoutError) as exc:
        raise ToolError("BigQuery did not complete. No ranking is available.") from exc


def _estimate(
    client: bigquery.Client,
    window: _Window,
    hour: int,
    day_type: DayType,
    area: Area,
) -> int:
    estimated = 0
    for day in window.days if window.eligible_days else []:
        job = client.query(
            RANKING_SQL,
            job_config=_config(window, hour, day_type, area, day, dry_run=True),
            location=LOCATION,
            timeout=30,
            retry=NO_RETRY,
            job_retry=None,
        )
        size = job.total_bytes_processed
        if size is None or not 0 <= size <= MAXIMUM_BYTES_BILLED:
            raise ToolError("A ranking estimate is missing or exceeds the 256 MiB cap.")
        estimated += size
        if estimated > MAXIMUM_REQUEST_BYTES:
            raise ToolError("Ranking estimates exceed the 1 GiB request cap.")
    return estimated


def estimate_ranking(
    client: bigquery.Client,
    metric: RankMetric,
    hour: int,
    limit: int = 5,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    day_type: DayType = "all",
    area: Area = "all",
    now: datetime | None = None,
) -> tuple[int, int]:
    window = _window(
        metric,
        hour,
        limit,
        start_date,
        end_date,
        day_type,
        area,
        now or datetime.now(UTC),
    )
    with _errors():
        return (
            _estimate(client, window, hour, day_type, area),
            len(window.days) if window.eligible_days else 0,
        )


class _Metadata(BaseModel):
    received_at: AwareDatetime
    capture_id: str
    station_name: str | None
    latitude: float | None
    longitude: float | None


@dataclass
class _Totals:
    observations: int = 0
    valid: int = 0
    total: int = 0
    zeros: int = 0
    days: set[date] = field(default_factory=set)
    latest: _Metadata | None = None


def read_rankings(
    metric: RankMetric,
    hour: int,
    limit: int = 5,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    day_type: DayType = "all",
    area: Area = "all",
    client: bigquery.Client | None = None,
    now: datetime | None = None,
) -> RankingReport:
    window = _window(
        metric,
        hour,
        limit,
        start_date,
        end_date,
        day_type,
        area,
        now or datetime.now(UTC),
    )
    totals: dict[str, _Totals] = {}
    billed = processed = 0
    days = window.days if window.eligible_days else []
    kind = "bike" if "bikes" in metric else "dock"
    with _errors():
        if days:
            client = client if client is not None else bigquery.Client(project=PROJECT)
            _estimate(client, window, hour, day_type, area)
        for day in days:
            assert client is not None
            remaining = MAXIMUM_REQUEST_BYTES - billed
            if remaining <= 0:
                raise ToolError("The 1 GiB ranking request budget is exhausted.")
            job = client.query(
                RANKING_SQL,
                job_config=_config(
                    window,
                    hour,
                    day_type,
                    area,
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
                raise ToolError("Ranking query accounting is missing; stopping.")
            billed += job.total_bytes_billed
            processed += job.total_bytes_processed
            if billed > MAXIMUM_REQUEST_BYTES or len(rows) > MAX_PARTITION_ROWS:
                raise ToolError("Ranking exceeded its request or candidate bound.")
            for row in rows:
                station_id = str(row["station_id"])
                item = totals.setdefault(station_id, _Totals())
                if len(totals) > MAX_CANDIDATES:
                    raise ToolError("Ranking exceeded 10,000 station candidates.")
                valid = int(row[f"{kind}_sample_count"])
                item.observations += int(row["observation_count"])
                item.valid += valid
                item.total += int(row[f"{kind}_sum"] or 0)
                item.zeros += int(row[f"zero_{kind}_count"])
                if valid:
                    item.days.add(row["sample_day"])
                metadata = _Metadata.model_validate(dict(row["latest_record"]))
                if item.latest is None or (
                    metadata.received_at,
                    metadata.capture_id,
                ) > (item.latest.received_at, item.latest.capture_id):
                    item.latest = metadata
    candidates = [
        (station_id, item) for station_id, item in totals.items() if item.valid
    ]
    rates = metric.startswith("empty_")
    candidates.sort(
        key=lambda pair: (
            -pair[1].zeros / pair[1].valid if rates else pair[1].total / pair[1].valid,
            -pair[1].valid,
            pair[0],
        )
    )
    stations: list[RankedStation] = []
    for rank, (station_id, item) in enumerate(candidates[:limit], 1):
        assert item.latest is not None
        average, rate = item.total / item.valid, item.zeros / item.valid
        stations.append(
            RankedStation(
                rank=rank,
                station_id=station_id,
                station_name=item.latest.station_name,
                latitude=item.latest.latitude,
                longitude=item.latest.longitude,
                last_observed_at=item.latest.received_at,
                metric_value=rate if rates else average,
                observation_count=item.observations,
                valid_sample_count=item.valid,
                invalid_sample_count=item.observations - item.valid,
                zero_sample_count=item.zeros,
                zero_sample_rate=rate,
                avg_available=average,
                sampled_days=len(item.days),
            )
        )
    return RankingReport(
        metric=metric,
        hour=hour,
        start_date=window.start,
        end_date=window.end,
        day_type=day_type,
        area=area,
        eligible_days=window.eligible_days,
        status="ok" if stations else "no_data",
        stations=stations,
        total_eligible_stations=len(candidates),
        truncated=len(candidates) > len(stations),
        query_count=len(days),
        bytes_processed=processed,
        bytes_billed=billed,
    )
