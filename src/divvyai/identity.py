import json
from dataclasses import dataclass
from hashlib import sha256

from divvyai.capture import FeedCapture


@dataclass(frozen=True)
class CaptureIdentity:
    capture_id: str
    object_name: str


def identify_capture(
    station_information: FeedCapture,
    station_status: FeedCapture,
) -> CaptureIdentity:
    """Derive a stable ID and UTC archive path from a pair of captured feeds."""
    captures = (station_information, station_status)
    if tuple(capture.feed_name for capture in captures) != (
        "station_information",
        "station_status",
    ):
        raise ValueError("Expected station_information followed by station_status.")

    identity_data = [
        {
            "feed_name": capture.feed_name,
            "source_url": capture.source_url,
            "received_at": capture.received_at.isoformat(),
            "raw_sha256": sha256(capture.raw).hexdigest(),
        }
        for capture in captures
    ]
    identity_json = json.dumps(identity_data, sort_keys=True, separators=(",", ":"))
    capture_id = sha256(identity_json.encode("utf-8")).hexdigest()

    latest_receipt = max(capture.received_at for capture in captures)
    object_name = (
        f"raw/date={latest_receipt:%Y-%m-%d}/hour={latest_receipt:%H}/{capture_id}.zip"
    )
    return CaptureIdentity(capture_id=capture_id, object_name=object_name)
