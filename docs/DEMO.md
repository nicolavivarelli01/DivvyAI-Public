# DivvyAI: a short demonstration

Documentation reviewed October 25, 2026 (America/Chicago). Recorded results
below retain their original dates. Live examples make real model or warehouse
requests only when explicitly run.

## What the project does

Every 15 minutes, Cloud Scheduler launches a containerized Python collector
on Cloud Run Jobs. It fetches Chicago Divvy station information and status,
archives the original responses in Cloud Storage, and batch-loads station
records into BigQuery.

dbt views organize those records into station snapshots, hourly sampled
availability and collection intervals.

A separate Cloud Run checker runs the existing receipt-freshness and snapshot
quality checks at minute 10 of every UTC hour. Cloud Monitoring watches failed
collector or checker executions; email delivery was verified on October 24.

A separate local MCP server offers three read-only data tools. A Strands agent
uses OpenAI gpt-6-luna to choose a tool and explain the returned observations.
The initial 20-question benchmark passed 19/20 cases, with one API timeout.
The website's model-testing page uses the local Python API. A live browser
availability answer matched recorded SQL values; the agent is not hosted yet.

## 1. Demonstrate collection locally

From the repository root:

```bash
uv run python -m divvyai.local
```

This uses synthetic fixtures and makes no network requests.

Expected output includes:

- Mode: fixture (synthetic data).
- Four raw records: two stations across two feeds.
- A ZIP archive under data/demo/raw/.
- A JSONL load file under data/demo/warehouse/.

The ZIP preserves original inputs for inspection and replay.
The JSONL represents individual station records for warehouse loading.

## 2. Explain the deployed cloud path

```text
Cloud Scheduler
      |
Cloud Run Job -> Divvy feeds
      |
      +-> Cloud Storage: original response archive
      |
      +-> BigQuery: raw station records
                        |
                     dbt views
                        |
              station snapshots and hourly averages

Hourly Scheduler -> checker container -> queries existing BigQuery data
                             |
                      job pass/fail result

Collector or checker failure -> Cloud Monitoring -> email
```

Recorded evidence from October 18, 2026, UTC:

Execution divvyai-collector-4mhvf completed a BigQuery load at 17:02:00.340.
Its completion receipt reported 4,148 raw records.

Those are records across two feeds, not 4,148 unique stations.
This is recorded deployment evidence, not a live result from the offline demo.

## 3. Explain the analytics

The staging views extract typed fields from raw JSON.
The snapshot view joins station information and status from the same capture.
The hourly mart groups observed counts by station and UTC hour.

One recorded October 18 result for station
03a1610c-3ba0-4b85-b512-bc26791cb7ee at 22:00 UTC contained:

- Three snapshots.
- Mean bikes available: 4.
- Mean docks available: 10.

These are averages of collected observations, not continuous measurements.

## 4. Show the recorded availability chart

![Hourly average bikes and available docks for one Divvy station on October 21, 2026, UTC](assets/hourly_station_availability_2026-10-21.png)

[Open the PDF](assets/hourly_station_availability_2026-10-21.pdf) or
[inspect the 24-row CSV](assets/hourly_station_availability_2026-10-21.csv).
These saved artifacts can be viewed without cloud access or new queries.

This Data Studio report shows one station on October 21, 2026, UTC:

- Station ID: a3ad8072-a135-11e9-9cda-0a87ae2ba916.
- 24 hourly rows, from 00:00 through 23:00 UTC.
- Four station-status observations per hour, for 96 observations in total.
- Mean bikes available and mean docks available for each hour.

For example, at 00:00 UTC the hourly averages were 1.25 bikes and 43.5 docks.
At 16:00 UTC they were 43.75 bikes and 1.25 docks. Fractional values are
averages of the four observations, not fractional physical bikes or docks.

The station was selected because its hourly bike averages varied. It is an
illustration of one station, not a summary of the entire Divvy network.
The CSV export groups the saved raw station-status records by UTC hour using
the same hourly averaging calculation described above. It was uploaded to
Data Studio manually; the report is not directly connected to the dbt views
and does not refresh automatically.

The lines connect observed hourly averages. They do not establish continuous
availability between samples or count trips, and they are not predictions.

The current sql/dashboard/station_hourly_availability.sql targets a different
station in the current-day mart; it does not reproduce this historical export.
Use the saved CSV/PDF/PNG for this recorded demonstration.

## 5. Explain the scheduled checks and recorded failure email

The collector brings data in. The checker reads the stored data and asks:

- Was raw data received recently? Freshness warns after 30 minutes and fails
  after 60; a warning alone does not fail the job.
- Do today's snapshots contain missing required values, duplicate keys or
  missing/negative bike and dock counts?

These are real-data checks on BigQuery. The scheduled job does not redeploy
views or run the complete Python/dbt development test suites.

Recorded evidence from October 24, 2026, UTC:

- divvyai-dbt-checks-fzdqp was created at the 11:10:00 scheduled slot by the
  scheduler account and succeeded, 1 / 1 complete.
- divvyai-dbt-checks-lmx2l was created at 11:10:24 with an intentional missing
  profiles directory. It failed with exit code 2 before data checks.
- divvyai-dbt-checks-fw79b was created at 11:13:24 after a manual Scheduler
  trigger and succeeded, 1 / 1 complete.
- The configured Outlook inbox received the failure email at 11:18 UTC. It
  identified the checker, result failed, and count 1 above the threshold of 0.

A successful checker completion receipt has this form:

```json
{
  "event": "dbt_checks_completed",
  "status": "passed",
  "failed_checks": []
}
```

The deliberate failure was an alert-delivery test, not a collection outage or
bad warehouse data. Its execution-only arguments did not alter the job defaults.
The email arrived after monitoring and delivery latency; reading the policy and
execution list did not cause it to be sent. This section presents recorded
evidence and does not launch a new failure test. See [the runbook](RUNBOOK.md)
for explicit live operations.

## 6. Demonstrate the MCP tools and agent

MCP gives an AI client a list of callable tools and their input/output formats.
The model decides which tool fits the question. Strands runs the requested
function through MCP, gives its result back to the model and returns an answer.
The tools run fixed parameterized SQL; the model does not write arbitrary SQL.

From the AI project, discover the three tools without a model or data query:

```bash
cd ai
env -u VIRTUAL_ENV uv sync --locked
env -u VIRTUAL_ENV uv run --locked python agent.py --list-tools
```

Expected: get_station_availability, find_capture_gaps and rank_stations.
The current AI checkout passed 160 offline tests, Ruff, formatting and strict
mypy for 24 Python files on October 25.

After privately configuring Google ADC and OPENAI_API_KEY in the same terminal,
this explicit live example asks for the gap tool's supported recent window:

```bash
env -u VIRTUAL_ENV uv run --locked python agent.py \
  "Find capture intervals of at least 30 minutes in the tool's supported recent window. State the exact window and do not count the two feeds as separate failed jobs."
```

The answer depends on current observations. Its evidence should show
find_capture_gaps and its actual time window: yesterday's UTC midnight through
query time. This does not check a rolling 48 hours or detect an ongoing outage
after the final receipt. Real agent questions consume OpenAI API usage and
BigQuery scans. See the [AI guide](../ai/README.md) for setup, estimates and limits.

Recorded agent evidence from October 25:

- A ranking question for October 23, Chicago hour 8, weekdays and downtown_demo
  selected rank_stations with the correct arguments. Its five returned stations
  matched the previously checked tool output: four zero-bike samples out of four
  valid samples per station, from one sampled day. The area is an illustrative
  rectangle, not an official downtown boundary.
- An availability question for station a3ad8072-a135-11e9-9cda-0a87ae2ba916,
  October 21 00:00 UTC inclusive to 01:00 UTC exclusive, selected
  get_station_availability. It returned 1.25 average bikes, 43.5 average docks
  and four valid samples for each, matching the first hour of the saved CSV.
- The availability run saved an actual JSON report in the ignored
  data/evaluation/ directory. Reports retain tool arguments, returned data,
  answers, limits and reported token usage. They are not automatic grades.

These manual examples demonstrate tool selection and data-backed answers.
The initial 20-question benchmark passed 19/20 cases (95%): six availability,
eight ranking and five of six gap questions. All 19 completed cases matched
independent SQL and expected tool arguments. The 45-minute gap question recorded
APITimeoutError after 60.65 seconds, with no completed answer saved.

The benchmark reuses six earlier availability reports and adds 14 new attempts.
Availability references and reports are in data/evaluation/availability-reference-v1.json
and availability-run-v1/. The remaining reports, independent ranking/gap references
and summary.json are in data/evaluation/benchmark-run-v1/. This is a fixed
structured-JSON benchmark; the original 95% includes its timed-out attempt.

## 7. Try the existing website locally

The separate DivvyAI-Website repository contains model-testing.html. Its
homepage remains an intentionally illustrative product concept. The new page
submits actual questions to POST /api/agent and shows the returned tool evidence;
it does not replace failed API requests with canned answers.

With the AI environment synced and the key exported in that terminal:

```bash
env -u VIRTUAL_ENV uv run --locked python local_web.py \
  --website-dir /path/to/DivvyAI-Website
```

Open http://127.0.0.1:8000/model-testing.html. The single Python process serves
both the existing page and its API. Opening the page or /health makes no model
or warehouse request; submitting a question does. The API accepts one active
question and a response timeout does not cancel already running remote work.

The recorded browser acceptance used station
a3ad8072-a135-11e9-9cda-0a87ae2ba916 from October 23 00:00 UTC inclusive to
01:00 UTC exclusive. The supplied screenshot showed a successful
get_station_availability call and an answer with 0.5 average bikes, 41.5 average
docks and four samples for each, matching the SQL reference. Reported run time
was 12.8 seconds, tool duration 6.99 seconds and reported usage 3,179 tokens;
neither call limit was reached. The tool card was collapsed in that screenshot.

For a later demonstration, choose a supported recent date or the undated gap
question. Fixed dates eventually leave the tool's recent-history window.

The local server and its explicit dependencies are committed and its offline
checks pass. The live local-browser availability check passed. The static
website was published on Vercel, but its hosted API connection and the
Python Cloud Run service are not deployed. For this local path the Mac must
remain running. The OpenAI model runs at OpenAI and the data remains in BigQuery.

## Current limits

The recorded chart uses manual CSV refresh. Automatic dashboard refresh and
prediction are not implemented.
Collection is scheduled, but gap-free delivery is not guaranteed.
Hourly health checks and failure email are implemented. They do not detect every
missing scheduled launch or prove fresh source reports for each station.
Three read-only MCP tools, the local Strands agent and JSON reports are
implemented. The initial 20-question evaluation is complete at 19/20 passed,
including one API timeout. Forecasting and a hosted agent service are unfinished.
The recorded local-browser availability check passed for one question.

One missed collection was investigated and traced to container-image
authentication failure before Python started. The underlying authentication
cause remains unconfirmed.

See [project state](PROJECT_STATE.md) and the
[incident report](INCIDENT_2026-10-18_IMAGE_IMPORT.md) for evidence.
