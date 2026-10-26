# Missed collection: October 18, 2026

Investigation recorded: 2026-10-19 UTC. All event times below are UTC.
Documentation reviewed October 25, 2026; the historical findings are unchanged.
No new live investigation was performed for this documentation refresh.

## Impact

The collection scheduled for 16:45 did not reach application startup.
The dbt interval report showed approximately 29.98 minutes between observed
captures around 16:31 and 17:01 for both station feeds.

This report describes one inspected incident. It does not establish an uptime
percentage or the number of failures across the project's full history.

## Timeline

| Time | Evidence |
|---|---|
| 16:32:00.347 | Previous execution, divvyai-collector-4wv6s, completed a 4,148-row BigQuery load. |
| 16:45:15.501 | Scheduler created execution divvyai-collector-jzlw7. |
| 16:45:19.232 | Scheduler recorded HTTP 200 for the launch request. |
| 16:52:36.118 | Execution conditions reported ContainerImageImportFailed. |
| 17:00:34.320 | Scheduler created execution divvyai-collector-4mhvf. |
| 17:02:00.340 | That execution completed a 4,148-row BigQuery load. |

## Confirmed failure

Cloud Run could not retrieve the container-image manifest from Artifact Registry.
The error included HTTP 401 and "No valid credential was supplied."
The execution's Started condition was false and its task listing was empty.
Python never ran, so the collector did not fetch or archive this observation.

The failed import targeted this manifest:

```text
us-central1-docker.pkg.dev/divvy-ai/divvyai/collector@sha256:dba143347a2501ca68759f5573bfce0a8c5dae5ca5c2656ce88b48163e16b0bf
```

The preceding and following successful executions used the same manifest.
The next execution started the collector and logged a successful load receipt.

## Interpretation and response

A temporary authentication failure in the image-import path is consistent with
the evidence. Why valid credentials were missing or rejected is unconfirmed.
This is not evidence of a confirmed Google outage or a specific credential bug.

The IAM snapshot taken during the investigation showed roles/run.serviceAgent
on service-576327842002@serverless-robot-prod.iam.gserviceaccount.com. That role
includes Artifact Registry read/download permissions. A later IAM snapshot
does not prove permissions at the failure time or every effective access
constraint.

No application, permission or retry change was made as part of this investigation.
The next scheduled collection succeeded; the missed historical observation
remains missing. Manual archive replay cannot recover data never fetched.

## Later monitoring and agent context

Hourly warehouse checks and failed-execution email alerts were verified on
October 24; they do not change the explanation of this earlier incident.
The read-only MCP gap tool uses the recent capture-interval view, so it cannot
now retrieve this October 18 event. Its default threshold is at least 30
minutes; this recorded receipt interval of about 29.98 minutes would also fall
below that threshold. Scheduled execution failure and measured receipt interval
are related evidence, but they are not the same test.

See the [runbook](RUNBOOK.md) for current operating procedures and
[project state](PROJECT_STATE.md) for the subsequent verified milestones.

## Evidence and limits

Evidence came from execution descriptions, task listings, complete Cloud Run
system-event responses, Scheduler logs, successful load receipts and current
configuration/IAM metadata. Raw diagnostic output remains a local artifact,
outside Git; this document preserves the relevant finding and identifiers.

This was a pre-start image-import failure, not an observed Python API-request,
parsing, Storage-upload or BigQuery-load failure. Future prediction labels must
treat missing observations as unknown.

Reference: [Cloud Run service-agent permissions](https://docs.cloud.google.com/iam/docs/roles-permissions/run#run.serviceAgent).
