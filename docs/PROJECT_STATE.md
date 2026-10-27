# DivvyAI project state

Last updated: 2026-10-26 (America/Chicago).

## Current phase

The current data-engineering milestone is implemented and verified: scheduled
GCP ingestion, tested dbt analytics, hourly warehouse health checks, failure
email notifications and a recorded availability-chart demo.

The October 18 collection gap has been investigated. Its immediate cause was
an image-import authentication failure before the collector started. The reason
valid registry credentials were missing or rejected remains unconfirmed.

The approved extension is a read-only Python MCP server and a Strands
tool-calling agent over existing BigQuery data. The three MCP tools have passed
offline checks and live BigQuery calls. The Strands/OpenAI agent has answered
ranking, gap and historical-availability questions through MCP. The ranking
answer matched the previously checked tool result; the gap answers disclosed
the tool's limited recent window. The initial 20-question benchmark passed
19/20 cases (95%): availability 6/6, ranking 8/8 and capture gaps 5/6. All 19
completed cases matched independent SQL. One gap case recorded an API timeout
after 60.65 seconds. Prediction remains deferred, and the deployed collection
pipeline is unchanged.

The agent is now hosted. On October 26 the agent/API/MCP tools were deployed as
a separate, IAM-protected Cloud Run service, divvyai-agent. One authenticated
hosted question matched the saved SQL reference. The separate website
(DivvyAI-Website on Vercel) reaches the service through a server-side Vercel
function using Vercel OIDC and Google Workload Identity Federation; no
service-account keys are used. A browser answer from the hosted website matched
the SQL reference, and a question was answered from a phone while the Mac was
off.

The model-testing page is public at https://divvyai.bike but invite-only; the
earlier divvyai-weld.vercel.app address still works. Visitors log in with
Firebase email and password, must confirm their email and must be on a
Firestore allow list. The website's server function checks this before any
Google credential, model or warehouse call. No per-person daily question limit
or durable spending cap exists yet.

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
- A separate containerized dbt checker on Cloud Run, scheduled hourly.
- A runner that executes receipt freshness and snapshot quality, attempts both
  checks and returns failure if either command fails.
- Six offline runner tests; Ruff, formatting and mypy passed for the two new
  Python files at the implementation checkpoint.
- An enabled Cloud Monitoring policy for failed collector or checker executions,
  with delivery to the configured Outlook email address verified.
- A recorded October 21 Data Studio chart, PDF, PNG and 24-row CSV in docs/assets/.
- An isolated ai/ uv project with a local Python MCP 2.1.1 server over stdio.
- Three read-only tools: get_station_availability, find_capture_gaps and
  rank_stations, using fixed parameterized queries and typed JSON results.
- Historical availability and ranking reads from existing retained raw data,
  capped at 256 MiB per query and 1 GiB per historical/ranking request.
- The AI checkout passed 160 offline tests, Ruff, formatting and strict mypy
  for 24 Python files on October 25. The earlier JSON-report checkpoint had 46
  passing tests; the local web-server checkpoint had 63. The AI suite was not
  re-run during the October 26 hosting and website work.
- A Strands agent using OpenAI gpt-6-luna with reasoning_effort=none,
  sequential MCP calls, at most three model calls and two admitted tool calls.
- API credentials supplied privately: through the terminal environment locally
  and from Secret Manager on Cloud Run. The Google-facing MCP child does not
  receive the OpenAI API key.
- An optional --report argument saves actual tool calls, returned data, the
  answer, limit flags, timestamps and SDK-reported token usage as JSON.
- A local-only Python web server serves the existing website and POST /api/agent,
  reuses the same agent and reports, and has offline tests. Its source, tests,
  explicit Starlette/uvicorn dependencies and lockfile update are committed.
- A hosted agent-only entry point, ai/hosted_web.py, reusing the local server's
  models and helpers: GET /health and POST /api/agent, one active question,
  a 100-second response timeout, no automatic retries and JSON errors with
  request IDs. It relies on Cloud Run IAM for caller authentication.
- A separate agent container image built from Dockerfile.agent and uploaded to
  Artifact Registry, running as an unprivileged user with one uvicorn worker.
- A Cloud Run service, divvyai-agent, requiring IAM authentication, scaling to
  zero and running at most one instance.
- A successful live local-browser availability answer with one tool call and
  displayed averages/sample counts matching the recorded SQL reference.
- A saved 20-question evaluation set, independent SQL references and a runner
  that preserves reports and scores. Availability/ranking reference queries
  use a 512 MiB cap; gap reference queries use 256 MiB.
- A recorded initial benchmark result of 19/20 passed (95%). All 19 completed
  cases matched the expected tools/arguments, SQL results and final answers.
  The remaining attempt recorded an API timeout after 60.65 seconds.

In the separate DivvyAI-Website repository (main at 6e5a7ac):

- api/agent.mjs, a Vercel function for same-origin POST /api/agent. It checks
  the deployment, address, method, origin and question; checks the visitor's
  Firebase login and allow-list entry; exchanges the deployment's Vercel OIDC
  token for a Google ID token; sends one request to Cloud Run; and relays the
  agent's JSON unchanged. It never retries or caches, and never logs questions,
  emails or tokens. Allowed addresses: preview URLs, and divvyai.bike and
  divvyai-weld.vercel.app on production.
- vercel.json enabling Fluid compute with a 120-second limit for the function.
- Firebase email/password login on model-testing.html (js/account.js,
  js/account-core.js), with email confirmation, password reset and an
  invite-only "no access" state pointing to divvyai@nickvivarelli.com.
- firestore.rules, the copy in Git of the published allow-list rules.
- Offline website checks: 44 unit tests and 88 headless-browser checks using
  synthetic agent replies and a synthetic login. None contacts Vercel,
  Firebase, Google or the agent.

Python tests and Ruff, formatting and mypy checks passed at implementation
checkpoints. Default Python tests do not call live feeds or cloud services.
dbt tests execute SQL on BigQuery, including when their inputs are synthetic.

## Cloud configuration

Agent, website-access and login settings below were read back on October 26.
Collection-pipeline settings come from earlier recorded checkpoints and were not
re-queried. A read-only query-job usage audit and the initial agent evaluation
were run on October 25. These checks do not establish current health or a
complete billing ledger.

- Project: divvy-ai; project number: 576327842002.
- gcloud configuration: divvyai.
- Region: us-central1.
- Billing budget with alerts configured; alerts do not cap spending.
- Local ADC credentials and the personal dbt profile remain outside Git.
- config/dbt-checks/profiles.yml contains nonsecret connection settings and is
  tracked in Git; the checker obtains credentials from its attached account.
- No service-account keys are used.

Core resources:

- Bucket: gs://divvy-ai-raw-576327842002.
- Raw dataset/table: divvy-ai.divvyai_raw.station_records.
- Analytics dataset: divvy-ai.divvyai_analytics.
- SDK acceptance dataset: divvy-ai.divvyai_test; current contents not rechecked.
- Artifact Registry repository: divvyai.
- Collector image: us-central1-docker.pkg.dev/divvy-ai/divvyai/collector:v1.
- Checker image: us-central1-docker.pkg.dev/divvy-ai/divvyai/dbt-checks:v1.
- Agent image: us-central1-docker.pkg.dev/divvy-ai/divvyai/agent@sha256:257e42631c293a70e39a2ee817ab0c80d753109ab626294144ef73c654b5f417.
- Cloud Run Jobs: divvyai-collector and divvyai-dbt-checks.
- Cloud Run service: divvyai-agent, revision divvyai-agent-00001-wzj, created
  2026-10-26 05:15 UTC.
- Secret: divvyai-openai-api-key, version 1, supplied to the service as
  OPENAI_API_KEY.
- Workload identity pool: vercel, with OIDC provider divvyai-website.
- Security Token Service API (sts.googleapis.com) enabled for the federation.
- Scheduler jobs: divvyai-collect-15m and divvyai-dbt-checks-hourly.
- Alert policy: projects/divvy-ai/alertPolicies/4698528212121560473.
- Email channel: projects/divvy-ai/notificationChannels/13403291277965377652.

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

Cloud Run job settings:

| Job | Tasks / parallelism | CPU / memory | Task timeout | Task retries |
|---|---|---|---|---|
| divvyai-collector | 1 / 1 | 1 / 512 MiB | 60 seconds | 0 |
| divvyai-dbt-checks | 1 / 1 | 1 / 512 MiB | 300 seconds | 0 |

The collector and checker use separate images. The checker image contains the
runner, pinned dbt environment, SQL/YAML files and nonsecret cloud profile.
Image contents are fixed at build time: local edits require rebuilding, pushing
and explicitly updating the corresponding job. Deploying changed BigQuery view
definitions is a separate dbt operation. Parallelism one does not prevent
separate executions from overlapping.

Cloud Run service settings:

| Service | CPU / memory | Instances | Concurrency | Request timeout | Billing |
|---|---|---|---|---|---|
| divvyai-agent | 1 / 1 GiB | 0 minimum / 1 maximum | 1 | 110 seconds | request-based |

- Ingress is all; Cloud Run IAM authentication is required. Unauthenticated
  requests were rejected at the deployment checkpoint.
- URLs: https://divvyai-agent-576327842002.us-central1.run.app and
  https://divvyai-agent-65x4npicyq-uc.a.run.app. The second is the metadata
  status.url and is the ID-token audience the website uses.
- The application stops waiting after 100 seconds and admits one question at a
  time, answering 429 BUSY with Retry-After otherwise. Per question: at most
  three model calls and two admitted tool calls; OpenAI timeout 60 seconds with
  no automatic retries; BigQuery 256 MiB per query and 1 GiB per
  historical/ranking tool invocation.
- These are capacity and request controls, not durable global usage or dollar
  caps. A timeout or lost connection does not cancel already-running remote work.
- Image contents are fixed at build time; agent code changes require a new image
  and an explicit service update.

Scheduler settings:

| Trigger | Schedule | Starts |
|---|---|---|
| divvyai-collect-15m | */15 * * * * | divvyai-collector |
| divvyai-dbt-checks-hourly | 10 * * * * | divvyai-dbt-checks |

Both use Etc/UTC and were reported ENABLED. Each uses an authenticated OAuth
POST to its Cloud Run Jobs run endpoint with body {}, a 30-second launch-request
deadline and zero configured retries. Minute 10 gives the normal collection
cycle time to finish; it is a time offset, not a dependency between jobs.
HTTP 200 launch acceptance does not prove that a job completed successfully.

Website hosting (Vercel project divvyai, team nicola-vivarellis-projects):

- Production addresses: divvyai.bike (main) and divvyai-weld.vercel.app, both
  deployed from main. www.divvyai.bike redirects to divvyai.bike (308).
- Domain divvyai.bike registered at Namecheap on 2026-10-26; a paid annual
  registration outside GCP whose renewal price is not recorded here. DNS stays
  at Namecheap: A @ 216.198.79.1 and CNAME www to
  8d94ee5d8f52d201.vercel-dns-017.com. Vercel issues the HTTPS certificate.
- Vercel Authentication with Standard Protection: preview deployments require a
  Vercel login; the production addresses are public.
- OIDC Federation with the team issuer
  https://oidc.vercel.com/nicola-vivarellis-projects.
- The function runs only on preview URLs, divvyai.bike and
  divvyai-weld.vercel.app. www.divvyai.bike, other production aliases, other
  custom domains and development are refused. The Google federation checks the
  Vercel environment, not the address, so the domain needed no Google change.
- The Vercel plan and its included usage were not re-checked.

Login project (Firebase), separate from divvy-ai:

- Project divvyai-dc6ef, project number 460711887411, Spark plan with no
  billing account.
- Firebase Authentication with email/password; email-link sign-in is off.
  Authorized domains: localhost, divvyai-dc6ef.firebaseapp.com,
  divvyai-dc6ef.web.app, the agent-proxy preview alias,
  divvyai-weld.vercel.app and divvyai.bike.
- Firestore (default) database in us-central1, Native mode, Standard edition.
  Collection agent_access/{lowercase email} holds allowed: true | false.
- Published rules: a logged-in person with a confirmed email may read only
  their own entry; nobody can write from the website. Copy in Git:
  DivvyAI-Website/firestore.rules.
- Browser API key restricted to the preview alias, divvyai-weld.vercel.app,
  divvyai.bike and divvyai-dc6ef.firebaseapp.com, and to the Identity Toolkit
  and Token Service APIs. The API Keys API was enabled only to add divvyai.bike
  and then disabled again.
- Google Analytics is linked to the Firebase project; the website does not load it.
- Firebase's own service account was created automatically; no key was generated.

## Identities and permissions

Collector: divvyai-collector@divvy-ai.iam.gserviceaccount.com.

- Storage Object Creator on the archive bucket.
- BigQuery Job User on the project.
- BigQuery Data Editor on the raw table only.

Data Editor also permits reading, changing and deleting that table. Loading
with CREATE_NEVER and WRITE_APPEND is an application choice.

Checker: divvyai-dbt-checks@divvy-ai.iam.gserviceaccount.com.

- BigQuery Job User on the project.
- BigQuery Data Viewer on divvyai_raw.station_records.
- BigQuery Data Viewer on the divvyai_analytics dataset.

The checker can submit queries and read the source table and analytics views.
Its warehouse access is read-only. The scheduled path does not build models or
store failing rows in persistent tables.

Scheduler: divvyai-scheduler@divvy-ai.iam.gserviceaccount.com.

- Cloud Run Invoker on both the collector and checker jobs.

Agent runtime: divvyai-agent@divvy-ai.iam.gserviceaccount.com.

- BigQuery Job User on the project.
- BigQuery Data Viewer on divvyai_raw.station_records.
- Secret Manager Secret Accessor on divvyai-openai-api-key.

Website invoker: divvyai-web-invoker@divvy-ai.iam.gserviceaccount.com.

- Cloud Run Invoker on the divvyai-agent service only.
- No project, warehouse or secret roles.
- Service Account OpenID Token Creator on this account, granted only to the
  federated subjects
  owner:nicola-vivarellis-projects:project:divvyai:environment:preview and
  owner:nicola-vivarellis-projects:project:divvyai:environment:production.
- The federation provider accepts only tokens from the team issuer with
  audience https://vercel.com/nicola-vivarellis-projects, team ID
  team_kkAtIJfH60KsfyZvAEtAVJry, project ID prj_AvTZffE7dyvAg04ENTk7srUjQIbq
  and environment preview or production.
- Every preview deployment of the Vercel project, from any branch, receives the
  preview subject.

Website visitors are identified by Firebase ID tokens. The function reads the
visitor's own allow-list entry with that token under the Firestore rules; it
holds no Firebase credentials. The allow list is edited in the Firebase console.

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
- Checker profile: config/dbt-checks/profiles.yml, using attached-service-account
  ADC via the same oauth method; target name checks.
- Target: divvy-ai.divvyai_analytics in us-central1; one thread.
- Last verified per-query maximum_bytes_billed: 268435456 bytes (256 MiB).
- This guard is per query, not a monthly spending cap or free-tier allowance.
- The checker profile sets zero query retries and a 60-second query execution
  timeout. The runner limits each dbt subprocess to 120 seconds.

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
or exact warehouse completion time. A 30-minute freshness warning does not fail
the checker; a 60-minute error does. With hourly execution, a silent collection
stoppage can take close to two hours to be flagged, plus execution and alerting
latency.

The hourly job runs only these existing checks:

1. dbt source freshness --select source:divvy.station_records.
2. dbt test --select assert_station_snapshot_quality.

View deployment, all five synthetic dbt unit tests and the root Python test
suite remain separate developer operations. Views read new matching raw rows
when queried; they do not require hourly rebuilding. The runner treats empty
selections, process errors, timeouts and nonzero dbt exits as failures.

## Failure notifications

The live Cloud Monitoring policy is enabled and linked to the email channel.
It selects run.googleapis.com/job/completed_execution_count with result=failed
for divvyai-collector or divvyai-dbt-checks in us-central1. It sums each series
over 300 seconds and triggers above zero, with a zero-second retest window.
Only incident-open notifications are requested; normal successful runs do not
trigger this rule. The configured autoClose is 1800 seconds; it is not an exact
email-delivery or incident-closure deadline.

config/monitoring/job-failure-alert.json is a disabled creation template without
notification-channel IDs. The live policy was enabled and its recipient selected
in the Cloud Console. The template is not a full export of that live state.
Recreating it would create another policy; routine operations use the existing ID.

Email delivery was verified with one intentional checker failure. This does not
establish detection of every possible failure path or a scheduler that stops
launching jobs. Metrics, alert evaluation and email delivery add latency.

The alert policy covers the two jobs only. Failures of the divvyai-agent
service or the website function do not send email.

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

### Hourly checks and email acceptance: October 24

Two manual cloud runs completed both data checks successfully. One recorded
completion was divvyai-dbt-checks-nr8mc at 10:49:34 UTC, with event
dbt_checks_completed, status passed and an empty failed_checks list.

The execution listing then showed the following. Times are execution creation
times on October 24, 2026, UTC, not completion times.

| Execution | Created | Started by | Outcome |
|---|---|---|---|
| divvyai-dbt-checks-fzdqp | 11:10:00 | Scheduler account, scheduled slot | Succeeded, 1 / 1 complete |
| divvyai-dbt-checks-lmx2l | 11:10:24 | Local operator, intentional failure | Failed with exit code 2 |
| divvyai-dbt-checks-fw79b | 11:13:24 | Scheduler account, manually requested trigger | Succeeded, 1 / 1 complete |

The intentional test used an execution-only argument override naming a missing
profiles directory. It failed before BigQuery checks and did not change the
saved job arguments. The alert email reached the configured Outlook inbox at
11:18 UTC. The email identified the checker, result failed and a count of one
above the zero threshold. Later read-only inspection commands did not trigger it.

This verifies the scheduled checker path, failure signal and email delivery at
that checkpoint. It is not a complete reliability measurement.

### Recorded chart demo

[DEMO.md](DEMO.md) includes the October 21 UTC availability chart for station
a3ad8072-a135-11e9-9cda-0a87ae2ba916, with PDF, PNG and CSV under assets/.
The CSV has 24 hourly rows and four status samples per hour, 96 in total.
It was exported from historical raw data and uploaded to Data Studio manually.
The report is static and is not a live BigQuery/dbt connection. The current
sql/dashboard/station_hourly_availability.sql selects another station from the
current-day mart and does not reproduce this historical chart.

### Local MCP tool acceptance

get_station_availability completed a current-day live call. A historical call
for October 21 UTC matched all 24 rows of the recorded demo CSV, including sample
counts and averages across 96 observations. Four data queries billed 95 MiB.

find_capture_gaps checked 102 completed intervals per feed from October 24
00:00 UTC through October 25 01:31:27 UTC. No interval met the inclusive
25-minute threshold; 38 MiB was billed. Empty results do not detect ongoing
silence after the last capture or establish source freshness.

rank_stations queried October 23, 08:00-08:59 America/Chicago, within the
illustrative downtown_demo rectangle. It found 248 eligible station candidates
and returned five tied at an observed empty-bike rate of 1.0, each supported
by four valid samples from one date. Three data queries billed 253 MiB.
Station operating flags and source-report age were not filtered.

These checks verify the three local MCP tool paths and bounded data results.
They do not establish operating-station recommendations or prediction quality.

### Local Strands agent acceptance

On October 25, the agent selected rank_stations with October 23 dates, hour 8,
weekdays, downtown_demo, empty_bikes_rate and limit 5. The five named stations,
4/4 zero-bike observations and one sampled day matched the previously checked
ranking output. The answer disclosed that these are samples and that the
geographic box is illustrative.

Two further gap questions selected find_capture_gaps and reported an observed
30.05-minute interval around October 25 06:45-07:15 UTC. The answers explicitly
limited their coverage to October 24 UTC midnight through query time, rather
than claiming the requested week-and-a-half or rolling 48-hour coverage. This
new interval has not been independently checked with SQL and is not proof of
a failed Cloud Run execution.

At the earlier JSON run-report checkpoint, 46 offline tests, Ruff, formatting
and strict mypy passed.
A live agent question for station
a3ad8072-a135-11e9-9cda-0a87ae2ba916 during October 21 00:00-01:00 UTC selected
get_station_availability with the correct arguments and returned 1.25 average
bikes, 43.5 average docks and four valid samples for each. These match the
recorded CSV. The report saved under data/evaluation/. This is one checked
answer, not a measured accuracy score or a billing ledger.

### Initial 20-question evaluation: 19/20 passed

The saved data/evaluation/questions-v1.json contains six availability, eight
ranking and six capture-gap questions. The initial benchmark passed 19/20 cases
(95%), combining six earlier availability reports with 14 new agent attempts.

| Question group | Passed / attempted |
| --- | --- |
| Availability | 6/6 |
| Station ranking | 8/8 |
| Capture gaps | 5/6 |
| Total | 19/20 (95%) |

All 19 completed cases selected the expected tools and arguments, returned data
matching independent SQL and produced matching final JSON answers. The failed
case, gaps_03, recorded APITimeoutError after 60.65 seconds, with no completed
answer or tool trace saved. It receives zero credit in all score columns.
No automatic retry replaced this first-pass result.

This is a fixed structured-JSON benchmark. Counts and list order must match;
floats use relative/absolute tolerance 1e-9, and extra JSON fields are ignored.
It does not establish performance across arbitrary questions. No SQL reference
was calculated for the timed-out gap case. The five completed gap references
read raw records at each tool response's recorded query time.

Local evidence:

- data/evaluation/questions-v1.json: the original 20 questions and expected
  tool arguments.
- data/evaluation/availability-reference-v1.json and availability-run-v1/:
  independent availability references and the six reused agent reports.
- data/evaluation/benchmark-run-v1/: manifest, eight ranking references,
  five gap references, 14 new attempt reports and summary.json with all scores.

The 19 completed runs reported 66,384 tokens. The timed-out request has no SDK
usage report, so that number is incomplete usage accounting, not an invoice.
Mean agent-attempt elapsed time was 13.56 seconds including the timeout, or
11.08 seconds across completed cases. These are local CLI measurements.

The availability reference query billed 93 MiB, the ranking reference query
208 MiB and the five gap reference queries 190 MiB combined. The 19 completed
agent runs reported 1,430 MiB billed across tool queries. These are recorded
query statistics, not a complete billing ledger.

A project-specific metadata audit at October 25 23:42 UTC reported 4.7754 GiB
of query usage for the billing month, with no audit issues. It preceded these
evaluation runs and is not a current remaining-allowance guarantee.

The AI source and test files match the temporary copy that passed 160 offline
tests, Ruff, formatting and strict mypy for 24 Python files. The separate local
browser check below passed. Preserve the 95% result; hosted checks are separate
acceptance evidence and do not replace it.

### Website connection: local browser acceptance passed

The separate static DivvyAI-Website repo contains model-testing.html.
The page was published on Vercel and .vercelignore was completed. The mock
homepage is intentionally retained. The page expects POST /api/agent and does
not fall back to canned answers when unavailable.

The existing saved report contains structuredContent compatible with that
page. ai/local_web.py and ai/tests/test_local_web.py are committed, together
with explicit starlette==1.10.0 and uvicorn==0.54.0 dependencies and the AI
lockfile update. The server serves the page and API together, reusing agent.ask().

At the local web-server checkpoint, 63 offline tests, Ruff, formatting and
strict mypy passed for 15 Python files. The AI suite later had 160 passing
tests and clean quality checks for 24 Python files. An
additional offline HTTP -> Strands -> MCP -> HTTP check previously passed
with a mocked model and synthetic MCP server. The real website client accepted
an API-shaped recorded report. A later supplied screenshot confirms a live
browser answer for the October 23 00:00-01:00 UTC hour at station
a3ad8072-a135-11e9-9cda-0a87ae2ba916. The displayed answer matched the saved SQL
reference: 0.5 average bikes, 41.5 average docks and four bike/dock samples.

The page showed one successful get_station_availability call, gpt-6-luna,
12.8 seconds reported run time, 6.99 seconds tool duration and 3,179 reported
tokens. Neither call limit was reached. Request ID:
b323324205df46d4937174250fd9ac05. The tool card was collapsed in the screenshot;
expanded tool arguments, raw payload and snapshot_count were not captured.
This accepts the displayed local browser path for one question.

The local API waits up to 100 seconds for a response, does not automatically
retry, and retains its single active-request slot until work actually ends.
A response timeout does not cancel the thread or remote OpenAI/BigQuery work.
It binds loopback, checks Host/Origin, serves selected public website files
and refuses the Cloud Run entry point. These local protections are not public
authentication or account-wide spending controls. Temporary API reports are
deleted after use; they are not durable evaluation records.

### Hosted agent acceptance: October 26

- An unauthenticated request to divvyai-agent was rejected.
- Authenticated GET /health returned {"status":"ok","busy":false}.
- One authenticated POST /api/agent completed in 27.29 seconds with one
  successful get_station_availability call: station
  a3ad8072-a135-11e9-9cda-0a87ae2ba916, start 2026-10-23T00:00:00Z, end
  2026-10-23T01:00:00Z.
- The tool data and final answer matched the saved independent SQL reference:
  0.5 average bikes, 41.5 average docks, four snapshots, four bike samples and
  four dock samples. Neither call limit was reached.

This is a separate hosted acceptance check. It does not rerun or replace the
19/20 benchmark.

### Website connection: hosted acceptance, October 26

- The website function was first deployed as a preview of branch agent-proxy
  (DivvyAI-Website commit d058411).
- Without a Vercel login, the preview page redirected to Vercel's login (302)
  and POST /api/agent returned 401. The production /api/agent returned 404 at
  that time.
- Vercel function logs showed seven questions relayed between 07:14 and 07:19
  UTC, each with upstream status 200. Cloud Run request logs showed the same
  seven POSTs.
- A preview question selected get_station_availability with the known station
  and hour arguments. The expanded tool card showed 0.5 average bikes, 41.5
  average docks and four snapshots, bike samples and dock samples, with four
  queries, 80.2 MB processed and 84.9 MB billed. This matches the SQL reference.
- A question was answered on another device (a phone) through the preview while
  the Mac was fully off.

### Invite-only login: October 26

- The published Firestore rules passed seven Firebase Rules API test cases:
  own entry with a confirmed email allowed, including a mixed-case token email;
  unconfirmed email, another person's entry, no login, create and list denied.
- On the preview (DivvyAI-Website commit f9dadb3), an allow-listed account
  received an answer from find_capture_gaps (agent request
  d203d6f4d4b34ec9b0df4ef94c21a7cc). An account not on the list saw
  "You don't have access yet".
- The public site was enabled with commit e60b50b, merged to main. At 09:54 UTC
  the first public question returned 502 UPSTREAM_AUTH_FAILED: the ID-token
  step received 403 while the new production grant was still propagating. No
  question reached the agent. The next question, 15 seconds later, returned 200
  (agent request 83bae8f49fa54ab2a7b1416d00fb9779).
- Website offline checks at the final change: 44 unit tests and 88/88 headless
  browser checks. A smoke test loaded the real Firebase SDK 12.19.0 and showed
  the login form to a logged-out visitor.
- Revoking an entry (allowed: false) is covered by offline tests but has not
  been checked live.

These checks accept the hosted path and access control for the questions shown.
They do not establish per-person usage limits, measured availability or
production-wide answer accuracy.

### Custom domain: October 26

- Public DNS returned 216.198.79.1 for divvyai.bike and the Vercel CNAME for
  www.divvyai.bike. Vercel listed both domains as verified.
- https://divvyai.bike/ and /model-testing.html returned 200 with a valid
  certificate. http://divvyai.bike/ and https://www.divvyai.bike/ redirected
  (308) to https://divvyai.bike/.
- The production deployment of DivvyAI-Website commit 6e5a7ac was READY. On
  divvyai.bike, POST /api/agent without the page's headers returned 403
  ("Questions must come from this site's model-testing page"); with them but
  without a login it returned LOGIN_REQUIRED.
- The Firebase browser key accepted requests with a divvyai.bike or
  divvyai-weld.vercel.app referer and refused another site (403).
- An allow-listed account logged in at https://divvyai.bike/model-testing.html
  and received an answer. That answer was not compared with a SQL reference.
- Website offline checks with the change applied: 44/44 unit tests and 93/93
  headless-browser checks, run on a working tree that also held uncommitted
  homepage changes.

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

- The completed scope includes ingestion, dbt analytics, a recorded demo,
  scheduled health checks and tested failure email. MCP tools, a Strands agent,
  JSON reports, a hosted agent service and an invite-only website connection
  have also been verified.
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
- Website access is invite-only. Anyone can create a Firebase account, but only
  email-confirmed accounts set to allowed: true get answers. The allow list is
  edited manually.
- The website function sends at most one request to the agent per question,
  never retries and stops waiting after 115 seconds. The agent admits one
  question at a time. These are capacity controls, not per-person quotas or
  spending caps.
- Each allowed question uses OpenAI tokens (about 3,000 in observed runs) and
  BigQuery scans. No per-person daily limit or durable usage ledger exists.
- IAM changes can take time to propagate; a request right after a grant can
  fail once.
- Preview deployments from any branch can use the agent, behind Vercel's login
  and the allow list.
- divvyai.bike is the main public address; www.divvyai.bike only redirects.
  The function, Firebase authorized domains and browser key allow divvyai.bike
  but not www, so reversing the Vercel redirect would break login and the agent
  on the custom domain. divvyai-weld.vercel.app still works and is not
  redirected.

## Not implemented

- Automatic dashboard refresh or a live dashboard connection to BigQuery.
- Forecasting features, labels, model evaluation or predictions.
- Automatic recovery from model API failures or broader agent evaluation
  beyond the initial 20 structured questions.
- Per-person daily question limits, durable usage records or spending caps for
  the hosted agent.
- Alerts for agent-service or website-function failures.
- Automatic recovery or scheduled model deployment/full development test suites.
- Guaranteed delivery, complete missing-slot detection or measured uptime.
- Terraform, keyless GitHub Actions deployment or a local Airflow DAG.
- A durable warehouse availability audit or validated ML feature dataset.

Credentials, keys and local authentication state stay out of Git; the tracked
checker profile contains connection settings only.

The project does not demonstrate gap-free delivery, measured uptime, automatic
recovery, continuous availability measurement, forecasting, public self-serve
access, per-user rate limits or spending caps, production-wide agent accuracy
or a 100% pass rate on the initial benchmark.

## Documentation map

- [README](../README.md): project overview and separate environments.
- [AI guide](../ai/README.md): MCP tools, agent setup, reports and local website.
  It predates the hosted service.
- [dbt guide](../dbt/README.md): view windows, checks and development commands.
- [Runbook](RUNBOOK.md): deployment operations, recovery, alerts and local AI use.
  It predates the hosted service.
- [Demo](DEMO.md): recorded evidence and bounded live demonstrations.
- [October 18 incident](INCIDENT_2026-10-18_IMAGE_IMPORT.md): historical evidence.
- DivvyAI-Website (separate repository): model-testing page, api/agent.mjs,
  Firebase login and firestore.rules. Its README and MODEL_TESTING.md predate
  the hosted connection.
