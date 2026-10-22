import json
from collections.abc import Sequence
from typing import TypedDict, cast

from divvyai.capture import FeedCapture
from divvyai.gbfs import parse_station_feed
from divvyai.identity import identify_capture


class RawStationRow(TypedDict):
    capture_id: str
    archive_object_name: str
    feed_name: str
    source_url: str
    received_at: str
    station_id: str
    feed_metadata: dict[str, object]
    payload: dict[str, object]


def build_raw_rows(
    station_information: FeedCapture,
    station_status: FeedCapture,
) -> list[RawStationRow]:
    """Prepare one raw row per station, feed, and capture."""
    identity = identify_capture(station_information, station_status)
    rows: list[RawStationRow] = []

    # going thru the captures
    for capture in (station_information, station_status):
        stations = parse_station_feed(capture.raw)
        document: dict[str, object] = json.loads(capture.raw)
        feed_metadata = {key: value for key, value in document.items() if key != "data"}

        for station in stations:
            rows.append(
                {
                    "capture_id": identity.capture_id,
                    "archive_object_name": identity.object_name,
                    "feed_name": capture.feed_name,
                    "source_url": capture.source_url,
                    "received_at": capture.received_at.isoformat(),
                    "station_id": cast(str, station["station_id"]),
                    "feed_metadata": feed_metadata,
                    "payload": station,
                }
            )

    return rows


def serialize_raw_rows(rows: Sequence[RawStationRow]) -> bytes:
    """Serialize a complete batch as UTF-8 newline-delimited JSON."""
    lines = (
        json.dumps(
            row,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
        for row in rows
    )
    return "".join(lines).encode("utf-8")
