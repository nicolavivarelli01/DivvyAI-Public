"""This file contains tests for the GBFS station feed parser."""

import json
from pathlib import Path

import pytest

from divvyai.gbfs import parse_station_feed

FIXTURE = Path(__file__).parent / "fixtures" / "station_status.json"


def test_parses_station_status_fixture() -> None:
    records = parse_station_feed(FIXTURE.read_bytes())

    assert records == [
        {
            "station_id": "001",
            "num_bikes_available": 3,
            "num_docks_available": 7,
            "is_installed": 1,
            "is_renting": 1,
            "is_returning": 1,
            "last_reported": 1791503990,
        },
        {
            "station_id": "002",
            "num_bikes_available": 0,
            "num_docks_available": 12,
            "is_installed": 1,
            "is_renting": 1,
            "is_returning": 1,
            "last_reported": 1791503980,
        },
    ]


def test_accepts_empty_station_array() -> None:
    assert parse_station_feed(b'{"data": {"stations": []}}') == []


def test_preserves_station_information_fields() -> None:
    station = {"station_id": "003", "name": "Synthetic Station", "capacity": 10}
    raw = json.dumps({"data": {"stations": [station]}}).encode("utf-8")

    assert parse_station_feed(raw) == [station]


@pytest.mark.parametrize("raw", [b"not-json", b'{"data":', b"\xff"])
def test_rejects_malformed_body(raw: bytes) -> None:
    with pytest.raises(ValueError, match="Feed body must be UTF-8 JSON"):
        parse_station_feed(raw)


@pytest.mark.parametrize(
    ("document", "message"),
    [
        (None, "root must"),
        ([], "root must"),
        ({}, "data must"),
        ({"data": []}, "data must"),
        ({"data": {}}, "stations must"),
        ({"data": {"stations": {}}}, "stations must"),
        ({"data": {"stations": [42]}}, "index 0 must be"),
        ({"data": {"stations": [{}]}}, "nonempty string station_id"),
        (
            {"data": {"stations": [{"station_id": ""}]}},
            "nonempty string station_id",
        ),
        (
            {"data": {"stations": [{"station_id": "   "}]}},
            "nonempty string station_id",
        ),
        (
            {"data": {"stations": [{"station_id": 123}]}},
            "nonempty string station_id",
        ),
        (
            {"data": {"stations": [{"station_id": "001"}, {"station_id": "001"}]}},
            "Duplicate station_id",
        ),
    ],
)
def test_rejects_invalid_structure(document: object, message: str) -> None:
    raw = json.dumps(document).encode("utf-8")

    with pytest.raises(ValueError, match=message):
        parse_station_feed(raw)
