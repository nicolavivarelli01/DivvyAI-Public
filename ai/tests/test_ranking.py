from datetime import UTC, date, datetime
from typing import cast
from unittest.mock import Mock

import pytest
from google.api_core.exceptions import Forbidden
from google.cloud.bigquery import Client
from mcp.server.mcpserver.exceptions import ToolError

from history import MAXIMUM_BYTES_BILLED
from ranking import RankMetric, estimate_ranking, read_rankings

NOW = datetime(2026, 10, 25, 1, 30, tzinfo=UTC)  # Still October 24 in Chicago.
START = date(2026, 10, 22)
END = date(2026, 10, 24)


def job(rows: list[dict[str, object]] | None = None, *, size: int = 100) -> Mock:
    result = Mock(total_bytes_processed=size, total_bytes_billed=size)
    result.result.return_value = rows or []
    return result


def sample(
    station: str,
    day: date,
    count: int,
    zeros: int,
    total: int,
) -> dict[str, object]:
    return {
        "station_id": station,
        "sample_day": day,
        "observation_count": count,
        "bike_sample_count": count,
        "dock_sample_count": 0,
        "bike_sum": total,
        "dock_sum": None,
        "zero_bike_count": zeros,
        "zero_dock_count": 0,
        "latest_record": {
            "received_at": datetime(day.year, day.month, day.day, 13, tzinfo=UTC),
            "capture_id": f"capture-{day}",
            "station_name": f"{station}-{day}",
            "latitude": 41.88,
            "longitude": -87.63,
        },
    }


def weighted_client() -> Mock:
    client = Mock(spec=Client)
    client.query.side_effect = [
        *[job() for _ in range(4)],
        job([sample("a", START, 1, 1, 0), sample("b", START, 10, 2, 80)]),
        job(),
        # An old observation uploaded later must merge into the same station.
        job([sample("a", START, 9, 0, 90)]),
        job([sample("a", date(2026, 10, 23), 1, 0, 5)]),
    ]
    return client


def test_rates_merge_all_samples_and_late_uploads_before_ranking() -> None:
    client = weighted_client()
    report = read_rankings(
        "empty_bikes_rate",
        8,
        1,
        start_date=START,
        end_date=END,
        client=client,
        now=NOW,
    )
    assert report.stations[0].station_id == "b"
    assert report.stations[0].metric_value == 0.2
    assert report.stations[0].valid_sample_count == 10
    assert report.bytes_billed == 400 and report.query_count == 4
    configs = [call.kwargs["job_config"] for call in client.query.call_args_list]
    assert [config.dry_run for config in configs] == [True] * 4 + [False] * 4
    assert all(
        config.maximum_bytes_billed <= MAXIMUM_BYTES_BILLED for config in configs
    )


def test_means_use_sample_weights_and_latest_observed_metadata() -> None:
    report = read_rankings(
        "lowest_avg_bikes",
        8,
        start_date=START,
        end_date=END,
        client=weighted_client(),
        now=NOW,
    )
    assert [station.station_id for station in report.stations] == ["b", "a"]
    station = report.stations[1]
    assert station.metric_value == pytest.approx(95 / 11)
    assert station.sampled_days == 2
    assert station.station_name == "a-2026-10-23"


def test_missing_metric_samples_are_excluded_instead_of_treated_as_zero() -> None:
    report = read_rankings(
        "empty_docks_rate",
        8,
        start_date=START,
        end_date=END,
        client=weighted_client(),
        now=NOW,
    )
    assert report.status == "no_data" and report.stations == []


def test_chicago_dates_include_dst_change_and_later_ingestion_dates() -> None:
    client = Mock(spec=Client)
    client.query.return_value = job()
    _, count = estimate_ranking(
        client,
        "empty_bikes_rate",
        1,
        start_date=date(2026, 10, 31),
        end_date=date(2026, 11, 2),
        day_type="weekends",
        area="downtown_demo",
        now=datetime(2026, 11, 3, 1, tzinfo=UTC),
    )
    assert count == 4
    config = client.query.call_args_list[0].kwargs["job_config"]
    params = {item.name: item.value for item in config.query_parameters}
    assert params["start"] == datetime(2026, 10, 31, 5, tzinfo=UTC)
    assert params["end"] == datetime(2026, 11, 2, 6, tzinfo=UTC)
    assert params["hour"] == 1
    assert params["day_type"] == "weekends"
    assert params["area"] == "downtown_demo"


@pytest.mark.parametrize(
    "metric,hour,limit",
    [
        (cast(RankMetric, "arbitrary_sql"), 8, 5),
        ("empty_bikes_rate", True, 5),
        ("empty_bikes_rate", 24, 5),
        ("empty_bikes_rate", 8, 21),
    ],
)
def test_invalid_inputs_fail_before_any_query(
    metric: RankMetric,
    hour: int,
    limit: int,
) -> None:
    client = Mock(spec=Client)
    with pytest.raises(ToolError):
        read_rankings(metric, hour, limit, client=client, now=NOW)
    client.query.assert_not_called()


def test_incomplete_current_local_day_is_rejected() -> None:
    client = Mock(spec=Client)
    with pytest.raises(ToolError):
        read_rankings(
            "empty_bikes_rate",
            8,
            start_date=START,
            end_date=date(2026, 10, 25),
            client=client,
            now=NOW,
        )
    client.query.assert_not_called()


def test_partial_date_pair_is_rejected_before_any_query() -> None:
    client = Mock(spec=Client)
    with pytest.raises(ToolError, match="together"):
        read_rankings("empty_bikes_rate", 8, start_date=START, client=client, now=NOW)
    client.query.assert_not_called()


@pytest.mark.parametrize("request_budget", [False, True])
def test_preflight_stops_before_execution_when_a_cap_is_exceeded(
    request_budget: bool,
) -> None:
    client = Mock(spec=Client)
    client.query.return_value = job(
        size=MAXIMUM_BYTES_BILLED if request_budget else MAXIMUM_BYTES_BILLED + 1,
    )
    with pytest.raises(ToolError, match="cap"):
        estimate_ranking(
            client,
            "empty_bikes_rate",
            8,
            start_date=date(2026, 10, 17),
            end_date=END,
            now=NOW,
        )
    assert all(
        call.kwargs["job_config"].dry_run for call in client.query.call_args_list
    )
    client.query.return_value.result.assert_not_called()


def test_later_permission_failure_returns_no_partial_ranking() -> None:
    client = Mock(spec=Client)
    client.query.side_effect = [
        *[job() for _ in range(4)],
        job([sample("a", START, 4, 4, 0)]),
        Forbidden("denied"),
    ]
    with pytest.raises(ToolError, match="access was denied"):
        read_rankings(
            "empty_bikes_rate",
            8,
            start_date=START,
            end_date=END,
            client=client,
            now=NOW,
        )
