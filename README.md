# DivvyAI 🚲

[![CI](https://github.com/nicolavivarelli01/DivvyAI-Public/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/nicolavivarelli01/DivvyAI-Public/actions/workflows/ci.yml)
[![Live feed check](https://github.com/nicolavivarelli01/DivvyAI-Public/actions/workflows/live-feeds.yml/badge.svg?branch=main)](https://github.com/nicolavivarelli01/DivvyAI-Public/actions/workflows/live-feeds.yml)

A Python data pipeline for Chicago Divvy bike-share availability on Google
Cloud, with read-only MCP tools and a local tool-calling agent.

Documentation reviewed October 25, 2026 (America/Chicago). Cloud settings below
reflect recorded acceptance checkpoints. The initial agent benchmark and usage
audit below do not establish current cloud configuration or invoice totals.

## Current status

Cloud Scheduler triggers a containerized Cloud Run Job every 15 minutes.
The collector fetches both public Divvy station feeds, archives their original
response bytes and a manifest in one ZIP in Cloud Storage, and batch-loads
station records into an ingestion-partitioned BigQuery table.

Five dbt views organize the records into station status, station information,
station snapshots, hourly sampled availability and per-feed capture intervals.
All five model-specific builds and synthetic unit tests passed on BigQuery.
Python tests run offline.

A separate Cloud Run Job now runs the existing receipt-freshness and
snapshot-quality checks hourly, at 10 minutes past each UTC hour. Cloud
Monitoring watches failed executions of both the collector and the checker.
On October 24, a controlled checker failure produced an email notification;
manual and scheduled checker executions also passed. These are verification
checkpoints, not an uptime measurement or proof of every future notification.

The [demo](docs/DEMO.md) includes a recorded Data Studio chart, PDF and 24-row
CSV for one station on October 21. The report uses a manually uploaded CSV and
does not refresh automatically.

The staging, snapshot and hourly views currently read the current UTC ingestion
day. The interval view uses today and yesterday in UTC. Sample averages describe
observations, not continuous availability or guaranteed collection coverage.

One observed collection gap was traced to a pre-start image-import HTTP 401
failure on October 18. Adjacent executions using the same image succeeded;
the underlying authentication cause remains unconfirmed. See the
[incident report](docs/INCIDENT_2026-10-18_IMAGE_IMPORT.md).

Scheduled ingestion, tested dbt analytics, scheduled checks, manual archive
recovery and a recorded demo are implemented within their documented scope.

An isolated [AI project](ai/README.md) now exposes three read-only MCP tools and
a Strands agent using OpenAI gpt-6-luna. Manual live availability, ranking and
gap questions have returned tool-backed answers. JSON run reports preserve the
actual tool calls, returned observations, answer and SDK-reported token usage.
The initial 20-question benchmark passed 19/20 cases (95%): availability 6/6,
ranking 8/8 and capture gaps 5/6. All 19 completed cases matched independent SQL
for tool arguments, returned data and final answers. One gap case recorded an
API timeout after 60.65 seconds. The result combines six saved availability
runs with 14 new attempts. See [project state](docs/PROJECT_STATE.md) for evidence.

A separate static website lives in DivvyAI-Website. Its model-testing page was
published on Vercel. The local Python bridge in ai/local_web.py and its
explicit dependencies are committed. On October 25, the current AI checkout
passed 160 offline tests, Ruff, formatting and strict mypy. A live local-browser
availability answer matched the recorded SQL values, with one successful tool
call. The website-to-agent cloud connection and a hosted agent service are
not deployed.
The existing concept homepage is intentionally retained as a mock.

Forecasting, automatic recovery and automatic dashboard refresh are not
implemented. The project does not claim guaranteed delivery, complete
missing-execution detection, continuous station availability or prediction
quality.

## Pipeline

```text
Cloud Scheduler (every 15 minutes)
    |
Cloud Run collector <- Divvy GBFS feeds
    |
    +-> Cloud Storage: original responses in one ZIP/capture
    |
    +-> BigQuery: raw station rows
            |
         dbt views -> station snapshots and hourly sampled availability

Recorded demo: bounded historical raw-data CSV export -> manual Data Studio upload

Cloud Scheduler (hourly at :10 UTC)
    |
Cloud Run checker -> dbt receipt-freshness and snapshot-quality checks

Cloud Monitoring -> email when a collector or checker execution fails

Local AI path (separate from scheduled collection):
website model-testing page -> Python /api/agent -> Strands agent
                                                   |       |
                                             OpenAI model  MCP tools -> BigQuery
```

The agent chooses tools for each submitted question; it is not another scheduled
collection job. The current MCP transport runs as a local subprocess. The
planned hosted version places the Python API, agent and tools in a separate
Cloud Run service while the website stays on Vercel and the model stays at
OpenAI. That deployment is future work.

The collector archives original inputs before parsing station records and
loading BigQuery. Manual recovery uses a saved archive and its original
load-job ID. Replay cannot recreate observations that were never collected.
Reusing an existing completed load does not reload its rows, even if they have
expired. Expired-data restoration is not a supported recovery workflow.

All five dbt models are views and reflect matching raw rows when queried.
The hourly checker tests the existing data; it does not rebuild the views.
Model changes and synthetic transformation tests are run explicitly during
development. Prediction remains later work requiring sufficient observed
history, point-in-time feature availability and a measured baseline.

## Local setup

Requirements: Git, uv, and Python 3.12. Run from the repository root:

```bash
uv sync --locked
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
```

The Python tests use synthetic fixtures and mocked HTTP/cloud boundaries;
they do not call live feeds or Google Cloud. dbt tests execute SQL on BigQuery
and are separate from this offline suite. See the [dbt guide](dbt/README.md)
for its separate environment, check runner and cloud configuration.

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

Run the opt-in integration test against the real public feeds:

```bash
DIVVYAI_LIVE_TESTS=1 uv run pytest tests/test_live_feeds.py -v
```

It fetches both feeds, checks that the archive preserves their original bytes,
and prepares warehouse rows. It does not upload to Google Cloud.

uv installs the package and development tools into an ignored local environment
(.venv by default). uv.lock records resolved dependency versions.

## MCP tools and agent

| Tool | What it returns | Window |
|---|---|---|
| get_station_availability | Hourly sampled bikes/docks and sample counts for an exact station ID | Up to seven days, starting within the previous seven UTC days or today |
| find_capture_gaps | Completed receipt intervals at least the requested duration, separately per feed | Yesterday's UTC midnight through query time |
| rank_stations | Observed zero-count rates or lowest sampled availability for a Chicago hour | Up to seven recent completed Chicago dates; three completed dates by default |

Historical availability and ranking read retained raw observations because the
existing staging, snapshot and hourly views cover only the current UTC ingestion
day. Queries are fixed and parameterized. Rates describe observed samples, not
empty duration or future risk. Station-name resolution and forecasting are not
implemented. See the [AI guide](ai/README.md) for the full tool contracts, limits,
local setup, browser integration and evaluation status.

The AI environment is separate from the collector and dbt environments. From the
repository root:

```bash
cd ai
env -u VIRTUAL_ENV uv sync --locked
env -u VIRTUAL_ENV uv run --locked pytest -q
env -u VIRTUAL_ENV uv run --locked ruff check .
env -u VIRTUAL_ENV uv run --locked ruff format --check .
env -u VIRTUAL_ENV uv run --locked mypy *.py tests
env -u VIRTUAL_ENV uv run --locked python agent.py --list-tools
```

These checks and tool discovery do not call OpenAI or query BigQuery. Actual tool
calls need local Google ADC; actual agent questions also need OPENAI_API_KEY in
the terminal environment. API usage is billed separately from a ChatGPT plan.
Keys, credentials and .env files must not be committed or displayed.

For a live question, after those credentials are configured privately:

```bash
env -u VIRTUAL_ENV uv run --locked python agent.py \
  "Were there any capture intervals of at least 30 minutes in the tool's recent UTC window?" \
  --report ../data/evaluation/manual-gap-question-001.json
```

Use a new report filename: the agent refuses to overwrite an existing report
before making a model request. Questions can use up to three model calls and two
admitted tool calls. BigQuery guards remain per query/tool request, not a total
monthly spending limit. Full run and local-browser instructions are in
[ai/README.md](ai/README.md).

## Continuous integration

GitHub Actions runs two workflows:

- **CI**, on every push and pull request: Ruff, mypy and the offline pytest
  suites for the collector and the AI project, the fixture demo, and Docker
  builds of the collector, dbt-checker and agent images.
- **Live feed check**, daily and on demand: the integration test above against
  the real Divvy feeds, to catch upstream URL or schema changes.

Neither workflow has Google Cloud or OpenAI credentials; deployment remains manual.

## Repository layout

```text
.github/workflows/        CI and scheduled live-feed checks
pyproject.toml            package metadata and tool configuration
uv.lock                   dependency lockfile
src/divvyai/              collection, archive, cloud loading, recovery and dbt runner
tests/fixtures/           small synthetic station feed samples
Dockerfile                collector container
Dockerfile.dbt-checks     separate dbt checker container
Dockerfile.agent          hosted agent API container
config/dbt-checks/        cloud dbt connection settings, without credentials
config/monitoring/        disabled alert-policy import template
sql/verification/         bounded historical-capture verification queries
sql/dashboard/            bounded dashboard export query
dbt/                      separate uv project, five views and transformation checks
ai/                       separate uv project, MCP tools, Strands agent and local API
ai/README.md              tool contracts, private credentials, local browser and eval status
docs/DEMO.md              offline demo and recorded hourly availability chart
docs/assets/              saved demo CSV, chart and PDF
docs/PROJECT_STATE.md      verified state, evidence and limitations
docs/RUNBOOK.md            operation, replay, monitoring and teardown guidance
docs/INCIDENT_*.md         investigated failures and evidence limits
```

## Cost, security, and data correctness

- Stay within available GCP free-tier allowances; trial credits do not count.
- Billing budget alerts do not cap spending.
- Document expected cost, permissions, and deletion for each cloud resource.
- Use local user authentication and attached service accounts, without keys in Git.
- The cloud dbt profile contains connection settings only; local credentials
  and the personal dbt profile remain outside Git.
- Offline Python tests and the fixture demo make no live-feed, model or cloud calls.
- Real agent questions use OpenAI API credits and BigQuery; a ChatGPT subscription
  does not cover calls made with this application's API key.
- Agent/MCP queries use 256 MiB per-query limits; historical availability and
  ranking additionally cap a tool request at 1 GiB. The agent can admit two
  tool calls, so a tool-request cap is not an aggregate per-question cap.
- The local web bridge binds loopback, accepts one active question and has a
  100-second response timeout. A timeout does not cancel remote work; the active
  slot stays occupied until the worker finishes. These are local protections,
  not public authentication or a hosted spending cap.
- dbt unit tests use synthetic inputs and execute on BigQuery.
- The checker has query-job and scoped read access, without table-write access.
- Its 256 MiB byte guard applies per query, not per run or month.
- Preserve original bytes and receipt times; log successful warehouse completion.
- Keep source time, receipt time and warehouse availability distinct.
- Use only information available at prediction time when building future ML features.
- Report only implemented capabilities and measured results.

At the October 24 verification, the live failure policy was enabled with a
configured email channel. The checked-in
`config/monitoring/job-failure-alert.json` remains a disabled import template
without a notification channel. Importing that file alone does not reproduce
the live alert configuration; see the [runbook](docs/RUNBOOK.md).

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
