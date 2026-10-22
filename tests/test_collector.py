from datetime import UTC, datetime, tzinfo
from email.message import Message
from io import BytesIO
from unittest.mock import Mock
from urllib.error import HTTPError, URLError
from urllib.request import Request
from urllib.response import addinfourl

import pytest

import divvyai.collector as collector

SOURCE_URL = "https://example.test/station_status.json"
RECEIVED_AT = datetime(2026, 10, 9, 14, 5, tzinfo=UTC)
RAW = b'{ "data": { "stations": [] } }\n'


def _response(
    raw: bytes = RAW, *, status: int = 200, url: str = SOURCE_URL
) -> addinfourl:
    return addinfourl(BytesIO(raw), Message(), url, code=status)


@pytest.fixture
def transport(monkeypatch: pytest.MonkeyPatch) -> Mock:
    mock = Mock()
    monkeypatch.setattr(collector, "urlopen", mock)
    return mock


@pytest.fixture(autouse=True)
def clock(monkeypatch: pytest.MonkeyPatch) -> Mock:
    mock = Mock()
    mock.now.return_value = RECEIVED_AT
    monkeypatch.setattr(collector, "datetime", mock)
    return mock


@pytest.mark.parametrize("feed_name", ["station_information", "station_status"])
def test_preserves_bytes_and_timestamps_only_after_reading(
    transport: Mock, clock: Mock, feed_name: str
) -> None:
    source_url = f"https://example.test/{feed_name}.json"
    body = BytesIO(RAW)
    response = addinfourl(body, Message(), source_url, code=200)
    transport.return_value = response

    def after_read(tz: tzinfo) -> datetime:
        assert tz is UTC
        assert body.tell() == len(RAW)
        return RECEIVED_AT

    clock.now.side_effect = after_read
    capture = collector.fetch_feed(feed_name, source_url)

    assert capture.raw == RAW
    assert capture.received_at == RECEIVED_AT
    assert capture.source_url == source_url
    assert capture.feed_name == feed_name
    assert body.closed
    transport.assert_called_once()
    request = transport.call_args.args[0]
    assert isinstance(request, Request)
    assert request.full_url == source_url
    assert request.get_method() == "GET"
    assert request.get_header("Accept") == "application/json"
    assert request.get_header("Accept-encoding") == "identity"
    assert request.get_header("User-agent") == "DivvyAI/0.1"
    assert transport.call_args.kwargs == {"timeout": 10.0}


@pytest.mark.parametrize("raw", [b"not-json", b"", b"\xff"])
def test_retains_unparseable_http_200_body(transport: Mock, raw: bytes) -> None:
    transport.return_value = _response(raw)

    assert collector.fetch_feed("station_status", SOURCE_URL).raw == raw


def test_accepts_body_at_exact_size_limit_and_custom_timeout(transport: Mock) -> None:
    transport.return_value = _response()

    capture = collector.fetch_feed(
        "station_status", SOURCE_URL, max_response_bytes=len(RAW), timeout_seconds=2.0
    )

    assert capture.raw == RAW
    assert transport.call_args.kwargs == {"timeout": 2.0}


def test_rejects_oversized_body_and_closes_response(
    transport: Mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = _response()
    read = Mock(wraps=response.read)
    monkeypatch.setattr(response, "read", read)
    transport.return_value = response

    with pytest.raises(ValueError, match="exceeds max_response_bytes"):
        collector.fetch_feed(
            "station_status", SOURCE_URL, max_response_bytes=len(RAW) - 1
        )

    read.assert_called_once_with(len(RAW))
    assert response.closed


@pytest.mark.parametrize("status", [204, 206])
def test_rejects_non_200_response(transport: Mock, status: int) -> None:
    response = _response(status=status)
    transport.return_value = response

    with pytest.raises(ValueError, match="Expected HTTP 200"):
        collector.fetch_feed("station_status", SOURCE_URL)

    assert response.closed


def test_checks_advertised_content_length(transport: Mock) -> None:
    response = _response()
    response.headers["Content-Length"] = str(len(RAW))
    transport.return_value = response

    assert collector.fetch_feed("station_status", SOURCE_URL).raw == RAW


def test_rejects_truncated_body_and_closes_response(transport: Mock) -> None:
    response = _response()
    response.headers["Content-Length"] = str(len(RAW) + 1)
    transport.return_value = response

    with pytest.raises(ValueError, match="does not match Content-Length"):
        collector.fetch_feed("station_status", SOURCE_URL)

    assert response.closed


@pytest.mark.parametrize("length", ["-1", "invalid", ""])
def test_rejects_invalid_content_length(transport: Mock, length: str) -> None:
    response = _response()
    response.headers["Content-Length"] = length
    transport.return_value = response

    with pytest.raises(ValueError):
        collector.fetch_feed("station_status", SOURCE_URL)

    assert response.closed


def test_rejects_unexpected_compression(transport: Mock) -> None:
    response = _response()
    response.headers["Content-Encoding"] = "gzip"
    transport.return_value = response

    with pytest.raises(ValueError, match="Unsupported response encoding"):
        collector.fetch_feed("station_status", SOURCE_URL)

    assert response.closed


def test_accepts_explicit_identity_encoding(transport: Mock) -> None:
    response = _response()
    response.headers["Content-Encoding"] = "Identity"
    transport.return_value = response

    assert collector.fetch_feed("station_status", SOURCE_URL).raw == RAW


def test_records_final_url_after_a_redirect(transport: Mock) -> None:
    final_url = "https://other.test/station_status.json"
    transport.return_value = _response(url=final_url)

    assert collector.fetch_feed("station_status", SOURCE_URL).source_url == final_url


@pytest.mark.parametrize(
    "error",
    [
        URLError("synthetic network failure"),
        TimeoutError("synthetic timeout"),
        HTTPError(SOURCE_URL, 503, "Unavailable", Message(), None),
    ],
)
def test_request_failures_propagate(transport: Mock, error: Exception) -> None:
    transport.side_effect = error

    with pytest.raises(type(error)):
        collector.fetch_feed("station_status", SOURCE_URL)

    transport.assert_called_once()


def test_read_timeout_propagates_and_closes_response(
    transport: Mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = _response()
    transport.return_value = response

    def fail_read(size: int) -> bytes:
        raise TimeoutError("synthetic read timeout")

    monkeypatch.setattr(response, "read", fail_read)
    with pytest.raises(TimeoutError):
        collector.fetch_feed("station_status", SOURCE_URL)

    assert response.closed


@pytest.mark.parametrize("feed_name", ["gbfs", "station_status/other"])
def test_invalid_feed_name_fails_before_request(
    transport: Mock, feed_name: str
) -> None:
    with pytest.raises(ValueError, match="Unsupported station feed"):
        collector.fetch_feed(feed_name, SOURCE_URL)

    transport.assert_not_called()


@pytest.mark.parametrize("source_url", ["file:///synthetic.json", "https://", ""])
def test_invalid_url_fails_before_request(transport: Mock, source_url: str) -> None:
    with pytest.raises(ValueError, match="HTTP or HTTPS URL"):
        collector.fetch_feed("station_status", source_url)

    transport.assert_not_called()


@pytest.mark.parametrize("timeout", [0.0, -1.0, float("nan"), float("inf")])
def test_invalid_timeout_fails_before_request(transport: Mock, timeout: float) -> None:
    with pytest.raises(ValueError, match="finite and positive"):
        collector.fetch_feed("station_status", SOURCE_URL, timeout_seconds=timeout)

    transport.assert_not_called()


@pytest.mark.parametrize("limit", [0, -1])
def test_invalid_size_limit_fails_before_request(transport: Mock, limit: int) -> None:
    with pytest.raises(ValueError, match="max_response_bytes must be positive"):
        collector.fetch_feed("station_status", SOURCE_URL, max_response_bytes=limit)

    transport.assert_not_called()
