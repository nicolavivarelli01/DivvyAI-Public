import math
from datetime import UTC, datetime
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from divvyai.capture import FeedCapture


def fetch_feed(
    feed_name: str,
    source_url: str,
    *,
    timeout_seconds: float = 10.0,
    max_response_bytes: int = 5_000_000,
) -> FeedCapture:
    """Fetch one feed, preserving its body and recording receipt after reading it."""
    if feed_name not in {"station_information", "station_status"}:
        raise ValueError(f"Unsupported station feed: {feed_name!r}.")
    parsed_url = urlsplit(source_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
        raise ValueError("source_url must be an HTTP or HTTPS URL with a host.")
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be finite and positive.")
    if max_response_bytes <= 0:
        raise ValueError("max_response_bytes must be positive.")

    request = Request(
        source_url,
        headers={
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "User-Agent": "DivvyAI/0.1",
        },
    )
    with urlopen(request, timeout=timeout_seconds) as response:
        if response.status != 200:
            raise ValueError(f"Expected HTTP 200, got {response.status}.")
        encoding = response.headers.get("Content-Encoding", "identity").strip().lower()
        if encoding != "identity":
            raise ValueError(f"Unsupported response encoding: {encoding!r}.")

        length_header: str | None = response.headers.get("Content-Length")
        expected_length = int(length_header) if length_header is not None else None
        if expected_length is not None and expected_length < 0:
            raise ValueError("Content-Length must not be negative.")

        raw: bytes = response.read(max_response_bytes + 1)
        if len(raw) > max_response_bytes:
            raise ValueError("Response exceeds max_response_bytes.")
        if expected_length is not None and len(raw) != expected_length:
            raise ValueError("Response body does not match Content-Length.")

        received_at = datetime.now(UTC)
        final_url: str = response.geturl()

    return FeedCapture(
        feed_name=feed_name,
        source_url=final_url,
        received_at=received_at,
        raw=raw,
    )
