"""Define 20 evaluation questions without calling a model or BigQuery."""

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

ToolName = Literal["get_station_availability", "rank_stations", "find_capture_gaps"]
Arguments = dict[str, str | int | float]
CHICAGO = ZoneInfo("America/Chicago")
VARIED_STATION = "a3ad8072-a135-11e9-9cda-0a87ae2ba916"
SECOND_STATION = "03a1610c-3ba0-4b85-b512-bc26791cb7ee"

ANSWER_FIELDS: dict[ToolName, str] = {
    "get_station_availability": (
        "Return status and hours. Each hourly row must contain "
        "hour_started_at_utc, snapshot_count, bike_sample_count, "
        "dock_sample_count, avg_bikes_available and avg_docks_available."
    ),
    "rank_stations": (
        "Return status and stations in ranking order. Each station must contain "
        "station_id, metric_value, zero_sample_count, valid_sample_count "
        "and sampled_days. Keep rate values as fractions from 0 to 1."
    ),
    "find_capture_gaps": (
        "Return status, matching_intervals and gaps in the returned order. "
        "Each gap must contain feed_name, capture_id, previous_received_at, "
        "capture_received_at and gap_minutes."
    ),
}


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    question: str
    expected_tool: ToolName
    expected_arguments: Arguments

    @property
    def prompt(self) -> str:
        return (
            f"{self.question}\n\nFor this evaluation, answer only with a compact "
            f"JSON object, without Markdown or surrounding prose. "
            f"{ANSWER_FIELDS[self.expected_tool]} "
            "Use actual returned values; retain null for unknown numbers. "
            "Use ISO 8601 UTC timestamps. Do not round numeric values."
        )


def utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def latest_completed_weekend(today: date) -> date:
    for offset in range(1, 8):
        candidate = today - timedelta(days=offset)
        if candidate.weekday() >= 5:
            return candidate
    raise ValueError("No matching completed date in the seven-day window.")


def build_cases(as_of: datetime | None = None) -> list[EvaluationCase]:
    as_of = as_of if as_of is not None else datetime.now(UTC)
    if as_of.utcoffset() is None:
        raise ValueError("as_of must include a timezone.")
    utc_day = as_of.astimezone(UTC).date()
    local_day = as_of.astimezone(CHICAGO).date()
    day = utc_day - timedelta(days=2)
    midnight = datetime.combine(day, time.min, UTC)
    local_eight = datetime.combine(day, time(8), CHICAGO)
    cases: list[EvaluationCase] = []

    availability_windows = [
        (VARIED_STATION, midnight, midnight + timedelta(hours=1)),
        (
            VARIED_STATION,
            midnight + timedelta(hours=16),
            midnight + timedelta(hours=17),
        ),
        (VARIED_STATION, midnight + timedelta(hours=8), midnight + timedelta(hours=10)),
        (SECOND_STATION, midnight, midnight + timedelta(hours=1)),
        (VARIED_STATION, local_eight, local_eight + timedelta(hours=1)),
        (VARIED_STATION, midnight - timedelta(hours=1), midnight + timedelta(hours=1)),
    ]
    for index, (station, start, end) in enumerate(availability_windows, 1):
        question = (
            f"For station {station}, show hourly average bikes, average docks "
            f"and sample counts from {start.isoformat()} inclusive to "
            f"{end.isoformat()} exclusive. Keep separate hourly rows."
        )
        cases.append(
            EvaluationCase(
                f"availability_{index:02}",
                question,
                "get_station_availability",
                {"station_id": station, "start": utc_text(start), "end": utc_text(end)},
            )
        )

    weekend = latest_completed_weekend(local_day)
    weekend_start = weekend - timedelta(days=1) if weekend.weekday() == 6 else weekend
    metrics = [
        ("empty_bikes_rate", "highest fraction of zero-bike samples"),
        ("empty_docks_rate", "highest fraction of zero-dock samples"),
        ("lowest_avg_bikes", "lowest average bike count"),
        ("lowest_avg_docks", "lowest average dock count"),
    ]
    windows = [
        (local_day - timedelta(days=3), local_day, "weekdays", "all", 8, 3),
        (
            weekend_start,
            weekend + timedelta(days=1),
            "weekends",
            "downtown_demo",
            17,
            2,
        ),
    ]
    ranking_number = 1
    for start_day, end_day, day_type, area, hour, limit in windows:
        for metric, description in metrics:
            cases.append(
                EvaluationCase(
                    f"ranking_{ranking_number:02}",
                    f"List the top {limit} stations with the {description} during "
                    f"hour {hour:02}:00-{hour:02}:59 America/Chicago, using local "
                    f"dates {start_day} inclusive to {end_day} exclusive. "
                    f"Use day filter '{day_type}' and area '{area}'. "
                    "Show the selected metric and its sample/day counts.",
                    "rank_stations",
                    {
                        "metric": metric,
                        "hour": hour,
                        "limit": limit,
                        "start_date": start_day.isoformat(),
                        "end_date": end_day.isoformat(),
                        "day_type": day_type,
                        "area": area,
                    },
                )
            )

            ranking_number += 1

    for index, (duration, minutes, limit) in enumerate(
        [
            ("15 minutes", 15.0, 2),
            ("half an hour", 30.0, 2),
            ("45 minutes", 45.0, 3),
            ("one hour", 60.0, 2),
            ("two hours", 120.0, 2),
            ("24 hours", 1440.0, 2),
        ],
        1,
    ):
        cases.append(
            EvaluationCase(
                f"gaps_{index:02}",
                f"Find completed receipt intervals lasting at least {duration}, "
                "from yesterday's UTC midnight through the tool's query time. "
                f"Return at most {limit} intervals, largest first, separately "
                "for each feed, and the total number matching before the limit.",
                "find_capture_gaps",
                {"min_minutes": minutes, "limit": limit},
            )
        )
    return cases


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    now = datetime.now(UTC)
    cases = build_cases(now)
    for case in cases:
        print(f"{case.case_id}: {case.question}")
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        document = {
            "schema_version": 1,
            "created_at_utc": utc_text(now),
            "benchmark_kind": "structured_factual_answers",
            "cases": [{**asdict(case), "prompt": case.prompt} for case in cases],
        }
        with args.output.open("x", encoding="utf-8") as destination:
            destination.write(json.dumps(document, indent=2, allow_nan=False) + "\n")
        print(f"Saved {len(cases)} questions to {args.output}")
