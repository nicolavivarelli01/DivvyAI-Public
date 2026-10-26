"""Opt-in integration checks against the real public Divvy feeds.

Skipped unless DIVVYAI_LIVE_TESTS=1. They make two public HTTP requests and
never touch Google Cloud.
"""

import json
import os
from datetime import timedelta

import pytest

from divvyai.archive import build_capture_zip, read_capture_zip
from divvyai.collector import fetch_feed
from divvyai.identity import identify_capture
from divvyai.local import INFORMATION_URL, STATUS_URL
from divvyai.warehouse import build_raw_rows, serialize_raw_rows

pytestmark = pytest.mark.skipif(
    os.environ.get("DIVVYAI_LIVE_TESTS") != "1",
    reason="Live feed tests are opt-in; set DIVVYAI_LIVE_TESTS=1.",
)


def test_live_feeds_survive_archive_and_load_row_preparation() -> None:
    information = fetch_feed("station_information", INFORMATION_URL)
    status = fetch_feed("station_status", STATUS_URL)

    for capture in (information, status):
        assert capture.raw
        assert capture.received_at.utcoffset() == timedelta(0)
    assert information.received_at <= status.received_at

    # The archive must hold the original bytes and receipt metadata unchanged.
    restored = read_capture_zip(build_capture_zip(information, status))
    assert restored == (information, status)

    rows = build_raw_rows(information, status)
    information_ids = {
        row["station_id"] for row in rows if row["feed_name"] == "station_information"
    }
    status_ids = {
        row["station_id"] for row in rows if row["feed_name"] == "station_status"
    }
    assert information_ids
    assert status_ids
    # Both feeds must describe the same network, so their stations must overlap.
    assert information_ids & status_ids

    capture_id = identify_capture(information, status).capture_id
    lines = serialize_raw_rows(rows).splitlines()
    assert len(lines) == len(rows)
    assert {json.loads(line)["capture_id"] for line in lines} == {capture_id}
