import json
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import call, patch
from urllib.error import URLError
from zipfile import ZipFile

import pytest

from divvyai.capture import FeedCapture
from divvyai.identity import identify_capture
from divvyai.local import INFORMATION_URL, STATUS_URL, main, write_local_capture

FIXTURES = Path(__file__).parent / "fixtures"
INFORMATION = FeedCapture(
    feed_name="station_information",
    source_url="https://example.test/station_information.json",
    received_at=datetime(2026, 10, 9, 14, 5, tzinfo=UTC),
    raw=(FIXTURES / "station_information.json").read_bytes(),
)
STATUS = FeedCapture(
    feed_name="station_status",
    source_url="https://example.test/station_status.json",
    received_at=datetime(2026, 10, 9, 14, 5, 2, tzinfo=UTC),
    raw=(FIXTURES / "station_status.json").read_bytes(),
)


def test_writes_archive_and_four_load_rows_with_matching_provenance(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "demo"
    identity = identify_capture(INFORMATION, STATUS)

    result = write_local_capture(INFORMATION, STATUS, output_dir)

    assert result.archive_path == output_dir / identity.object_name
    assert result.rows_path == output_dir / "warehouse" / f"{identity.capture_id}.jsonl"
    assert result.row_count == 4
    with ZipFile(result.archive_path) as archive:
        assert archive.read("station_information.json") == INFORMATION.raw
        assert archive.read("station_status.json") == STATUS.raw
        manifest = json.loads(archive.read("manifest.json"))
    assert manifest["feeds"][0]["received_at"] == "2026-10-09T14:05:00+00:00"
    assert manifest["feeds"][1]["received_at"] == "2026-10-09T14:05:02+00:00"
    rows = [json.loads(line) for line in result.rows_path.read_bytes().splitlines()]
    assert len(rows) == 4
    assert {row["capture_id"] for row in rows} == {identity.capture_id}
    assert {row["archive_object_name"] for row in rows} == {identity.object_name}
    assert {(row["feed_name"], row["station_id"]) for row in rows} == {
        ("station_information", "001"),
        ("station_information", "002"),
        ("station_status", "001"),
        ("station_status", "002"),
    }


@pytest.mark.parametrize(
    "raw",
    [
        b"not-json\n",
        b'{"data":{"stations":[{"station_id":"001","num_bikes_available":NaN}]}}',
    ],
)
def test_failed_row_preparation_keeps_original_archive(
    tmp_path: Path, raw: bytes
) -> None:
    status = replace(STATUS, raw=raw)
    identity = identify_capture(INFORMATION, status)

    with pytest.raises(ValueError):
        write_local_capture(INFORMATION, status, tmp_path)

    with ZipFile(tmp_path / identity.object_name) as archive:
        assert archive.read("station_status.json") == raw
    assert not list(tmp_path.rglob("*.jsonl"))


def test_repeated_capture_reuses_paths_and_load_content(tmp_path: Path) -> None:
    first = write_local_capture(INFORMATION, STATUS, tmp_path)
    first_rows = first.rows_path.read_bytes()

    second = write_local_capture(INFORMATION, STATUS, tmp_path)

    assert second == first
    assert second.rows_path.read_bytes() == first_rows
    assert list(tmp_path.rglob("*.zip")) == [first.archive_path]
    assert list(tmp_path.rglob("*.jsonl")) == [first.rows_path]


def test_empty_feeds_create_an_archive_and_empty_load_file(tmp_path: Path) -> None:
    empty = b'{"data":{"stations":[]}}'

    result = write_local_capture(
        replace(INFORMATION, raw=empty), replace(STATUS, raw=empty), tmp_path
    )

    assert result.archive_path.exists()
    assert result.row_count == 0
    assert result.rows_path.read_bytes() == b""


def test_cli_uses_selected_directories_and_reports_fixture_mode(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output_dir = tmp_path / "chosen-output"
    with patch(
        "divvyai.local.fetch_feed", side_effect=AssertionError("Network forbidden")
    ):
        main(["--fixture-dir", str(FIXTURES), "--output-dir", str(output_dir)])

    output = capsys.readouterr().out
    assert "Mode: fixture (synthetic data)" in output
    assert "Rows: 4" in output
    assert str(output_dir) in output
    assert len(list(output_dir.rglob("*.zip"))) == 1
    assert len(list(output_dir.rglob("*.jsonl"))) == 1


def test_missing_second_fixture_fails_before_writing_outputs(tmp_path: Path) -> None:
    fixture_dir = tmp_path / "fixtures"
    fixture_dir.mkdir()
    (fixture_dir / "station_information.json").write_bytes(INFORMATION.raw)
    output_dir = tmp_path / "output"

    with pytest.raises(FileNotFoundError):
        main(["--fixture-dir", str(fixture_dir), "--output-dir", str(output_dir)])

    assert not output_dir.exists()


def test_live_cli_fetches_both_feeds_and_preserves_receipt_times(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    output_dir = tmp_path / "live-output"
    information = replace(
        INFORMATION,
        source_url=INFORMATION_URL,
        received_at=datetime(2026, 10, 10, 15, 0, 1, tzinfo=UTC),
    )
    status = replace(
        STATUS,
        source_url=STATUS_URL,
        received_at=datetime(2026, 10, 10, 15, 0, 3, tzinfo=UTC),
    )

    with patch("divvyai.local.fetch_feed", side_effect=[information, status]) as fetch:
        main(["--live", "--output-dir", str(output_dir)])

    assert fetch.call_args_list == [
        call("station_information", INFORMATION_URL),
        call("station_status", STATUS_URL),
    ]
    archive_path = next(output_dir.rglob("*.zip"))
    with ZipFile(archive_path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    assert [feed["source_url"] for feed in manifest["feeds"]] == [
        INFORMATION_URL,
        STATUS_URL,
    ]
    assert [feed["received_at"] for feed in manifest["feeds"]] == [
        information.received_at.isoformat(),
        status.received_at.isoformat(),
    ]
    rows_path = next(output_dir.rglob("*.jsonl"))
    rows = [json.loads(line) for line in rows_path.read_bytes().splitlines()]
    assert len(rows) == 4
    assert {(row["feed_name"], row["received_at"]) for row in rows} == {
        ("station_information", information.received_at.isoformat()),
        ("station_status", status.received_at.isoformat()),
    }
    output = capsys.readouterr().out
    assert "Mode: live (Divvy API)" in output
    assert "Rows: 4" in output
    for capture in (information, status):
        assert (
            f"{capture.feed_name}: {len(capture.raw)} bytes; "
            f"received_at={capture.received_at.isoformat()}"
        ) in output


@pytest.mark.parametrize("failed_request", [1, 2])
def test_live_network_failure_stops_before_creating_outputs(
    tmp_path: Path, failed_request: int
) -> None:
    output_dir = tmp_path / "output"
    responses: list[FeedCapture | URLError] = [URLError("offline")]
    if failed_request == 2:
        responses.insert(0, INFORMATION)

    with patch("divvyai.local.fetch_feed", side_effect=responses) as fetch:
        with pytest.raises(URLError, match="offline"):
            main(["--live", "--output-dir", str(output_dir)])

    assert fetch.call_count == failed_request
    assert not output_dir.exists()


@pytest.mark.parametrize("live", [False, True])
def test_cli_default_output_directory_matches_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, live: bool
) -> None:
    monkeypatch.chdir(tmp_path)
    arguments = ["--fixture-dir", str(FIXTURES)]
    if live:
        arguments.append("--live")

    with patch("divvyai.local.fetch_feed", side_effect=[INFORMATION, STATUS]) as fetch:
        main(arguments)

    output_dir = tmp_path / "data" / ("live" if live else "demo")
    assert len(list(output_dir.rglob("*.zip"))) == 1
    assert len(list(output_dir.rglob("*.jsonl"))) == 1
    if live:
        assert fetch.call_count == 2
    else:
        fetch.assert_not_called()


def test_module_entrypoint_displays_help() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "divvyai.local", "--help"],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )

    assert "--live" in result.stdout
    assert "--output-dir" in result.stdout
