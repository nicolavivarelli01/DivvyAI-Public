# dbt workspace

Documentation reviewed October 25, 2026 (America/Chicago). Cloud configuration
and acceptance results are recorded checkpoints, not a new live deployment
inspection or a fresh BigQuery test run.

dbt Core submits SQL to BigQuery. Local development and a separate scheduled
Cloud Run checker use the same pinned dbt project. The collector runs separately
and does not execute dbt.

All five analytics models are views, so new matching raw rows are reflected when
the views are queried. The scheduled checker runs existing freshness and data
tests; it does not rebuild the views or run the synthetic unit tests hourly.

## Environment and connection

- Python 3.12, dbt Core 1.12.6 and dbt-bigquery 1.12.1.
- Dependencies and lockfile: dbt/pyproject.toml and dbt/uv.lock.
- Separate environment because the dbt adapter and ingestion project have
  different dependency requirements.
- Local profile: ~/.dbt/divvyai/profiles.yml, profile name divvyai, using local ADC.
- Cloud profile: config/dbt-checks/profiles.yml, profile name divvyai and target
  checks, using the attached Cloud Run service account through ADC.
- The cloud profile contains connection settings only and can be committed.
  Local credentials and the personal dbt profile remain outside Git.
- Target: divvy-ai.divvyai_analytics, location us-central1, one thread.
- The cloud profile sets maximum_bytes_billed to 268435456 bytes per query,
  zero job retries and a 60-second query execution timeout.

From the repository root, install or check local tooling:

```bash
env -u VIRTUAL_ENV uv sync --locked --project dbt
env -u VIRTUAL_ENV uv run --locked --project dbt dbt --version
```

Clearing VIRTUAL_ENV for these commands avoids inheriting the root project's
active environment. The dbt environment and lockfile remain separate.

## Models and row grain

| View | One row represents |
|---|---|
| stg_station_status | A station-status record in a capture |
| stg_station_information | A station-information record in a capture |
| fct_station_snapshots | A status record joined to information from the same capture and station |
| mart_station_hourly_availability | Observed samples for one station and UTC hour |
| mart_capture_intervals | One feed in one capture, with its preceding observed receipt time |

Staging, snapshot and hourly views read the current UTC ingestion day.
They can be empty after UTC midnight until a new capture is loaded.
The interval view reads today and yesterday in UTC for both ingestion and
receipt dates. Raw retention is longer than these view windows.

The hourly mart reports equally weighted observed counts and sample counts.
It does not measure continuous or time-weighted availability. Missing counts
are not converted to zero. The snapshot join preserves absent information.

## How the AI tools use this data

The separate ai/ project adds read-only access without changing the collector
or these dbt models. It has its own Python environment and lockfile.

- get_station_availability uses mart_station_hourly_availability for requests
  starting today in UTC. Requests starting earlier use retained raw status
  history because this view only covers the current UTC ingestion day.
- find_capture_gaps queries mart_capture_intervals and inherits its window:
  yesterday's UTC midnight through query time. It is not arbitrary historical
  outage search or a rolling 48-hour check.
- rank_stations reads retained raw status/information from the same capture
  for recent completed Chicago dates and the requested local hour.

These tools do not extend the dbt view windows, schedule dbt builds, train a
model or modify warehouse data. Later uploads visible now can affect their
historical answers, so these reads are not a validated point-in-time ML dataset.
Starting the agent or local website server does not deploy or replace the dbt
checker. See the [AI guide](../ai/README.md) for commands and data limits, and
[project state](../docs/PROJECT_STATE.md) for the current integration milestone.

## Checks run automatically

The runner at src/divvyai/dbt_checks.py runs two fixed commands:

1. Source freshness for source:divvy.station_records. It checks MAX(received_at)
   over today and yesterday's UTC ingestion partitions. It warns after 30
   minutes and errors after 60 minutes.
2. The singular data test assert_station_snapshot_quality. It checks required
   and nonblank capture/station keys, one row per capture/station, status receipt
   time, and nonnull nonnegative bike/dock counts.

Both checks are attempted even if one fails. The runner streams dbt output and
prints a JSON summary. It returns a nonzero process result if either command
fails, cannot start or exceeds the 120-second per-command timeout. Empty
selections are treated as errors rather than successful checks.

A freshness warning alone does not fail the job. Source freshness checks recent
receipt across the raw table, not per-feed/station coverage, Divvy source-event
age or exact warehouse completion time. The snapshot-quality test can pass on
empty input and does not require bikes plus docks to equal station capacity.
The interval view reports observed gaps, not every missing schedule slot.

Run the same pair locally from the repository root:

```bash
env -u VIRTUAL_ENV uv run --locked --project dbt \
  python src/divvyai/dbt_checks.py \
  --project-dir dbt \
  --profiles-dir "$HOME/.dbt/divvyai"
```

This submits queries to BigQuery. A successful final summary has
`event: dbt_checks_completed`, `status: passed` and an empty failed_checks list.
The per-query byte guard is not a limit on the entire command or month.

## Scheduled cloud execution and alerts

The checker has its own image and job, separate from the collector:

- Dockerfile: Dockerfile.dbt-checks, with its own Docker build-context allowlist.
- Image: us-central1-docker.pkg.dev/divvy-ai/divvyai/dbt-checks:v1.
- Cloud Run Job: divvyai-dbt-checks in us-central1.
- Runtime identity: divvyai-dbt-checks@divvy-ai.iam.gserviceaccount.com.
- One task, parallelism one, one CPU, 512 MiB memory, a 300-second task timeout
  and zero task retries.
- Scheduler: divvyai-dbt-checks-hourly, cron `10 * * * *`, time zone Etc/UTC.
- The existing scheduler identity can invoke this checker job. It does not
  receive the checker's BigQuery access.

The checker identity has BigQuery Job User on the project, Data Viewer on the
raw station_records table and Data Viewer on the analytics dataset. It starts
query jobs and reads the existing table/views. It has no table-write grant for
these checks, and no service-account key is used.

Container dependencies are installed from dbt/uv.lock during the image build.
The image includes the runner, model and test definitions, and the credential-free
cloud profile. Generated dbt logs and target files use temporary container paths.
Pushing a changed image does not update an existing Cloud Run Job by itself.

Cloud Monitoring policy 4698528212121560473 was verified enabled on October 24
with an email notification channel. It watches failed completed executions of
divvyai-collector and divvyai-dbt-checks using
run.googleapis.com/job/completed_execution_count. It sums the failure count
over five minutes and triggers above zero, without a retest delay. Notifications
are configured for alert opening, without repeat reminders.

The checked-in config/monitoring/job-failure-alert.json is a disabled import
template without notificationChannels. The live policy was enabled and connected
to its email channel separately. Reapplying the template alone does not recreate
the live configuration.

Metric visibility, alert evaluation and email delivery introduce delay; a
notification is not immediate. The policy detects failed executions, not a
scheduler that silently stops launching jobs. The hourly freshness check can
take nearly two hours to fail after silent ingestion stoppage because its
error threshold is 60 minutes. If both scheduled jobs stop running, this setup
does not provide a separate heartbeat alert.

See the [runbook](../docs/RUNBOOK.md) for deployment, execution, log inspection,
alert configuration and teardown guidance.

## Explicit development checks

These commands submit work to BigQuery. Synthetic dbt unit tests also execute
there; they are separate from the root project's offline pytest suite.
Review available query allowance and dry-run estimates before cloud execution.

Check the connection:

```bash
env -u VIRTUAL_ENV uv run --locked --project dbt dbt debug \
  --project-dir dbt --profiles-dir "$HOME/.dbt/divvyai"
```

Build models and run their unit/data tests after model changes:

```bash
env -u VIRTUAL_ENV uv run --locked --project dbt dbt build \
  --project-dir dbt --profiles-dir "$HOME/.dbt/divvyai"
```

Check source receipt freshness separately:

```bash
env -u VIRTUAL_ENV uv run --locked --project dbt dbt source freshness \
  --select source:divvy.station_records \
  --project-dir dbt --profiles-dir "$HOME/.dbt/divvyai"
```

## Verified evidence

All five model-specific builds and unit tests, the snapshot data test, source
freshness and bounded real-data verification queries passed at their respective
implementation checkpoints.

On October 24, six offline runner tests passed. Two manual Cloud Run checker
executions passed, followed by a successful automatic hourly execution and a
successful manually requested Scheduler launch. A separate deliberately failing
checker execution used a nonexistent profile directory and exited with code 2.
Its failure opened an alert and an email arrived at the configured Outlook
address around 11:18 UTC.

That drill verified notification for a failed checker execution. It did not
introduce bad warehouse data, trigger a real stale-data error or establish
notification latency guarantees. It also did not change the normal job's saved
arguments.

These are recorded checkpoints, not a new combined test run or a guarantee of
permanently fresh data. Generated target/, logs/, dbt_packages/ and environment
directories are ignored. They are build outputs and dependencies, not copies
of the source-feed archive.
