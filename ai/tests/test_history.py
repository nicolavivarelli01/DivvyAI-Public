from datetime import UTC, date, datetime, timedelta
from unittest.mock import Mock

import pytest
from google.api_core.exceptions import Forbidden
from google.cloud.bigquery import Client
from mcp.server.mcpserver.exceptions import ToolError

from history import MAXIMUM_BYTES_BILLED, MAXIMUM_REQUEST_BYTES, estimate_history
from server import read_availability, validate_window

TODAY = date(2026, 10, 24)
START = datetime(2026, 10, 23, 8, tzinfo=UTC)
END = START + timedelta(hours=1)


def job(
    *,
    size: int = 100,
    billed: int | None = 100,
    seen: bool = True,
    hours: list[dict[str, object]] | None = None,
) -> Mock:
    result = Mock(total_bytes_processed=size, total_bytes_billed=billed)
    result.result.return_value = [{"station_seen": seen, "hours": hours or []}]
    return result


def test_late_loads_merge_with_sample_weights_and_bound_queries() -> None:
    client = Mock(spec=Client)
    first = {
        "hour_started_at_utc": START,
        "snapshot_count": 1,
        "bike_sample_count": 1,
        "dock_sample_count": 0,
        "bike_sum": 1,
        "dock_sum": None,
    }
    late = {
        "hour_started_at_utc": START,
        "snapshot_count": 3,
        "bike_sample_count": 3,
        "dock_sample_count": 3,
        "bike_sum": 27,
        "dock_sum": 12,
    }
    client.query.side_effect = [job(), job(), job(hours=[first]), job(hours=[late])]
    result = read_availability("s1", START, END, client=client, today=TODAY)
    hour = result.hours[0]
    assert result.source == "raw_history" and result.query_count == 2
    assert result.bytes_billed == 200
    assert hour.snapshot_count == 4 and hour.bike_sample_count == 4
    assert hour.avg_bikes_available == 7.0
    assert hour.dock_sample_count == 3 and hour.avg_docks_available == 4.0
    configs = [call.kwargs["job_config"] for call in client.query.call_args_list]
    assert [config.dry_run for config in configs] == [True, True, False, False]
    assert all(
        config.maximum_bytes_billed <= MAXIMUM_BYTES_BILLED for config in configs
    )
    assert [config.query_parameters[-1].value for config in configs[2:]] == [
        date(2026, 10, 23),
        TODAY,
    ]


def test_missing_historical_values_and_hours_remain_unknown() -> None:
    client = Mock(spec=Client)
    hour = {
        "hour_started_at_utc": START,
        "snapshot_count": 1,
        "bike_sample_count": 0,
        "dock_sample_count": 0,
        "bike_sum": None,
        "dock_sum": None,
    }
    client.query.side_effect = [job(), job(), job(hours=[hour]), job(seen=False)]
    result = read_availability(
        "s1", START, END + timedelta(hours=1), client=client, today=TODAY
    )
    assert len(result.hours) == 1
    assert result.hours[0].avg_bikes_available is None
    assert result.hours[0].avg_docks_available is None


def test_known_station_without_requested_history_returns_no_data() -> None:
    client = Mock(spec=Client)
    client.query.side_effect = [job(), job(), job(), job()]
    result = read_availability("s1", START, END, client=client, today=TODAY)
    assert result.status == "no_data" and result.hours == []


def test_unobserved_station_error_is_scoped_to_searched_dates() -> None:
    client = Mock(spec=Client)
    client.query.side_effect = [job(), job(), job(seen=False), job(seen=False)]
    with pytest.raises(ToolError, match="scanned ingestion dates"):
        read_availability("missing", START, END, client=client, today=TODAY)


@pytest.mark.parametrize("total_budget", [False, True])
def test_preflight_rejects_oversized_queries_before_execution(
    total_budget: bool,
) -> None:
    client = Mock(spec=Client)
    too_large = MAXIMUM_BYTES_BILLED + 1
    client.query.return_value = job(
        size=MAXIMUM_BYTES_BILLED if total_budget else too_large
    )
    start = START - timedelta(days=6) if total_budget else START
    with pytest.raises(ToolError, match="cap"):
        estimate_history(client, "s1", start, start + timedelta(hours=1), today=TODAY)
    assert all(
        call.kwargs["job_config"].dry_run for call in client.query.call_args_list
    )
    client.query.return_value.result.assert_not_called()


def test_later_failure_does_not_return_partial_history() -> None:
    client = Mock(spec=Client)
    client.query.side_effect = [job(), job(), job(), Forbidden("denied")]
    with pytest.raises(ToolError, match="access was denied"):
        read_availability("s1", START, END, client=client, today=TODAY)


def test_remaining_request_budget_caps_the_next_query() -> None:
    client = Mock(spec=Client)
    start = START - timedelta(days=3)
    client.query.side_effect = [
        *[job() for _ in range(5)],
        *[job(billed=MAXIMUM_BYTES_BILLED) for _ in range(3)],
        job(billed=MAXIMUM_BYTES_BILLED - 50),
        job(billed=20),
    ]
    result = read_availability(
        "s1", start, start + timedelta(hours=1), client=client, today=TODAY
    )
    assert client.query.call_args.kwargs["job_config"].maximum_bytes_billed == 50
    assert result.bytes_billed == MAXIMUM_REQUEST_BYTES - 30


def test_historical_offsets_normalize_to_utc_and_long_windows_reject() -> None:
    assert validate_window(
        "s1", datetime.fromisoformat("2026-10-23T03:00:00-05:00"), END, today=TODAY
    ) == (START, END)
    with pytest.raises(ToolError, match="at most seven days"):
        validate_window("s1", START - timedelta(days=7), END, today=TODAY)
