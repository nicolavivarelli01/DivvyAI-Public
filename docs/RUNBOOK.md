# DivvyAI runbook

Documentation checkpoint: 2026-10-25 (America/Chicago). Commands are operational references;
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
| Collector image | us-central1-docker.pkg.dev/divvy-ai/divvyai/collector:v1 |
| Collector Cloud Run Job | divvyai-collector |
| Collection Scheduler job | divvyai-collect-15m |
| Checker image | us-central1-docker.pkg.dev/divvy-ai/divvyai/dbt-checks:v1 |
| Checker Cloud Run Job | divvyai-dbt-checks |
| Checking Scheduler job | divvyai-dbt-checks-hourly |
| Failure alert policy | projects/divvy-ai/alertPolicies/4698528212121560473 |
| Configured email channel | projects/divvy-ai/notificationChannels/13403291277965377652 |

The scheduler identity, divvyai-scheduler@divvy-ai.iam.gserviceaccount.com,
has Cloud Run Invoker on both Run jobs. It launches executions; it does not
perform the collector's or checker's warehouse work.

The collector identity, divvyai-collector@divvy-ai.iam.gserviceaccount.com,
has Storage Object Creator on the bucket, BigQuery Job User on the project
and BigQuery Data Editor on the raw table. Data Editor permits more than append;
the application chooses CREATE_NEVER and WRITE_APPEND.

The checker identity, divvyai-dbt-checks@divvy-ai.iam.gserviceaccount.com,
has BigQuery Job User on the project, BigQuery Data Viewer on the raw table
and BigQuery Data Viewer on the analytics dataset. These allow the checker to
run queries and read the data and views, without editing the stored data.
It has no archive-bucket permission.

Google's Cloud Run service agent is
service-576327842002@serverless-robot-prod.iam.gserviceaccount.com. The report
shows roles/run.serviceAgent on the project. This is distinct from the running
collector's identity and participates in retrieving its container image.

Local development and dbt use user authentication/ADC outside Git.
Do not download service-account keys.
Cloud Run uses each job's attached service account through ADC; no local user
credentials or service-account key files are copied into either image.

Each deployed image is a snapshot of its build inputs. Editing local code,
profiles or SQL does not update a deployed image. The checker has its own image
containing dbt, the selected checks, models and a non-secret Cloud Run profile.
It does not rebuild the BigQuery views. See the image-update procedure below.

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

## Local MCP agent and website connection

The collector, dbt and AI projects use separate uv environments. The AI code lives
in ai/ and does not replace either scheduled Run job. See the [AI guide](../ai/README.md)
for tool arguments, data windows and acceptance evidence.

Run offline AI checks from the AI directory:

```bash
cd ai
env -u VIRTUAL_ENV uv sync --locked
env -u VIRTUAL_ENV uv run --locked pytest -q
env -u VIRTUAL_ENV uv run --locked ruff check .
env -u VIRTUAL_ENV uv run --locked ruff format --check .
env -u VIRTUAL_ENV uv run --locked mypy *.py tests
env -u VIRTUAL_ENV uv run --locked python agent.py --list-tools
```

Tests and discovery do not submit live model or warehouse requests. The local
server, tests, direct Starlette/uvicorn dependencies and lockfile update are
committed. The current AI checkout passed 189 offline tests, Ruff, formatting
and strict mypy for 29 Python files on October 28.

### Credentials and live questions

Real tools use the existing local Google Application Default Credentials and
need query-job permission plus read access to the raw table and analytics views.
Do not copy local ADC files into an image or download service-account keys.
The MCP child receives the needed Google runtime environment; it does not receive
the OpenAI API key.

The Strands agent uses OpenAI gpt-6-luna with reasoning_effort=none. Its key is read
from OPENAI_API_KEY in the process environment, not from a tracked file. The
following is for zsh; paste the key only at the hidden prompt, never into chat:

```bash
read -rs "OPENAI_API_KEY?Paste your API key (hidden), then press Enter: "
export OPENAI_API_KEY
```

Do not echo the variable, print environment dumps, open .env files or include
provider errors containing credentials in a shared report. An OpenAI API key's
usage is separate from ChatGPT subscription usage.

A live question submits model and BigQuery work under the existing limits:

```bash
env -u VIRTUAL_ENV uv run --locked python agent.py \
  "Were there any capture intervals of at least 30 minutes in the tool's recent UTC window?" \
  --report ../data/evaluation/manual-gap-question-001.json
```

The report is written beneath the ignored data/ directory. Use a new filename
for each attempt; existing files are preserved and rejected before a model call.
Reports record the actual question, answer, tool requests/results, flags, timing,
versions and SDK-reported token usage. The first six availability reports were
compared locally with independent SQL: all six matched the expected tool,
arguments and final answers. Reports do not grade themselves or establish
invoice totals.

The agent admits at most three model calls and two tool calls, runs tools
sequentially and does not retry failed calls automatically. BigQuery queries are
fixed and parameterized, capped at 256 MiB each; history/ranking additionally
cap each tool request at 1 GiB. Two admitted tool calls can together consume more
than one such tool-request cap. None of these controls is an account-wide cap.

### Local browser test

The existing website is a separate static repository,
DivvyAI-Website. Its model-testing.html expects
POST /api/agent. The concept homepage and recorded demonstrations remain as
chosen; they are not the live agent connection.

After syncing the AI environment, start the server in the same terminal where
the model key is set:

```bash
env -u VIRTUAL_ENV uv run --locked python local_web.py \
  --website-dir /path/to/DivvyAI-Website
```

Open http://127.0.0.1:8000/model-testing.html. If an older Node/static preview is
using port 8000, stop it first or add --port 8001 and open that port. This Python
process serves both the existing page/assets and its API, so no website endpoint
change or second preview server is needed. Opening the page or /health does not
call the model or warehouse. A submitted question calls the real agent. /health
reports only the local server and its busy state; it is not collection health.

The server is local only: loopback binding, Host/Origin checks, selected public
static files and no forwarded-header trust. The K_SERVICE guard refuses the
Cloud Run entry point. It allows one active question. After 100 seconds without
an answer it returns a safe 504/TIMEOUT response, but the thread and already
submitted remote work may still finish. The slot remains busy until that work
ends; a second question receives 429 and Retry-After instead of starting duplicate
work. The frontend waits 120 seconds and never retries. These are response
limits, not a guaranteed end-to-end deadline or hard execution cancellation.

Normal Ctrl+C shutdown can wait for the current worker and SDK cleanup. Temporary
API reports are deleted after use, while CLI --report files remain at their chosen
paths. Browser conversation history is held in the tab and cleared on reload.

At the October 25 checkpoint the AI checkout passed 160 offline tests plus
Ruff, formatting and strict mypy for 24 Python files. The website client also
accepted the
API-shaped recorded availability report in an earlier offline check. Live CLI
answers and the initial 20-question benchmark are verified. The supplied live
browser screenshot also confirmed one successful availability answer: station
a3ad8072-a135-11e9-9cda-0a87ae2ba916, October 23 00:00-01:00 UTC, 0.5 average
bikes, 41.5 average docks and four samples each. Reported run time was 12.8
seconds, with one successful tool call and neither call limit reached.
The tool card was collapsed, so expanded arguments/raw payload were not captured.
Request ID: b323324205df46d4937174250fd9ac05.

### Initial 20-question evaluation checkpoint

The initial benchmark passed 19/20 cases (95%): availability 6/6, ranking 8/8
and gaps 5/6. The failed 45-minute gap question recorded APITimeoutError after
60.65 seconds. All 19 completed cases matched expected tool requests,
independent SQL and final answers. This combines six saved availability runs
with 14 new attempts; preserve the original result when checking recovery.

The question set and reused availability evidence are in data/evaluation/.
New attempt reports, ranking/gap references and summary.json are in
data/evaluation/benchmark-run-v1/. The timed-out request has no SDK usage report;
the 66,384 reported tokens cover completed runs only and do not establish a bill.

Availability/ranking reference queries have a separate 512 MiB cap; gap
references use 256 MiB. Production tool limits remain 256 MiB per query and
1 GiB per historical/ranking request. eval_finish.py requires explicit
--execute and a new output directory; it does not retry failed model calls.

The local browser availability check above has passed. Keep the initial
benchmark and browser checkpoint as separate evidence; a later recovery check
does not replace the 95% result.

### Prompt-injection evaluation and CI

The injection evaluation uses a synthetic MCP server and needs only
OPENAI_API_KEY. It makes paid model calls (cents per run):

```bash
cd ai
env -u VIRTUAL_ENV uv run --locked python eval_injection.py --execute \
  --output ../data/evaluation/injection-run-NEW --repeats 3
```

The output directory must be new. The command exits with code 1 below the
fixed 80% pass rate. The last line reports attempts passed, cases x runs and
cases that passed every run.

GitHub Actions runs .github/workflows/ci.yml (offline checks for both projects,
the fixture demo and container builds) on every push to main and every pull
request, and .github/workflows/live-feeds.yml daily.
.github/workflows/injection-eval.yml (the evaluation with --repeats 3) is
manual-only in this public repository, and no API key is configured here. Add
the secret below first, then run the evaluation and read its score:

```bash
gh workflow run "Prompt-injection eval"
gh run list --limit 5
gh run view RUN_ID --log | grep "attempts passed"
```

Per-attempt reports are in each run's injection-run artifact. The key is the
OPENAI_API_KEY repository secret; replace it with `gh secret set OPENAI_API_KEY`
and revoke the old key at OpenAI. To stop paid CI runs, disable the workflow:
`gh workflow disable "Prompt-injection eval"`.

The first 3x CI run, in the private development repository, passed 59/60
attempts (98%); 19/20 cases passed every run. Failed runs do not block merges.

### Hosted agent: deployed October 26

The hosted path is the divvyai.bike model-testing page -> server-side Vercel
function -> the private Cloud Run service divvyai-agent (hosted_web.py) ->
OpenAI and BigQuery. The function authenticates with Vercel OIDC and Google
Workload Identity Federation; no service-account keys are used. Access is
invite-only through Firebase login and a Firestore allow list. A hosted answer
matched the SQL reference with the Mac off. Settings, identities and evidence
are recorded in [PROJECT_STATE.md](PROJECT_STATE.md); step-by-step hosted
operations are not yet documented here. Do not use the local-only entry point
as a public service. Cloud Run cold/warm request latency has not been measured.

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

Listing executions or reading stored logs inspects existing metadata and logs;
these commands do not collect another capture or run BigQuery SQL. Commands
that execute a job do start new work and consume the corresponding allowances.
Use execution-specific names and fixed UTC log windows when investigating an
older incident. The saved verification SQL files target particular historical
captures; they are not generic checks for the latest run.

## Hourly dbt checks

A second Scheduler job launches divvyai-dbt-checks at 10 minutes past every
UTC hour: 10 * * * *. It uses an OAuth-authenticated POST to the checker job's
Run endpoint, with a 30-second launch deadline and zero Scheduler retries.
This offset normally leaves time for collection; it is not an enforced
collection-then-check dependency. Scheduler HTTP 200 still means launch
acceptance, not successful checks.

The checker job has one task, parallelism one, one CPU, 512 MiB, a 300-second
task timeout and zero configured task retries. Its runner performs two checks:

1. Raw source receipt freshness: dbt source freshness for divvy.station_records.
2. Station snapshot quality: dbt test for assert_station_snapshot_quality.

The runner attempts the quality check even if freshness fails. Each dbt command
has a 120-second process timeout. Either nonzero result makes the final process
exit with failure. An empty or unmatched dbt selection also fails instead of
silently reporting that no checks were needed.

The deployed profile is config/dbt-checks/profiles.yml. It uses the attached
checker identity, one thread, a 268435456-byte (256 MiB) limit per query,
a 60-second query execution timeout and zero query retries. These controls do
not cap monthly costs. The runner does not run dbt build, recreate views,
collect feeds or replay archives.

Freshness warns after 30 minutes and errors after 60 minutes. The 30-minute
warning alone does not make this runner fail. The quality test checks keys,
uniqueness, receipt times and valid nonnegative counts; it does not fail solely
because the view is empty. Model unit tests remain separate development
checks, not part of this hourly job.

A manual checker run submits warehouse queries and consumes Cloud Run runtime:

```bash
gcloud run jobs execute divvyai-dbt-checks \
  --wait --region=us-central1 \
  --project=divvy-ai --configuration=divvyai
```

Inspect recent execution results and checker logs without running it again:

```bash
gcloud run jobs executions list \
  --job=divvyai-dbt-checks --region=us-central1 --limit=10 \
  --project=divvy-ai --configuration=divvyai

gcloud logging read \
  'resource.type="cloud_run_job" AND resource.labels.job_name="divvyai-dbt-checks" AND resource.labels.location="us-central1"' \
  --freshness=2h --limit=30 \
  --format='json(timestamp,severity,jsonPayload,textPayload,labels)' \
  --project=divvy-ai --configuration=divvyai
```

A normal run ends with dbt_checks_completed and status passed or failed, plus
a failed_checks list. Start-up or argument errors can occur before that summary;
use the Run execution result and full logs when the summary is absent.

Verified on October 24 UTC: divvyai-dbt-checks-fzdqp was created at 11:10:00
and divvyai-dbt-checks-fw79b at 11:13:24. Both later reported success,
1 / 1 complete. These are recorded checkpoints, not a claim that every hourly
check has succeeded since then.

## Failure email alerts

The live policy is enabled and uses the configured email channel listed above.
Delivery to its configured Outlook inbox was verified in the controlled test below.
The recipient is managed in Google Cloud, rather than recorded in this public
guide. Inspect it in [Monitoring Alerting](https://console.cloud.google.com/monitoring/alerting?project=divvy-ai).

The policy watches run.googleapis.com/job/completed_execution_count for
result=failed in us-central1, scoped to divvyai-collector and
divvyai-dbt-checks. Its per-series alignment sums failures over 300 seconds,
with a threshold above zero and a zero-second retest window. Notifications
are configured for incident opening only, without incident-closure emails.
An incident can cover multiple failures; this is not one email per execution.

The configured autoClose is 1800 seconds without data. This is not a promise
that an incident closes exactly 30 minutes later, nor proof of a successful
recovery. Metric sampling, visibility and notification delivery take time.

Inspect the live policy:

```bash
gcloud monitoring policies describe 4698528212121560473 \
  --format='json(name,enabled,notificationChannels,conditions,alertStrategy)' \
  --project=divvy-ai --configuration=divvyai
```

The checked-in config/monitoring/job-failure-alert.json is a disabled creation
template with no notification channel. The live policy was attached to its
email channel and enabled in the console. Do not create another policy for a
routine change, or replace the live policy with this template and assume the
email configuration is included. Edit the existing policy and preserve its
channel attachment and enabled state unless intentionally changing them.

One delivery test was verified on October 24 UTC. Execution
divvyai-dbt-checks-lmx2l was created at 11:10:24 with a nonexistent profiles
directory argument and later exited with argument error 2; the user received
its alert email at 11:18. This confirms the tested checker-failure notification
path. It does not separately verify notification for an image-import failure
or every failure mode.

Only when intentionally testing notification delivery again, run:

```bash
gcloud run jobs execute divvyai-dbt-checks \
  --wait \
  --args=--project-dir,/app/dbt,--profiles-dir,/missing-alert-test \
  --region=us-central1 \
  --project=divvy-ai --configuration=divvyai
```

This command is expected to fail. It changes arguments for that execution
only and fails before submitting dbt queries. The saved job configuration
remains usable by the normal execution command. It still consumes Cloud Run
runtime and creates a real alert incident. Do not use the collector or edit
production data to test delivery.

These alerts detect observed failed executions. They do not independently
detect a Scheduler job that never launches, establish complete missing-slot
coverage, guarantee notification delivery, or recover failed work. Hourly
freshness checks can identify stale receipts while the checker itself runs.

## Pause and resume schedules

Pause collection:

```bash
gcloud scheduler jobs pause divvyai-collect-15m \
  --location=us-central1 --project=divvy-ai --configuration=divvyai
```

Resume collection:

```bash
gcloud scheduler jobs resume divvyai-collect-15m \
  --location=us-central1 --project=divvy-ai --configuration=divvyai
```

Pause only the hourly checker:

```bash
gcloud scheduler jobs pause divvyai-dbt-checks-hourly \
  --location=us-central1 --project=divvy-ai --configuration=divvyai
```

Resume the hourly checker:

```bash
gcloud scheduler jobs resume divvyai-dbt-checks-hourly \
  --location=us-central1 --project=divvy-ai --configuration=divvyai
```

If collection is paused for planned maintenance while checks continue, the
freshness check will eventually fail as expected. For a complete planned pause,
pause both schedules. If intentionally silencing the failure policy as well:

```bash
gcloud monitoring policies update 4698528212121560473 \
  --no-enabled --project=divvy-ai --configuration=divvyai
```

After resuming collection and confirming a fresh successful load, resume the
checker and re-enable the same policy:

```bash
gcloud monitoring policies update 4698528212121560473 \
  --enabled --project=divvy-ai --configuration=divvyai
```

Pausing a schedule stops future scheduled launches. It does not stop an already
launched execution or delete stored data. Disabling the alert policy silences
notifications; it does not stop either job. Resume restarts the schedule without
recreating missed historical observations. Review costs before resuming work.

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
source events. The two existing operational checks now run hourly. Developing
or changing models and running their synthetic unit tests remain local actions.

The views evaluate their saved SQL when queried, so newly loaded matching raw
rows do not require another dbt build. Changing a model's SQL does require
explicitly deploying that view through dbt. The Cloud Run checker does not
deploy model changes.

## Update the checker image

Local edits do not update the deployed checker. For a future intended update,
review and verify the changed runner, dbt files or profile first. If view SQL
changed, deploy the changed views separately using the dbt guide. Then build
a new, unused checker image tag. The first-update example below uses v2;
choose another unused tag for later updates rather than overwriting a deployed tag:

```bash
docker buildx build --platform=linux/amd64 --load \
  -f Dockerfile.dbt-checks -t divvyai-dbt-checks:v2 .

docker run --rm --platform=linux/amd64 --network=none \
  divvyai-dbt-checks:v2 --help

docker image inspect divvyai-dbt-checks:v2 \
  --format '{{.Os}}/{{.Architecture}} {{.Size}} bytes'
```

The help check runs without cloud credentials or queries. The displayed local
image size is not the exact compressed Artifact Registry storage increase.
Review remaining account allowances and image retention before uploading:

```bash
docker tag divvyai-dbt-checks:v2 \
  us-central1-docker.pkg.dev/divvy-ai/divvyai/dbt-checks:v2

CLOUDSDK_ACTIVE_CONFIG_NAME=divvyai \
docker push us-central1-docker.pkg.dev/divvy-ai/divvyai/dbt-checks:v2

gcloud run jobs update divvyai-dbt-checks \
  --image=us-central1-docker.pkg.dev/divvy-ai/divvyai/dbt-checks:v2 \
  --region=us-central1 --project=divvy-ai --configuration=divvyai
```

This changes the checker job's image. Verify it with one normal manual checker
execution and inspect the result. The existing scheduler continues to invoke
that job name. This procedure does not update the collector image.

The verified v1 checker image index was
sha256:08c45528e5bf0b2d733e7a313518d15c8c96085700cf39ce4c88e5d17ad32265.
If an update needs rollback and that image is still retained:

```bash
gcloud run jobs update divvyai-dbt-checks \
  --image=us-central1-docker.pkg.dev/divvy-ai/divvyai/dbt-checks@sha256:08c45528e5bf0b2d733e7a313518d15c8c96085700cf39ce4c88e5d17ad32265 \
  --region=us-central1 --project=divvy-ai --configuration=divvyai
```

Retain a known working image until its replacement is verified. Deleting an
image needed by a scheduled job can prevent later execution startup. Image
updates are manual; there is no automatic deployment from repository edits.

## Retention, cost review and teardown

Configured archive retention is lifecycle deletion under raw/ after 90 days,
plus seven-day soft delete. Lifecycle deletion is asynchronous and soft-deleted
objects still consume storage. The policy is in config/storage-lifecycle.json.
The raw table uses daily UTC ingestion partitions with 30-day expiration and
requires partition filters. Reusing an existing completed load does not restore
rows removed by expiration; expired-data restoration is not a supported workflow.

Budget alerts do not cap spending. Trial credits do not satisfy the free-tier
constraint. Check account-wide usage and current prices before billable work.
Paused jobs still leave stored data and images in place. Both images use
Artifact Registry storage; a one-time checker run does not make its image
storage temporary. Review cleanup settings and preserve deployed image digests.

Cloud Run jobs have a one-minute minimum billable runtime per execution.
Hourly checks add runtime and warehouse scans; two queries with a 256 MiB
limit each can use up to 512 MiB for those queries per normal check. Profile
limits and task timeouts do not establish a monthly free-tier guarantee.
Cloud Scheduler's free jobs and Cloud Run's usage allowances are shared at
billing-account scope. Paused Scheduler jobs still count toward job billing.

At the October 25, 2026 documentation review, Google's [Observability pricing
page](https://cloud.google.com/products/observability/pricing) still says Monitoring
alert charges begin no sooner than September 1, 2027. Recheck pricing before
relying on that date for later operation. Built-in failure metrics do not
require a custom log-based metric. Billing evidence and trial-credit payments
must be distinguished from unused free-tier allowances.

The planned hosted agent adds OpenAI API usage, query scans and service runtime.
Those costs are separate from collector/checker jobs and have not been measured.
Do not infer the future service's latency or billing from a job's minute-long
launch or billing minimum. No minimum/warm service instance is configured.

For a future teardown, first pause both schedules and inspect running executions.
Disable the failure policy before intentionally removing its workloads. Then
review deletion of both Scheduler jobs, both Run jobs, the dedicated failure
policy and email channel if no other policy uses it, registry images/repository,
analytics/raw/test datasets, archive objects/bucket, dedicated service accounts
and grants. The scheduler service account is shared by the two DivvyAI jobs;
do not remove it while either still needs it. Inspect the test dataset before
deleting it. Preserve required archives and evidence first. Soft-deleted data
may remain billable until expiry. Do not delete a shared billing account or
unrelated project resources.

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
