import argparse
import json
from collections.abc import Sequence
from contextlib import closing
from hashlib import sha256

from google.cloud.bigquery import Client as BigQueryClient
from google.cloud.bigquery import TableReference
from google.cloud.storage import Client as StorageClient

from divvyai.collector import fetch_feed
from divvyai.identity import identify_capture
from divvyai.local import INFORMATION_URL, STATUS_URL
from divvyai.pipeline import write_cloud_capture


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Fetch live Divvy feeds, archive them, and load BigQuery."
    )
    parser.add_argument("--project", required=True, help="Google Cloud project ID.")
    parser.add_argument("--bucket", required=True, help="Bucket name without gs://.")
    parser.add_argument("--table", required=True, help="project.dataset.table")
    parser.add_argument("--location", default="us-central1")
    args = parser.parse_args(argv)
    project: str = args.project
    bucket_name: str = args.bucket
    table_id: str = args.table
    location: str = args.location

    for name, value in (
        ("project", project),
        ("bucket", bucket_name),
        ("table", table_id),
        ("location", location),
    ):
        if not value.strip():
            parser.error(f"--{name} must not be blank")
    if "/" in bucket_name:
        parser.error("--bucket must be a bucket name without gs:// or an object path")
    try:
        table = TableReference.from_string(table_id)
    except ValueError as error:
        parser.error(str(error))
    if table.project != project:
        parser.error("--table must belong to the project given by --project")
    table_id = str(table)

    information = fetch_feed("station_information", INFORMATION_URL)
    status = fetch_feed("station_status", STATUS_URL)
    identity = identify_capture(information, status)
    job_key = f"{table_id}\n{identity.capture_id}"
    job_id = f"divvyai_cloud_raw_v1_{sha256(job_key.encode('utf-8')).hexdigest()}"

    print(f"Capture ID: {identity.capture_id}", flush=True)
    print(f"Archive target: gs://{bucket_name}/{identity.object_name}", flush=True)
    print(f"Planned load job: {project}:{location}.{job_id}", flush=True)

    with (
        closing(StorageClient(project=project)) as storage_client,
        closing(BigQueryClient(project=project, location=location)) as bigquery_client,
    ):
        job = write_cloud_capture(
            information,
            status,
            storage_client=storage_client,
            bigquery_client=bigquery_client,
            bucket_name=bucket_name,
            table_id=table_id,
            job_id=job_id,
            location=location,
        )
        if job.output_rows is None or job.ended is None:
            raise RuntimeError("Completed load is missing row count or completion time")

    receipt = {
        "event": "capture_loaded",
        "schema_version": 1,
        "capture_id": identity.capture_id,
        "archive_uri": f"gs://{bucket_name}/{identity.object_name}",
        "destination_table": table_id,
        "job_project": project,
        "job_location": location,
        "job_id": job_id,
        "output_rows": job.output_rows,
        "load_completed_at": job.ended.isoformat(),
    }
    print(json.dumps(receipt, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
