import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from divvyai.archive import build_capture_zip
from divvyai.capture import FeedCapture
from divvyai.collector import fetch_feed
from divvyai.identity import identify_capture
from divvyai.warehouse import build_raw_rows, serialize_raw_rows

# URLs for fetching Divvy station information and status feeds
INFORMATION_URL = "https://gbfs.lyft.com/gbfs/1.1/chi/en/station_information.json"
STATUS_URL = "https://gbfs.lyft.com/gbfs/1.1/chi/en/station_status.json"


@dataclass(frozen=True)
class LocalArtifacts:
    archive_path: Path
    rows_path: Path
    row_count: int


def write_local_capture(
    station_information: FeedCapture,
    station_status: FeedCapture,
    output_dir: Path,
) -> LocalArtifacts:
    """Save raw evidence before preparing the warehouse load file."""
    identity = identify_capture(station_information, station_status)
    archive_path = output_dir / identity.object_name
    archive_bytes = build_capture_zip(station_information, station_status)
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    archive_path.write_bytes(archive_bytes)

    rows = build_raw_rows(station_information, station_status)
    rows_bytes = serialize_raw_rows(rows)
    rows_path = output_dir / "warehouse" / f"{identity.capture_id}.jsonl"
    rows_path.parent.mkdir(parents=True, exist_ok=True)
    rows_path.write_bytes(rows_bytes)

    return LocalArtifacts(
        archive_path=archive_path, rows_path=rows_path, row_count=len(rows)
    )


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Capture Divvy feeds locally.")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Fetch real Divvy feeds instead of fixtures.",
    )
    parser.add_argument("--fixture-dir", type=Path, default=Path("tests/fixtures"))
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    live: bool = args.live
    fixture_dir: Path = args.fixture_dir
    output_dir: Path = args.output_dir or Path("data/live" if live else "data/demo")

    if live:
        information = fetch_feed("station_information", INFORMATION_URL)
        status = fetch_feed("station_status", STATUS_URL)
    else:
        information = FeedCapture(
            feed_name="station_information",
            source_url="https://example.test/station_information.json",
            received_at=datetime(2026, 10, 9, 14, 5, tzinfo=UTC),
            raw=(fixture_dir / "station_information.json").read_bytes(),
        )
        status = FeedCapture(
            feed_name="station_status",
            source_url="https://example.test/station_status.json",
            received_at=datetime(2026, 10, 9, 14, 5, 2, tzinfo=UTC),
            raw=(fixture_dir / "station_status.json").read_bytes(),
        )

    result = write_local_capture(information, status, output_dir)
    print("Mode: live (Divvy API)" if live else "Mode: fixture (synthetic data)")
    for capture in (information, status):
        print(
            f"{capture.feed_name}: {len(capture.raw)} bytes; "
            f"received_at={capture.received_at.isoformat()}"
        )
    print(f"Rows: {result.row_count}")
    print(f"Archive: {result.archive_path}")
    print(f"Load file: {result.rows_path}")


if __name__ == "__main__":
    main()
