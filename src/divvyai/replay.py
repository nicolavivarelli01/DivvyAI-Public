import argparse
import json
from collections.abc import Sequence
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from google.cloud.bigquery import Client, TableReference

from divvyai.recovery import recover_archive


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Recover a saved cloud-collector capture using its original job ID."
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--table", required=True, help="project.dataset.table")
    parser.add_argument("--archive", required=True, type=Path, help="Local ZIP path.")
    parser.add_argument("--job-id", required=True, help="Original short import job ID.")
    parser.add_argument("--location", default="us-central1")
    args = parser.parse_args(argv)
    project: str = args.project
    table_id: str = args.table
    job_id: str = args.job_id
    location: str = args.location
    archive_path: Path = args.archive

    for name, value in (
        ("project", project),
        ("table", table_id),
        ("job-id", job_id),
        ("location", location),
    ):
        if not value.strip():
            parser.error(f"--{name} must not be blank")
    try:
        table = TableReference.from_string(table_id)
    except ValueError as error:
        parser.error(str(error))
    if table.project != project:
        parser.error("--table must belong to the project given by --project")
    table_id = str(table)
    archive = archive_path.read_bytes()

    with closing(Client(project=project, location=location)) as client:
        job = recover_archive(
            client,
            table_id,
            archive,
            job_id=job_id,
            location=location,
            now=datetime.now(UTC),
        )
        if job.output_rows is None or job.ended is None:
            raise RuntimeError("Completed load is missing row count or completion time")

    receipt = {
        "event": "recovery_completed",
        "schema_version": 1,
        "archive_path": str(archive_path),
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
