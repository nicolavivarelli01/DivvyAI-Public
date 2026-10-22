from datetime import datetime, timedelta
from hashlib import sha256

from google.cloud.bigquery import Client, LoadJob, TableReference

from divvyai.archive import read_capture_zip
from divvyai.cloud_bigquery import get_existing_load, load_raw_jsonl
from divvyai.identity import identify_capture
from divvyai.warehouse import build_raw_rows, serialize_raw_rows


def recover_archive(
    client: Client,
    table_id: str,
    archive: bytes,
    *,
    job_id: str,
    location: str,
    now: datetime,
) -> LoadJob:
    """Recover a cloud-collector capture using its recorded original job ID."""
    table = TableReference.from_string(table_id)
    if table.project != client.project:
        raise ValueError("The table and BigQuery client must use the same project.")
    table_id = str(table)

    information, status = read_capture_zip(archive)
    identity = identify_capture(information, status)
    job_key = f"{table_id}\n{identity.capture_id}"
    expected_id = f"divvyai_cloud_raw_v1_{sha256(job_key.encode('utf-8')).hexdigest()}"
    if job_id != expected_id:
        raise ValueError("Original job ID does not match this capture and table.")

    existing = get_existing_load(client, table_id, job_id=job_id, location=location)
    if existing is not None:
        return existing

    latest_receipt = max(information.received_at, status.received_at)
    age = now - latest_receipt
    if not timedelta(0) <= age <= timedelta(days=90):
        raise ValueError(
            "Missing-job recovery requires a capture from the last 90 days."
        )

    rows = build_raw_rows(information, status)
    return load_raw_jsonl(
        client,
        table_id,
        serialize_raw_rows(rows),
        job_id=job_id,
        location=location,
    )
