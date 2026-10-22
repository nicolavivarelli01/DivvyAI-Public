from io import BytesIO

from google.api_core.exceptions import NotFound
from google.cloud.bigquery import (
    Client,
    CreateDisposition,
    LoadJob,
    LoadJobConfig,
    SourceFormat,
    TableReference,
    WriteDisposition,
)


def load_raw_jsonl(
    client: Client,
    table_id: str,
    jsonl: bytes,
    *,
    job_id: str,
    location: str,
) -> LoadJob:
    """Append JSONL to an existing table and wait for the load to succeed."""
    if not job_id.strip():
        raise ValueError("job_id must not be blank")
    if not jsonl:
        raise ValueError("jsonl must contain records")

    config = LoadJobConfig(
        source_format=SourceFormat.NEWLINE_DELIMITED_JSON,
        write_disposition=WriteDisposition.WRITE_APPEND,
        create_disposition=CreateDisposition.CREATE_NEVER,
        autodetect=False,
        max_bad_records=0,
        ignore_unknown_values=False,
    )
    with BytesIO(jsonl) as source:
        job = client.load_table_from_file(
            source,
            table_id,
            job_id=job_id,
            location=location,
            job_config=config,
            size=len(jsonl),
            num_retries=0,
            timeout=30,
        )

    job.result(timeout=60, retry=None)
    return job


def get_existing_load(
    client: Client,
    table_id: str,
    *,
    job_id: str,
    location: str,
) -> LoadJob | None:
    """Find an existing load and wait for success; return None if not found."""
    expected_table = TableReference.from_string(table_id)
    try:
        job = client.get_job(job_id, location=location, timeout=30)
    except NotFound:
        return None

    if not isinstance(job, LoadJob) or job.destination != expected_table:
        raise ValueError(f"Expected a load job for {table_id}.")
    if job.state is None:
        raise ValueError("Existing job is missing its state.")

    job.result(timeout=60, retry=None)
    return job
