# DivvyAI 🚲

[![CI](https://github.com/nicolavivarelli01/DivvyAI-Public/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/nicolavivarelli01/DivvyAI-Public/actions/workflows/ci.yml)
[![Live feed check](https://github.com/nicolavivarelli01/DivvyAI-Public/actions/workflows/live-feeds.yml/badge.svg?branch=main)](https://github.com/nicolavivarelli01/DivvyAI-Public/actions/workflows/live-feeds.yml)

DivvyAI collects Chicago Divvy bike-share availability every 15 minutes on
Google Cloud, models it with dbt in BigQuery, and lets you ask questions about
it in plain English through an AI agent.

Ask *"Which downtown stations ran out of bikes most often around 8 a.m. on
weekdays?"* or *"Were there any gaps in data collection yesterday?"*. The agent
picks one of three read-only data tools, queries BigQuery, and answers from the
returned numbers, showing which tool it called and with what arguments.

The agent is deployed on Cloud Run and is reachable from the project website,
[divvyai.bike](https://divvyai.bike). Asking questions
there is invite-only.

## How it works

```text
            ┌──────────────────────── Collect ────────────────────────┐
Divvy GBFS  │  Cloud Scheduler (every 15 min) -> Cloud Run Job         │
  feeds ───►│     ├─► Cloud Storage: original responses, one ZIP each  │
            │     └─► BigQuery: raw station rows (batch load)          │
            └──────────────────────────┬──────────────────────────────┘
                                       │
            ┌──────────────────────── Model ──────────────────────────┐
            │  dbt views: station snapshots, hourly availability,      │
            │  collection intervals                                    │
            │  Hourly Cloud Run checker: freshness + data quality      │
            │  Cloud Monitoring: email when a job fails                │
            └──────────────────────────┬──────────────────────────────┘
                                       │
            ┌───────────────────────── Ask ───────────────────────────┐
Website ───►│  Vercel function (login + allow list)                    │
(Firebase   │     └─► Cloud Run service (IAM-protected)                │
 login)     │           └─► Strands agent ──► OpenAI model             │
            │                  └─► MCP server: 3 read-only tools ──► BigQuery
            └─────────────────────────────────────────────────────────┘
```

### 1. Collect

A Cloud Run Job runs every 15 minutes. It fetches Divvy's two public GBFS feeds,
`station_information` and `station_status`. It saves the original response bytes
and a manifest as one ZIP per capture in Cloud Storage, then batch-loads one row
per station into an ingestion-partitioned BigQuery table.

- The original bytes are archived before anything is parsed, so every row can be
  traced back to the exact response it came from.
- Captures have stable IDs, so a saved archive can be replayed manually without
  adding duplicate rows.
- Each row keeps the source URL and its UTC receipt time. Source time, receipt
  time and warehouse availability are kept distinct.

### 2. Model

Five dbt views turn raw JSON into typed staging tables, station snapshots,
hourly sampled availability and per-feed collection intervals. Each view has a
synthetic unit test.

A second Cloud Run Job runs every hour. It checks that new data is still
arriving (receipt freshness) and that snapshots are well formed (keys,
uniqueness, non-negative counts). Cloud Monitoring emails an alert when either
job fails. That alert was tested end to end with a controlled failure.

### 3. Ask

The [`ai/`](ai/README.md) project contains an MCP server and a tool-calling
agent built with [Strands Agents](https://strandsagents.com/).

| MCP tool | What it answers |
|---|---|
| `get_station_availability` | Hourly average bikes and docks, with sample counts, for one station over up to seven days |
| `find_capture_gaps` | Recent intervals where data collection paused longer than a threshold |
| `rank_stations` | Stations most often out of bikes or docks, or with the lowest averages, at a given Chicago hour over recent days |

- The tools run fixed, parameterized SQL. The model chooses a tool and its
  arguments, but never writes SQL.
- Each question is limited to three model calls and two tool calls. Every
  BigQuery query has a 256 MiB billing cap, and a historical or ranking tool
  call is capped at 1 GiB.
- A run can be saved as a JSON report with the actual tool calls, returned
  data, final answer and token usage.

The agent is hosted as a private Cloud Run service. It requires IAM
authentication, scales to zero and runs at most one instance. The OpenAI key
lives in Secret Manager.

The website lives in a separate repository. It calls the agent through a
server-side Vercel function. That function first checks the visitor's Firebase
login and their entry on a Firestore allow list. It then exchanges Vercel's
OIDC token for a Google ID token through Workload Identity Federation. No
service-account keys exist anywhere in the system.

## Results

- **Agent benchmark: 19/20 (95%).** On a fixed set of 20 questions
  (availability 6/6, ranking 8/8, collection gaps 5/6), every completed answer
  used the expected tool and arguments. Its data and final answer matched an
  independent SQL reference. The one failure was an OpenAI API timeout; it was
  scored as a failure, not retried.
- **Hosted path verified.**
  - An authenticated question to the Cloud Run service matched its SQL
    reference.
  - Unauthenticated requests were rejected.
  - A question asked from a phone was answered with the development machine off.
- **Access control verified.** The Firestore rules passed seven rule tests. An
  allow-listed account got an answer, and an account not on the list was
  refused.
- **Prompt-injection evaluation: 59/60 attempts (98%).** The real agent and
  model ran against a synthetic MCP server whose tool results hide attacks,
  plus attacks in user messages: 20 cases, three runs each, against a fixed
  80% threshold. 19 of the 20 cases passed every run. The one failure repeated
  system-prompt sentences when asked to print its instructions in a code block.
  Scoring is plain code; no model grades the answers.
- **Replay verified.** Replaying a completed capture reused the original load
  job and added no rows.
- **One real incident investigated.** A missed collection on October 18 was
  traced to a container-image pull failure before the job started. See the
  [incident report](docs/INCIDENT_2026-10-18_IMAGE_IMPORT.md).

The evidence and its limits are recorded in
[docs/PROJECT_STATE.md](docs/PROJECT_STATE.md).

## Run it locally

Requirements: Git, [uv](https://docs.astral.sh/uv/) and Python 3.12. None of
the commands below need cloud credentials or an API key.

**Collector**, from the repository root:

```bash
uv sync --locked
uv run pytest
uv run python -m divvyai.local
```

The demo uses synthetic fixtures and makes no network requests. It writes a ZIP
archive under `data/demo/raw/` and a JSONL load file under `data/demo/warehouse/`.
Add `--live` to fetch the real Divvy feeds instead; nothing is uploaded.

**AI project**, which has its own environment:

```bash
cd ai
uv sync --locked
uv run --locked pytest -q
uv run --locked python agent.py --list-tools
```

The tests use synthetic model responses and never call OpenAI or BigQuery.
Asking the agent real questions needs Google credentials and an OpenAI API key;
see the [AI guide](ai/README.md).

**Live-feed integration test**, opt-in:

```bash
DIVVYAI_LIVE_TESTS=1 uv run pytest tests/test_live_feeds.py -v
```

This fetches both real feeds and checks that archiving preserves their original
bytes and that warehouse rows can be built from them.

dbt runs against BigQuery in its own environment; see the [dbt guide](dbt/README.md).

## Continuous integration

GitHub Actions has three workflows. Deployment stays manual, and this repository
holds no cloud or OpenAI credentials.

- **CI**, on every push and pull request:
  - Ruff, mypy and offline pytest for the collector and the AI project;
  - the fixture demo;
  - Docker builds and smoke tests of the collector, dbt-checker and agent images.
- **Live feed check**, daily and on demand: the integration test against the
  real Divvy feeds, to catch upstream URL or schema changes.
- **Prompt-injection eval**, manual-only here: it makes paid OpenAI calls and
  needs an API key secret, which is not configured in this repository. The
  results above come from the private development repository.

## Repository layout

```text
src/divvyai/          collector: fetch, archive, BigQuery load, replay, dbt check runner
tests/                offline collector tests with synthetic fixtures
ai/                   MCP server, Strands agent, hosted API and evaluation tools
dbt/                  dbt project: five views, unit tests and data checks
sql/                  verification and dashboard export queries
config/               storage lifecycle, dbt checker profile, alert-policy template
Dockerfile            collector image
Dockerfile.dbt-checks hourly checker image
Dockerfile.agent      hosted agent image
docs/                 project state, runbook, demo walkthrough and incident report
.github/workflows/    CI and the daily live-feed check
```

## Design choices

- **Cost.** The project is designed to stay within Google Cloud free-tier
  allowances. It uses batch loads instead of streaming inserts, byte caps on
  every query, scale-to-zero services and no always-on infrastructure. A
  billing budget sends alerts but does not cap spending.
- **Security.**
  - Each job and the agent run as their own least-privilege service account.
  - The agent's data tools are read-only.
  - The OpenAI key is never passed to the process that talks to Google.
  - No credentials or keys are in this repository; the tracked dbt profile
    holds connection settings only.
- **Honest numbers.** Hourly figures are averages of 15-minute samples, not
  continuous measurements, and the tools return sample counts with them. Missing
  or invalid samples are excluded rather than counted as zero.

## Limitations

- Forecasting is not implemented yet.
- Collection runs on a schedule, but gap-free delivery is not guaranteed, and
  uptime has not been measured.
- The hosted agent has no per-person daily question limit or spending cap yet.
  Access is invite-only.
- The 19/20 benchmark covers 20 structured questions. It does not measure
  accuracy across arbitrary questions.
- The injection evaluation is a small, fixed set of cases with synthetic tool
  data. It does not establish general injection resistance, and the agent can
  still be made to repeat its system prompt, which holds rules, not secrets.
- The dbt views read a recent window, the current UTC day for most of them.
  The agent's historical tools read retained raw data instead.

## Documentation

- [Project state](docs/PROJECT_STATE.md): what is implemented, configuration and evidence
- [AI guide](ai/README.md): tool contracts, agent setup, reports and evaluation
- [dbt guide](dbt/README.md): models, checks and scheduled execution
- [Runbook](docs/RUNBOOK.md): operations, recovery, alerts and teardown
- [Demo walkthrough](docs/DEMO.md): a guided tour with recorded results
- [Incident report](docs/INCIDENT_2026-10-18_IMAGE_IMPORT.md): the October 18 missed collection

## Built with

Python 3.12 · uv · pytest · Ruff · mypy · Google Cloud (Cloud Run, Cloud
Scheduler, Cloud Storage, BigQuery, Cloud Monitoring, Secret Manager, Artifact
Registry) · dbt · MCP · Strands Agents · OpenAI · Docker · GitHub Actions ·
Vercel · Firebase

## License

Copyright (c) 2026 Nick Vivarelli. All rights reserved.

This repository is public for viewing only. No permission is granted to copy,
modify, distribute or reuse the code. See [LICENSE](LICENSE).
