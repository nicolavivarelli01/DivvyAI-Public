# DivvyAI runbook

Documentation checkpoint: 2026-10-19 UTC. Commands are operational references;
run them only for the operation you intend. No cloud execution is needed for
a documentation-only update.

## Resources and identities

Project: divvy-ai. gcloud configuration: divvyai. Region: us-central1.

| Resource | Identifier |
|---|---|
| Archive bucket | gs://divvy-ai-raw-576327842002 |
| Raw table | divvy-ai.divvyai_raw.station_records |
| Analytics dataset | divvy-ai.divvyai_analytics |
| SDK acceptance dataset | divvy-ai.divvyai_test; current contents not rechecked |
| Artifact Registry repository | divvyai |
| Container image | us-central1-docker.pkg.dev/divvy-ai/divvyai/collector:v1 |
| Cloud Run Job | divvyai-collector |
| Cloud Scheduler job | divvyai-collect-15m |

The scheduler identity, divvyai-scheduler@divvy-ai.iam.gserviceaccount.com,
has Cloud Run Invoker on this job. It launches executions.

The collector identity, divvyai-collector@divvy-ai.iam.gserviceaccount.com,
has Storage Object Creator on the bucket, BigQuery Job User on the project
and BigQuery Data Editor on the raw table. Data Editor permits more than append;
the application chooses CREATE_NEVER and WRITE_APPEND.

Google's Cloud Run service agent is
service-576327842002@serverless-robot-prod.iam.gserviceaccount.com. The report
shows roles/run.serviceAgent on the project. This is distinct from the running
collector's identity and participates in retrieving its container image.

Local development and dbt use user authentication/ADC outside Git.
Do not download service-account keys. The deployed image is a snapshot of the
build inputs; editing local recovery code or dbt SQL does not update that image.

## Local setup and offline checks

Run from the repository root:

```bash
uv sync --locked
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run python -m divvyai.local
```

Expected: Python checks pass; the fixture demo prints four raw rows and paths
under data/demo/raw/ and data/demo/warehouse/. These checks do not call GCP.

Explicit local live capture:

```bash
uv run python -m divvyai.local --live
```

This fetches both public feeds and saves a ZIP plus JSONL under data/live/.
It does not upload to GCP. Both requests must succeed before the paired ZIP is
written. Station parsing and row preparation happen after archival.

Generated local captures remain on disk and are ignored by Git.

## Scheduled collection and manual execution

Scheduler uses */15 * * * * in Etc/UTC and an authenticated POST to the Run job.
The configured launch deadline is 30 seconds and Scheduler retries are zero.
The Run job has one task, one CPU, 512 MiB, a 60-second task timeout and zero
configured task retries. Separate executions can overlap.

An extra manual run creates a new capture and consumes Cloud Run runtime,
Storage operations/storage and BigQuery storage. Check available allowances
before executing; current billing has not been established by application logs.

```bash
gcloud run jobs execute divvyai-collector \
  --wait --region=us-central1 \
  --project=divvy-ai --configuration=divvyai
```

List recent executions and successful load receipts:

```bash
gcloud run jobs executions list \
  --job=divvyai-collector --region=us-central1 --limit=10 \
  --project=divvy-ai --configuration=divvyai

gcloud logging read \
  'resource.type="cloud_run_job" AND resource.labels.job_name="divvyai-collector" AND resource.labels.location="us-central1" AND jsonPayload.event="capture_loaded"' \
  --freshness=2h --limit=5 --format=json \
  --project=divvy-ai --configuration=divvyai
```

Scheduler HTTP 200 proves launch acceptance. Confirm execution status and a
capture_loaded receipt containing capture_id, archive_uri, job_id, output_rows
and load_completed_at to assess the completed ingestion.

Admin metadata and stored-log reads have no additional GCP query charge.
Use execution-specific names and fixed UTC log windows when investigating an
older incident. The saved verification SQL files target particular historical
captures; they are not generic checks for the latest run.

## Pause and resume collection

```bash
gcloud scheduler jobs pause divvyai-collect-15m \
  --location=us-central1 --project=divvy-ai --configuration=divvyai

gcloud scheduler jobs resume divvyai-collect-15m \
  --location=us-central1 --project=divvy-ai --configuration=divvyai
```

Pause stops future scheduled launches; it does not stop an already launched
execution or delete stored data. Resume restarts the schedule; it does not
recreate missed historical observations. Review costs before resuming collection.

## Manual archive recovery

Recovery is a local operator action, not an automatic part of the Run job.
It needs a saved archive and its original planned load-job ID, from the original
collector output. Downloading an archive consumes Storage operations and may
consume transfer allowance; submitting a missing load adds BigQuery storage.

For the original verified acceptance capture, the commands were:

```bash
mkdir -p data/recovery
gcloud storage cp \
  gs://divvy-ai-raw-576327842002/raw/date=2026-10-13/hour=06/e2356ba95b41db44a7fb55b64dc5627d7736807b773ecdd3b438cc93670f1c25.zip \
  data/recovery/cloud-run-capture.zip \
  --project=divvy-ai --configuration=divvyai

uv run python -m divvyai.replay \
  --project=divvy-ai \
  --table=divvy-ai.divvyai_raw.station_records \
  --archive=data/recovery/cloud-run-capture.zip \
  --job-id=divvyai_cloud_raw_v1_cbd0d076a7ebdeb926030031154fe19b177a8fe915257baac44bff0cfe236a75 \
  --location=us-central1
```

Use the actual archive and original ID for the capture being recovered.
Only the cloud collector's canonical divvyai_cloud_raw_v1_ job IDs are supported;
legacy manually named bq load jobs do not satisfy this recovery check.
Recovery verifies the archive/table/job relationship and reuses a verified
completed load. Missing-job recovery accepts captures up to 90 days old.
Existing failed or uncertain jobs stop for investigation. Do not invent a new
job ID to bypass these checks. Reusing an existing completed load does not
reload its rows, even if those rows have expired. Expired-data restoration is
not a supported recovery workflow. Replay cannot recreate observations never
fetched.

## dbt, query controls and interpretation

See [the dbt guide](../dbt/README.md) for the separate uv environment, external
profile, build and source-freshness commands. dbt tests submit BigQuery SQL.
The last verified profile limit is 268435456 bytes billed per query.
It is not a monthly cap, and creating a view does not prepay later view queries.
Review query allowance and dry-run estimates before new warehouse work.

Five views are implemented. Staging, snapshots and hourly availability cover
the current UTC ingestion day. The capture-interval view covers today and
yesterday in UTC for both receipt and ingestion dates. Raw data remains longer.

Hourly averages describe observed samples. Freshness measures recent receipt
across the raw table, with 30-minute warning and 60-minute error thresholds.
Neither proves continuous availability, per-station coverage or fresh Divvy
source events. dbt and its checks are manually executed.

## Retention, cost review and teardown

Configured archive retention is lifecycle deletion under raw/ after 90 days,
plus seven-day soft delete. Lifecycle deletion is asynchronous and soft-deleted
objects still consume storage. The policy is in config/storage-lifecycle.json.
The raw table uses daily UTC ingestion partitions with 30-day expiration and
requires partition filters. Reusing an existing completed load does not restore
rows removed by expiration; expired-data restoration is not a supported workflow.

Budget alerts do not cap spending. Trial credits do not satisfy the free-tier
constraint. Check account-wide usage and current prices before billable work.
Paused collection still leaves storage and image data in place.

For a future teardown, first pause collection and inspect running executions.
Then review deletion of the Scheduler job, Run job, registry images/repository,
analytics/raw/test datasets, archive objects/bucket, dedicated service accounts
and grants. Inspect the test dataset before deleting it. Preserve any required
archives and evidence first. Soft-deleted data may remain billable until expiry.
Do not delete a shared billing account or unrelated project resources.

## Known incident

The October 18 16:45 UTC execution failed during image import with registry
HTTP 401 before Python started. Adjacent executions using the same manifest
succeeded. The authentication cause remains unconfirmed; no code or IAM change
was made. See [the incident report](INCIDENT_2026-10-18_IMAGE_IMPORT.md).

## Cost references

- [Billing budgets](https://docs.cloud.google.com/billing/docs/how-to/budgets)
- [Cloud Run pricing](https://cloud.google.com/run/pricing)
- [Cloud Storage pricing](https://cloud.google.com/storage/pricing)
- [BigQuery pricing](https://cloud.google.com/bigquery/pricing)
- [Cloud Scheduler pricing](https://cloud.google.com/scheduler/pricing)
- [Artifact Registry pricing](https://cloud.google.com/artifact-registry/pricing)
- [Cloud Logging pricing](https://cloud.google.com/products/observability/pricing)
