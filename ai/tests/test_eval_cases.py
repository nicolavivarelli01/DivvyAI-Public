from collections import Counter
from datetime import UTC, date, datetime

import pytest

from eval_cases import build_cases


def test_twenty_unique_questions_cover_all_three_tools() -> None:
    cases = build_cases(datetime(2026, 10, 25, 12, tzinfo=UTC))
    assert len(cases) == len({case.case_id for case in cases}) == 20
    assert len({case.question for case in cases}) == 20
    assert Counter(case.expected_tool for case in cases) == {
        "get_station_availability": 6,
        "rank_stations": 8,
        "find_capture_gaps": 6,
    }
    for case in cases:
        assert "only with a compact JSON object" in case.prompt


@pytest.mark.parametrize("month, expected_hour", [(7, 13), (11, 14)])
def test_local_hour_respects_chicago_daylight_saving(
    month: int, expected_hour: int
) -> None:
    case = build_cases(datetime(2026, month, 10, 12, tzinfo=UTC))[4]
    start = datetime.fromisoformat(str(case.expected_arguments["start"]))
    assert start.hour == expected_hour
    offset = start.utcoffset()
    assert offset is not None and offset.total_seconds() == 0


def test_dates_remain_completed_in_chicago_near_utc_midnight() -> None:
    cases = build_cases(datetime(2026, 10, 25, 0, 5, tzinfo=UTC))
    chicago_today = date(2026, 10, 24)
    for case in cases:
        if case.expected_tool != "rank_stations":
            continue
        start = date.fromisoformat(str(case.expected_arguments["start_date"]))
        end = date.fromisoformat(str(case.expected_arguments["end_date"]))
        assert chicago_today.toordinal() - 7 <= start.toordinal()
        assert start < end <= chicago_today
        if case.expected_arguments["day_type"] == "weekends":
            assert start.weekday() >= 5


def test_naive_evaluation_clock_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone"):
        build_cases(datetime(2026, 10, 25, 12))
