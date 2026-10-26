"""These tests use a fake BigQuery client.
They check parameters, the scan cap, missing values,
invalid dates, and permission errors."""

from datetime import UTC, date, datetime
from unittest.mock import Mock

import pytest
from google.api_core.exceptions import Forbidden
from google.cloud.bigquery import Client
from mcp.server.mcpserver.exceptions import ToolError

from server import MAXIMUM_BYTES_BILLED, NO_RETRY, read_availability

TODAY = date(2026, 10, 24)
START = datetime(2026, 10, 24, 8, tzinfo=UTC)
END = datetime(2026, 10, 24, 10, tzinfo=UTC)


def fake_client(
    *, seen: bool = True, hours: list[dict[str, object]] | None = None
) -> Mock:
    client = Mock(spec=Client)
    client.query.return_value.total_bytes_processed = 100
    client.query.return_value.total_bytes_billed = 100
    client.query.return_value.result.return_value = [
        {"data_date_utc": TODAY, "station_seen_today": seen, "hours": hours or []}
    ]
    return client


def test_parameters_cap_and_unknown_values_are_preserved() -> None:
    station_id = "station' OR TRUE --"
    client = fake_client(
        hours=[
            {
                "hour_started_at_utc": START,
                "snapshot_count": 2,
                "bike_sample_count": 0,
                "dock_sample_count": 2,
                "avg_bikes_available": None,
                "avg_docks_available": 10.5,
            }
        ]
    )
    result = read_availability(station_id, START, END, client=client, today=TODAY)
    assert result.status == "ok"
    assert result.hours[0].avg_bikes_available is None
    assert result.hours[0].avg_docks_available == 10.5
    args, kwargs = client.query.call_args
    assert station_id not in args[0]
    config = kwargs["job_config"]
    assert config.maximum_bytes_billed == MAXIMUM_BYTES_BILLED
    assert [parameter.name for parameter in config.query_parameters] == [
        "station_id",
        "start",
        "end",
    ]
    assert config.query_parameters[0].value == station_id
    assert kwargs["retry"] is NO_RETRY and kwargs["job_retry"] is None


def test_known_station_without_samples_in_window_returns_no_data() -> None:
    result = read_availability("s1", START, END, client=fake_client(), today=TODAY)
    assert result.status == "no_data" and result.hours == []


def test_station_not_observed_today_has_helpful_error() -> None:
    with pytest.raises(ToolError, match="not found in today's observations"):
        read_availability(
            "missing", START, END, client=fake_client(seen=False), today=TODAY
        )


@pytest.mark.parametrize(
    "start,end",
    [
        (START.replace(tzinfo=None), END),
        (END, START),
        (START.replace(minute=30), END),
        (START.replace(day=16), END),
    ],
)
def test_invalid_window_does_not_query(start: datetime, end: datetime) -> None:
    client = fake_client()
    with pytest.raises(ToolError):
        read_availability("s1", start, end, client=client, today=TODAY)
    client.query.assert_not_called()


def test_permission_error_is_not_reported_as_missing_data() -> None:
    client = fake_client()
    client.query.side_effect = Forbidden("denied")
    with pytest.raises(ToolError, match="access was denied"):
        read_availability("s1", START, END, client=client, today=TODAY)
    assert client.query.call_count == 1
