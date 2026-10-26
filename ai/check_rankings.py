"""Preview ranking scans or call the ranking tool through MCP."""

import argparse
import asyncio
import json
import sys
from datetime import date
from pathlib import Path
from typing import cast, get_args

from google.cloud import bigquery
from mcp import Client, StdioServerParameters
from mcp.server.mcpserver.exceptions import ToolError

from history import MAXIMUM_BYTES_BILLED, MAXIMUM_REQUEST_BYTES, PROJECT
from ranking import Area, DayType, RankMetric, estimate_ranking


async def check(
    action: str,
    metric: RankMetric,
    hour: int,
    limit: int,
    *,
    start_date: date | None,
    end_date: date | None,
    day_type: DayType,
    area: Area,
) -> None:
    if action == "dry-run":
        estimated, count = estimate_ranking(
            bigquery.Client(project=PROJECT),
            metric,
            hour,
            limit,
            start_date=start_date,
            end_date=end_date,
            day_type=day_type,
            area=area,
        )
        print(f"Ranking: {count} partition queries; estimated total: {estimated} bytes")
        print(
            f"Per-query cap: {MAXIMUM_BYTES_BILLED}; request cap: {MAXIMUM_REQUEST_BYTES}"
        )
        return

    arguments: dict[str, object] = {
        "metric": metric,
        "hour": hour,
        "limit": limit,
        "day_type": day_type,
        "area": area,
    }
    if start_date is not None:
        arguments["start_date"] = start_date.isoformat()
    if end_date is not None:
        arguments["end_date"] = end_date.isoformat()
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[str(Path(__file__).with_name("server.py"))],
    )
    error: str | None = None
    async with Client(parameters) as client:
        result = await client.call_tool("rank_stations", arguments)
        if result.is_error:
            error = str(result.content)
        else:
            print(json.dumps(result.structured_content, indent=2))
    if error is not None:
        raise ToolError(error)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["dry-run", "call"])
    parser.add_argument(
        "--metric", choices=get_args(RankMetric), default="empty_bikes_rate"
    )
    parser.add_argument(
        "--hour", type=int, default=8, help="Chicago local hour, 0 to 23."
    )
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--start-date", type=date.fromisoformat)
    parser.add_argument("--end-date", type=date.fromisoformat)
    parser.add_argument("--day-type", choices=get_args(DayType), default="all")
    parser.add_argument("--area", choices=get_args(Area), default="all")
    args = parser.parse_args()
    try:
        asyncio.run(
            check(
                args.action,
                cast(RankMetric, args.metric),
                args.hour,
                args.limit,
                start_date=args.start_date,
                end_date=args.end_date,
                day_type=cast(DayType, args.day_type),
                area=cast(Area, args.area),
            )
        )
    except ToolError as exc:
        raise SystemExit(str(exc)) from None
