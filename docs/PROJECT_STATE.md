# DivvyAI project state

Last updated: 2026-10-19 UTC.

## Current phase

The core milestone is implemented and verified: scheduled GCP ingestion and
tested dbt analytics, including hourly sampled availability.

The October 18 collection gap has been investigated. Its immediate cause was
an image-import authentication failure before the collector started. The reason
valid registry credentials were missing or rejected remains unconfirmed.

Further infrastructure, a dashboard and prediction are optional later work.

## Implemented

- Python 3.12 package using uv, Ruff, mypy and pytest.
- Offline synthetic fixtures and a local fixture demo.
- Live collection of station_information and station_status.
- Original response bytes, source URLs and separate UTC receipt times.
- One ZIP per paired capture, containing both feeds and a manifest.
- Stable capture IDs and UTC date/hour archive paths.
- Raw station rows with native JSON metadata and payloads.
- JSONL serialization for BigQuery batch loading.
- Python Cloud Storage and BigQuery adapters and a cloud CLI.
- A Linux amd64 Docker image uploaded to Artifact Registry.
- A Cloud Run Job using an attached service account.
- Manual archive reading, load-job lookup and replay.
- A Cloud Scheduler trigger every 15 minutes.
- Archive lifecycle and BigQuery partition-expiration policies.
- A separate uv project for local dbt development against BigQuery.
- Five dbt views, five synthetic unit tests, one snapshot-quality data test,
  and a separate raw-source receipt-freshness check.

Python tests and Ruff, formatting and mypy checks passed at implementation
checkpoints. Default Python tests do not call live feeds or cloud services.
dbt tests execute SQL on BigQuery, including when their inputs are synthetic.

## Cloud configuration

- Project: divvy-ai; project number: 576327842002.
- gcloud configuration: divvyai.
- Region: us-central1.
- Billing budget with alerts configured; alerts do not cap spending.
- Local user authentication, ADC and dbt profiles remain outside Git.
- No service-account keys are used.

Core resources:

- Bucket: gs://divvy-ai-raw-576327842002.
- Raw dataset/table: divvy-ai.divvyai_raw.station_records.
- Analytics dataset: divvy-ai.divvyai_analytics.
- SDK acceptance dataset: divvy-ai.divvyai_test; current contents not rechecked.
- Artifact Registry repository: divvyai.
- Image: us-central1-docker.pkg.dev/divvy-ai/divvyai/collector:v1.
- Cloud Run Job: divvyai-collector.
- Scheduler job: divvyai-collect-15m.

Configured retention and access controls:

- Bucket uses regional Standard storage, uniform bucket-level access and
  public access prevention.
- Seven-day soft delete; lifecycle deletion under raw/ after 90 days.
- Lifecycle deletion is asynchronous; soft-deleted objects also use storage.
- Lifecycle configuration: config/storage-lifecycle.json.
- Raw BigQuery table has eight required columns and daily ingestion-time
  partitions, a required partition filter and 30-day partition expiration.
- Ingestion partitions represent UTC load days, not exact availability times.
- Artifact Registry scanning is disabled.

Cloud Run settings:

- One task, parallelism one, one CPU and 512 MiB memory.
- Task timeout: 60 seconds; configured task retries: zero.
- The deployed v1 image runs the collector.
- Local recovery code and dbt models are not automatically added to that image.
- Parallelism one does not prevent separate executions from overlapping.

Scheduler settings:

- Schedule: */15 * * * *; time zone: Etc/UTC; reported state: ENABLED.
- Authenticated POST to the Cloud Run Jobs run endpoint, request body {}.
- Launch-request attempt deadline: 30 seconds; configured retries: zero.
- HTTP 200 launch acceptance does not prove that ingestion completed.

## Identities and permissions

Collector: divvyai-collector@divvy-ai.iam.gserviceaccount.com.

- Storage Object Creator on the archive bucket.
- BigQuery Job User on the project.
- BigQuery Data Editor on the raw table only.

Data Editor also permits reading, changing and deleting that table. Loading
with CREATE_NEVER and WRITE_APPEND is an application choice.

Scheduler: divvyai-scheduler@divvy-ai.iam.gserviceaccount.com.

- Cloud Run Invoker on the collector job.

Google-managed Cloud Run service agent:
service-576327842002@serverless-robot-prod.iam.gserviceaccount.com.

- The investigation report shows roles/run.serviceAgent on the project.
- This account participates in image retrieval; the collector account is the
  identity used by the running application for Storage and BigQuery.
- Current IAM is not a complete history of permissions at incident time.

## dbt configuration and model scope

- dbt Core 1.12.6 and dbt-bigquery 1.12.1, pinned in dbt/pyproject.toml.
- Separate dbt/uv.lock and Python environment.
- External profile: ~/.dbt/divvyai/profiles.yml, using local ADC via OAuth.
- Target: divvy-ai.divvyai_analytics in us-central1; one thread.
- Last verified per-query maximum_bytes_billed: 268435456 bytes (256 MiB).
- This guard is per query, not a monthly spending cap or free-tier allowance.

Views:

1. stg_station_status: typed bike/dock counts and capture/receipt identifiers.
2. stg_station_information: station name, coordinates and capacity.
3. fct_station_snapshots: status left-joined to information on capture_id and
   station_id, preserving missing metadata and both receipt timestamps.
4. mart_station_hourly_availability: station/hour sample counts and mean
   observed bike/dock counts.
5. mart_capture_intervals: per-feed intervals between observed captures.

The staging, snapshot and hourly views read the current UTC ingestion day.
They can be empty after UTC midnight until a new capture is loaded.
The interval view filters both ingestion and receipt dates to today and
yesterday in UTC. It measures observed intervals, not every expected cron slot
or an outage after the final observation.

The hourly mart weights each nonnull observation equally. Its sample counts
help describe coverage; its averages are not continuous or time-weighted
availability measurements. Views execute their SQL when queried and incur
the corresponding query scans.

The snapshot data test checks required/nonblank keys, uniqueness of
(capture_id, station_id), status receipt time, and nonnull nonnegative counts.
It does not require bikes plus docks to equal capacity or prove nonempty input.

Source freshness uses MAX(received_at) across the two most recent UTC ingestion
days. It warns after 30 minutes and errors after 60 minutes. It checks recent
receipt across the source, not each feed/station, Divvy's source-event age,
or exact warehouse completion time. dbt execution and checks are manual.

## Verified evidence

### Cloud capture and manual replay

Original execution: divvyai-collector-zg2gw.

Capture:
e2356ba95b41db44a7fb55b64dc5627d7736807b773ecdd3b438cc93670f1c25

BigQuery load job:
divvyai_cloud_raw_v1_cbd0d076a7ebdeb926030031154fe19b177a8fe915257baac44bff0cfe236a75

Load completed at 2026-10-13T06:08:48.972+00:00 with 4,148 records,
2,074 per feed. Capture-scoped SQL found zero payload-ID or archive-reference
mismatches. Two manual replays reused the original completed job, and SQL
counts remained unchanged. This is bounded replay evidence, not exactly-once
delivery or automatic recovery.

On October 15, five displayed scheduled executions completed successfully.
On October 16, five distinct capture_loaded receipts reported 4,148 records
each between 04:01 and 05:01 UTC. These are samples, not an uptime measurement.

### dbt analytics

- All five model-specific builds passed their synthetic unit test and created
  the corresponding BigQuery view.
- Real staging, snapshot, hourly and interval verification queries succeeded.
- assert_station_snapshot_quality passed against live warehouse data.
- dbt source freshness passed at the October 18 checkpoint.
- The interval report exposed an approximately 29.98-minute collection gap
  between 16:31 and 17:01 UTC on October 18.

These results were verified at their individual checkpoints; this documentation
update does not claim a new combined build, cost measurement or test run.

### Investigated collection gap

Execution divvyai-collector-jzlw7 was launched for the October 18 16:45 UTC slot.
Scheduler received HTTP 200, but Cloud Run reported ContainerImageImportFailed:
Artifact Registry returned HTTP 401, "No valid credential was supplied."

The container never started. Adjacent executions successfully used the same
image manifest and loaded 4,148 records each. The next successful execution
was divvyai-collector-4mhvf at the 17:00 UTC slot.

The evidence suggests a temporary authentication failure during image import;
the underlying cause remains unconfirmed. No code or IAM change was justified
by this report. See INCIDENT_2026-10-18_IMAGE_IMPORT.md for the timeline.

## Decisions and limitations

- Core scope is scheduled ingestion and tested dbt analytics. Further checks
  and infrastructure are not prerequisites for documenting this milestone.
- Preserve original bytes before parsing station records and loading BigQuery.
- The feeds are fetched separately, not as an atomic snapshot; both requests
  must succeed before their paired archive is written.
- An archive can exist even if station parsing or warehouse loading fails.
- Re-running collection fetches new observations; it is not historical replay.
- Manual replay verifies the original archive, destination and expected job ID.
- It reuses a verified completed load instead of submitting another load.
- Missing-job recovery accepts captures up to 90 days old. Failed or uncertain
  existing jobs stop for investigation.
- Reusing an existing completed load does not reload its rows, even if those
  rows have expired. Expired-data restoration is not a supported workflow.
- Replay cannot recreate observations never collected.
- Automatic recovery is intentionally outside the current scope.
- Completion receipts are logged; no durable warehouse availability-audit
  table or complete reliability measurement exists.
- Prediction features must obey receipt and warehouse-availability cutoffs.
  The current views are analytics, not a validated point-in-time ML dataset.
- Future labels spanning missing/stale observations must remain unknown.
- Free-tier assumptions depend on account-wide usage. Actual spending has not
  been established by these logs; trial credit is not free-tier usage.

## Not implemented

- Dashboard or forecasting model, features, labels, evaluation or predictions.
- Scheduled dbt execution, checks, notifications or automatic recovery.
- Guaranteed delivery, complete missing-slot detection or measured uptime.
- Terraform, keyless GitHub Actions deployment or a local Airflow DAG.
- A durable warehouse availability audit or validated ML feature dataset.

The project does not demonstrate gap-free delivery, measured uptime, automatic
recovery, continuous availability measurement, forecasting or model performance.
