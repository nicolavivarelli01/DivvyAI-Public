# dbt workspace

dbt Core runs locally and submits SQL to BigQuery. The collector runs separately
on Cloud Run; it does not execute dbt. All five analytics models are views, so
new matching raw rows are reflected when the views are queried.

## Environment and connection

- Python 3.12, dbt Core 1.12.6 and dbt-bigquery 1.12.1.
- Dependencies and lockfile: dbt/pyproject.toml and dbt/uv.lock.
- Separate environment because the dbt adapter and ingestion project have
  different dependency requirements.
- External profile: ~/.dbt/divvyai/profiles.yml, profile name divvyai.
- OAuth using local ADC; no credentials or profiles in Git.
- Target: divvy-ai.divvyai_analytics, location us-central1, one thread.
- Last verified profile maximum_bytes_billed: 268435456 bytes per query.

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
The interval view reads today and yesterday in UTC for both ingestion and
receipt dates. Raw retention is longer than these view windows.

The hourly mart reports equally weighted observed counts and sample counts.
It does not measure continuous or time-weighted availability. Missing counts
are not converted to zero. The snapshot join preserves absent information.

## Verification commands

These commands submit work to BigQuery. Synthetic dbt unit tests also execute
there; they are separate from the root project's offline pytest suite.
Review available query allowance and dry-run estimates before cloud execution.
The profile's byte guard applies to each query, not the whole command or month.

Check the connection:

```bash
env -u VIRTUAL_ENV uv run --locked --project dbt dbt debug \
  --project-dir dbt --profiles-dir "$HOME/.dbt/divvyai"
```

Build models and run their unit/data tests:

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

All five model-specific builds and unit tests, the snapshot data test, source
freshness and bounded real-data verification queries passed at their respective
checkpoints. This does not claim a new combined build or a permanently fresh
source. These checks are manually run, without scheduled notifications.

Freshness uses MAX(received_at) over today and yesterday's UTC ingestion
partitions: warn after 30 minutes, error after 60. It measures recent receipt
across the raw table, not per-feed/station coverage or Divvy source-event age.
The interval report measures observed gaps, not every missing schedule slot.

Generated target/, logs/, dbt_packages/ and environment directories are ignored.
They are build outputs and dependencies, not copies of the source-feed archive.
