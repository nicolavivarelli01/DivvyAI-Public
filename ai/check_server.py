import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from google.cloud import bigquery
from mcp import Client, StdioServerParameters
from mcp.server.mcpserver.exceptions import ToolError

from gaps import GAPS_SQL, gaps_config, validate_gap_parameters
from history import MAXIMUM_REQUEST_BYTES, estimate_history, history_dates
from server import (
    LOCATION,
    MAXIMUM_BYTES_BILLED,
    NO_RETRY,
    PROJECT,
    SQL,
    query_config,
    validate_window,
)


async def check(
    action: str,
    station_id: str,
    start: datetime,
    end: datetime,
    *,
    tool: str = "get_station_availability",
    min_minutes: float = 30.0,
    limit: int = 20,
) -> None:
    today = datetime.now(UTC).date()
    if tool == "find_capture_gaps":
        validate_gap_parameters(min_minutes, limit)
        arguments: dict[str, object] = {"min_minutes": min_minutes, "limit": limit}
    else:
        start, end = validate_window(station_id, start, end, today=today)
        arguments = {
            "station_id": station_id,
            "start": start.isoformat(),
            "end": end.isoformat(),
        }
    if action == "dry-run":
        bq_client = bigquery.Client(project=PROJECT)
        if tool == "find_capture_gaps":
            job = bq_client.query(
                GAPS_SQL,
                job_config=gaps_config(min_minutes, dry_run=True),
                location=LOCATION,
                timeout=30,
                retry=NO_RETRY,
                job_retry=None,
            )
            if job.total_bytes_processed is None:
                raise ToolError("No gap-query scan estimate was returned.")
            estimated = int(job.total_bytes_processed)
            print(f"Gap query estimate: {estimated} bytes; cap: {MAXIMUM_BYTES_BILLED}")
            if estimated > MAXIMUM_BYTES_BILLED:
                raise ToolError("The gap query exceeds the 256 MiB cap.")
        elif start.date() < today:
            estimated = estimate_history(bq_client, station_id, start, end, today=today)
            count = len(history_dates(start, today))
            print(
                f"History: {count} partition queries; estimated total: {estimated} bytes"
            )
            print(
                f"Per-query cap: {MAXIMUM_BYTES_BILLED}; request cap: {MAXIMUM_REQUEST_BYTES}"
            )
        else:
            job = bq_client.query(
                SQL,
                job_config=query_config(station_id, start, end, dry_run=True),
                location=LOCATION,
                timeout=30,
                retry=NO_RETRY,
                job_retry=None,
            )
            if job.total_bytes_processed is None:
                raise ToolError("No scan estimate was returned.")
            estimated = int(job.total_bytes_processed)
            print(
                f"Estimated scan: {estimated} bytes; cap: {MAXIMUM_BYTES_BILLED} bytes"
            )
            if estimated > MAXIMUM_BYTES_BILLED:
                raise ToolError("Over the cap. Do not run the live call yet.")
        return

    parameters = StdioServerParameters(
        command=sys.executable,
        args=[str(Path(__file__).with_name("server.py"))],
    )
    error: str | None = None
    async with Client(parameters) as client:
        tools = (await client.list_tools()).tools
        print("Available tools:", ", ".join(tool.name for tool in tools))
        if action == "call":
            result = await client.call_tool(tool, arguments)
            if result.is_error:
                error = str(result.content)
            else:
                print(json.dumps(result.structured_content, indent=2))
    if error is not None:
        raise ToolError(error)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["list", "dry-run", "call"])
    parser.add_argument(
        "--tool",
        choices=["get_station_availability", "find_capture_gaps"],
        default="get_station_availability",
    )
    parser.add_argument("--min-minutes", type=float, default=30.0)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--station-id", default="a3ad8072-a135-11e9-9cda-0a87ae2ba916")
    parser.add_argument("--start")
    parser.add_argument("--end")
    args = parser.parse_args()
    if args.tool == "find_capture_gaps" and (
        args.start is not None or args.end is not None
    ):
        parser.error("The gap tool uses yesterday/today UTC; omit --start and --end.")
    if (args.start is None) != (args.end is None):
        parser.error("Provide --start and --end together.")
    start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    if args.start is not None:
        try:
            start, end = (
                datetime.fromisoformat(args.start),
                datetime.fromisoformat(args.end),
            )
        except ValueError:
            parser.error("Use ISO timestamps, such as 2026-10-23T00:00:00+00:00.")
    try:
        asyncio.run(
            check(
                args.action,
                args.station_id,
                start,
                end,
                tool=args.tool,
                min_minutes=args.min_minutes,
                limit=args.limit,
            )
        )
    except ToolError as exc:
        raise SystemExit(str(exc)) from None
