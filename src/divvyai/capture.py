from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class FeedCapture:
    """Original feed response and the context in which it was received."""

    feed_name: str
    source_url: str
    received_at: datetime
    raw: bytes

    def __post_init__(self) -> None:
        if self.feed_name not in {"station_information", "station_status"}:
            raise ValueError(f"Unsupported station feed: {self.feed_name!r}.")

        if not self.source_url.strip():
            raise ValueError("source_url must not be blank.")

        if self.received_at.utcoffset() is None:
            raise ValueError("received_at must be timezone-aware.")

        object.__setattr__(self, "received_at", self.received_at.astimezone(UTC))
