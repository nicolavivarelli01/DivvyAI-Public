from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
from google.api_core.exceptions import Forbidden
from google.cloud.bigquery import Client
from mcp.server.mcpserver.exceptions import ToolError

from gaps import read_capture_gaps
from history import MAXIMUM_BYTES_BILLED

END = datetime(2026, 10, 24, 12, tzinfo=UTC)


def fake_client(
    *, information: int = 10, status: int = 10, matches: bool = False
) -> Mock:
    items = (
        [
            {
                "feed_name": feed,
                "capture_id": "same-capture",
                "previous_received_at": END - timedelta(minutes=30),
                "capture_received_at": END,
                "gap_minutes": 30.0,
            }
            for feed in ("station_information", "station_status")
        ]
        if matches
        else []
    )
    client = Mock(spec=Client)
    job = client.query.return_value
    job.total_bytes_processed = 100
    job.total_bytes_billed = 100
    job.result.return_value = [
        {
            "window_start_utc": END - timedelta(days=1),
            "window_end_utc": END,
            "information_intervals_checked": information,
            "status_intervals_checked": status,
            "matching_intervals": len(items),
            "gaps": items,
        }
    ]
    return client


def test_feed_lineage_parameters_and_scan_cap_are_preserved() -> None:
    client = fake_client(matches=True)
    result = read_capture_gaps(25.0, client=client)
    assert result.status == "gaps_found"
    assert [item.feed_name for item in result.gaps] == [
        "station_information",
        "station_status",
    ]
    assert {item.capture_id for item in result.gaps} == {"same-capture"}
    assert result.gaps[0].previous_received_at == END - timedelta(minutes=30)
    assert result.matching_intervals == 2 and not result.truncated
    config = client.query.call_args.kwargs["job_config"]
    assert config.maximum_bytes_billed == MAXIMUM_BYTES_BILLED
    assert config.query_parameters[0].name == "min_minutes"
    assert config.query_parameters[0].value == 25.0


def test_result_limit_reports_truncation_without_losing_matching_count() -> None:
    result = read_capture_gaps(25.0, limit=1, client=fake_client(matches=True))
    assert len(result.gaps) == 1
    assert result.matching_intervals == 2 and result.truncated


@pytest.mark.parametrize(
    "information,expected", [(10, "no_gaps"), (0, "insufficient_data")]
)
def test_no_matches_distinguishes_missing_feed_evidence(
    information: int, expected: str
) -> None:
    result = read_capture_gaps(client=fake_client(information=information))
    assert result.status == expected
    assert result.matching_intervals == 0 and result.gaps == []


@pytest.mark.parametrize(
    "minutes,limit",
    [
        (-1.0, 20),
        (float("nan"), 20),
        (30.0, 0),
        (30.0, 51),
        (True, 20),
        (30.0, True),
    ],
)
def test_invalid_parameters_do_not_query(minutes: float, limit: int) -> None:
    client = fake_client()
    with pytest.raises(ToolError):
        read_capture_gaps(minutes, limit, client=client)
    client.query.assert_not_called()


def test_permission_error_is_not_reported_as_no_gaps() -> None:
    client = fake_client()
    client.query.side_effect = Forbidden("denied")
    with pytest.raises(ToolError, match="access was denied"):
        read_capture_gaps(client=client)
    assert client.query.call_count == 1
