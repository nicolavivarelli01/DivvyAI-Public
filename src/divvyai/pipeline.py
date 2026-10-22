"""Create a pipeline that connects the upload ZIP file to cloud storage and jsonl to bigquery"""

from google.cloud.bigquery import Client as BigQueryClient
from google.cloud.bigquery import LoadJob
from google.cloud.storage import Client as StorageClient

from divvyai.archive import build_capture_zip
from divvyai.capture import FeedCapture
from divvyai.cloud_bigquery import load_raw_jsonl
from divvyai.cloud_storage import upload_archive
from divvyai.identity import identify_capture
from divvyai.warehouse import build_raw_rows, serialize_raw_rows


def write_cloud_capture(
    station_information: FeedCapture,
    station_status: FeedCapture,
    *,
    storage_client: StorageClient,
    bigquery_client: BigQueryClient,
    bucket_name: str,
    table_id: str,
    job_id: str,
    location: str,
) -> LoadJob:
    """Archive the original responses, then prepare and load their raw rows."""
    if not job_id.strip():
        raise ValueError("job_id must not be blank")

    identity = identify_capture(station_information, station_status)
    archive = build_capture_zip(station_information, station_status)
    upload_archive(storage_client, bucket_name, identity, archive)

    rows = build_raw_rows(station_information, station_status)
    jsonl = serialize_raw_rows(rows)
    return load_raw_jsonl(
        bigquery_client,
        table_id,
        jsonl,
        job_id=job_id,
        location=location,
    )
