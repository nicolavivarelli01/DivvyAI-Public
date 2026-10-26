from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field


class Hour(BaseModel):
    hour_started_at_utc: AwareDatetime
    snapshot_count: int
    bike_sample_count: int
    dock_sample_count: int
    avg_bikes_available: float | None
    avg_docks_available: float | None


class Availability(BaseModel):
    station_id: str
    start: AwareDatetime
    end: AwareDatetime
    status: Literal["ok", "no_data"]
    hours: list[Hour] = Field(max_length=168)
    bytes_processed: int
    bytes_billed: int | None = None
    source: Literal["hourly_mart", "raw_history"] = "hourly_mart"
    query_count: int = 1
    note: str = (
        "UTC hours; start inclusive, end exclusive. Averages describe observed "
        "samples. Missing hours or null averages are unknown. Hours can be partial."
    )
