import json
from datetime import datetime
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from divvyai.capture import FeedCapture


def build_capture_zip(
    station_information: FeedCapture,
    station_status: FeedCapture,
) -> bytes:
    """Package both original responses and their receipt metadata in one ZIP."""
    captures = (station_information, station_status)
    expected_names = ("station_information", "station_status")

    for capture, expected_name in zip(captures, expected_names, strict=True):
        if capture.feed_name != expected_name:
            raise ValueError(f"Expected {expected_name}, got {capture.feed_name}.")

    manifest = {
        "format_version": 1,
        "feeds": [
            {
                "feed_name": capture.feed_name,
                "file_name": f"{capture.feed_name}.json",
                "source_url": capture.source_url,
                "received_at": capture.received_at.isoformat(),
            }
            for capture in captures
        ],
    }

    # bytesio is a buffer for the ZIP file
    # a buffer is a temporary storage location in memory
    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        for capture in captures:
            archive.writestr(f"{capture.feed_name}.json", capture.raw)
        archive.writestr("manifest.json", json.dumps(manifest, indent=2))

    return buffer.getvalue()


def read_capture_zip(data: bytes) -> tuple[FeedCapture, FeedCapture]:
    """Restore captures from a version-1 ZIP produced by this project."""
    with ZipFile(BytesIO(data)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        if not isinstance(manifest, dict) or manifest.get("format_version") != 1:
            raise ValueError("Unsupported archive format; expected version 1.")

        feeds = manifest.get("feeds")
        if not isinstance(feeds, list) or len(feeds) != 2:
            raise ValueError("Archive manifest must describe exactly two feeds.")

        captures: list[FeedCapture] = []
        expected_names = ("station_information", "station_status")
        for entry, expected_name in zip(feeds, expected_names, strict=True):
            file_name = f"{expected_name}.json"
            if (
                not isinstance(entry, dict)
                or entry.get("feed_name") != expected_name
                or entry.get("file_name") != file_name
            ):
                raise ValueError(f"Expected archive entry for {expected_name}.")

            source_url = entry.get("source_url")
            received_at = entry.get("received_at")
            if not isinstance(source_url, str) or not isinstance(received_at, str):
                raise ValueError("Archive URL and receipt time must be strings.")

            captures.append(
                FeedCapture(
                    feed_name=expected_name,
                    source_url=source_url,
                    received_at=datetime.fromisoformat(received_at),
                    raw=archive.read(file_name),
                )
            )

    return captures[0], captures[1]
