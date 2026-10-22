# DivvyAI 🚲

App for Chicago Divvy bike-share availability,
running on Google Cloud.

## Current status

Cloud Scheduler triggers a containerized Cloud Run Job every 15 minutes.
The collector fetches both public Divvy station feeds, archives their original
response bytes and a manifest in one ZIP in Cloud Storage, and batch-loads
station records into an ingestion-partitioned BigQuery table.

Five dbt views organize the records into station status, station information,
station snapshots, hourly sampled availability and per-feed capture intervals.
All five model-specific builds and synthetic unit tests passed on BigQuery.
A snapshot-quality data test and a separate raw receipt-freshness check also
passed at their verification checkpoints. Python tests run offline.

The staging, snapshot and hourly views currently read the current UTC ingestion
day. The interval view uses today and yesterday in UTC. Sample averages describe
observations, not continuous availability or guaranteed collection coverage.

One observed collection gap was traced to a pre-start image-import HTTP 401
failure on October 18. Adjacent executions using the same image succeeded;
the underlying authentication cause remains unconfirmed. See the
[incident report](docs/INCIDENT_2026-10-18_IMAGE_IMPORT.md).

The core milestone, scheduled GCP ingestion plus tested dbt analytics, is
implemented. Dashboard, prediction, scheduled dbt checks and automatic recovery
remain unimplemented. Manual archive recovery is available within its documented
scope. The project does not claim an uptime percentage or guaranteed delivery.

## Pipeline

```text
Cloud Scheduler -> Cloud Run Job <- Divvy GBFS feeds
                         |
                         +-> Cloud Storage: original responses in one ZIP/capture
                         |
                         +-> BigQuery: raw station rows
                                  |
                             dbt models
                                  |
                         station snapshots -> hourly availability mart
                                  |
                         Looker Studio (stretch goal)
```

The job archives original inputs before parsing station records and loading
BigQuery. Manual recovery uses a saved archive and its original load-job ID.
Replay cannot recreate observations that were never collected. Reusing an
existing completed load does not reload its rows, even if they have expired.
Expired-data restoration is not a supported recovery workflow.

dbt currently runs locally against BigQuery. All five models are views and
reflect matching raw rows when queried. Prediction is later work requiring
sufficient observed history, point-in-time feature availability and a measured
baseline. Looker Studio remains a stretch goal.

## Local setup

Requirements: Git, uv, and Python 3.12. Run from the repository root:

```bash
uv sync --locked
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
```

All checks should pass. The Python tests use synthetic fixtures and mocked
HTTP/cloud boundaries; they do not call live feeds or Google Cloud.
dbt tests execute SQL on BigQuery and are separate from this offline suite.
See the [dbt guide](dbt/README.md) for its environment and verification commands.

Run the offline demo:

```bash
uv run python -m divvyai.local
```

Expect four raw rows, a ZIP under data/demo/raw/, and a JSONL file under
data/demo/warehouse/.

To fetch current Divvy data explicitly:

```bash
uv run python -m divvyai.local --live
```

This makes two public HTTP requests and saves outputs under data/live/.
The row count depends on the published feeds. It does not upload to Google Cloud.

Both modes print receipt times, the row count, and output paths.
Generated data is ignored by Git.

uv installs the package and development tools into an ignored local environment
(.venv by default). uv.lock records resolved dependency versions.

## Repository layout

```text
pyproject.toml            package metadata and tool configuration
uv.lock                   dependency lockfile
src/divvyai/              collection, archive, cloud loading, and manual recovery
tests/fixtures/           small synthetic station feed samples
sql/verification/         bounded historical-capture verification queries
dbt/                      separate uv project, five views, and transformation checks
docs/PROJECT_STATE.md      verified state, evidence, and limitations
docs/RUNBOOK.md            operation, replay, retention, and teardown guidance
docs/INCIDENT_*.md         investigated failures and evidence limits
docs/DEMO.md               short walkthrough of the pipeline
```

## Cost, security, and data correctness

- Stay within available GCP free-tier allowances; trial credits do not count.
- First cloud configuration: billing budget with email alerts.
- Document expected cost, permissions, and deletion for each cloud resource.
- Use local user authentication and attached service accounts, without keys in Git.
- Python tests and the local fixture demo make no live-feed or cloud calls.
- dbt unit tests use synthetic inputs and execute on BigQuery.
- Preserve original bytes and receipt times; log successful warehouse completion.
- Keep source time, receipt time and warehouse availability distinct.
- Use only information available at prediction time when building future ML features.
- Report only implemented capabilities and measured results.

See [project state](docs/PROJECT_STATE.md) and the
[demo walkthrough](docs/DEMO.md) for details.

## License

Copyright (c) 2026 Nick Vivarelli. All rights reserved.

This repository is public for viewing only. No permission is granted to copy,
modify, distribute or reuse the code. See [LICENSE](LICENSE).

## References

- [Divvy system data](https://divvybikes.com/system-data)
- [Divvy GBFS discovery](https://gbfs.divvybikes.com/gbfs/gbfs.json)
- [GBFS specification](https://gbfs.org/documentation/reference/)
- [GCP free-tier documentation](https://docs.cloud.google.com/free/docs/free-cloud-features)
- [Cloud Run pricing](https://cloud.google.com/run/pricing)
- [Cloud Storage pricing](https://cloud.google.com/storage/pricing)
- [BigQuery pricing](https://cloud.google.com/bigquery/pricing)
- [uv project documentation](https://docs.astral.sh/uv/concepts/projects/)
