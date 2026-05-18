# Workflow

Workflow tools answer a deceptively simple question: what should happen next?
The answer depends on what kind of system is asking. A data platform usually
cares about schedules, dependencies, assets, freshness, lineage, and backfills.
An application usually cares about durable state, retries, timers, signals, and
surviving process crashes. A small integration often just needs a webhook, a few
API calls, and enough history to see what happened.

This repo has four workflow stacks because those are different problems, not
four interchangeable UIs:

[Airflow](/stacks/data/workflow/airflow) schedules DAGs. It is the natural fit
when work is best described as "run these tasks in this order on this schedule."
It is useful for recurring batch jobs, dependency ordering, retries, operator
visibility, and manual reruns.

[Dagster](/stacks/data/workflow/dagster) models software-defined data assets.
It is the natural fit when the durable object is a table, file set, model,
report, or other data product, and the important questions are "what produced
this asset?", "what depends on it?", "is it fresh?", and "what metadata did the
materialization record?"

[Temporal](/stacks/data/workflow/temporal) runs durable application workflows.
It is the natural fit when workflow code is part of an application and must live
through long waits, crashes, retries, timers, signals, human steps, and worker
deploys. Temporal is closer to an event-sourced application runtime than to a
batch scheduler.

[n8n](/stacks/data/workflow/n8n) provides visual automation and webhook-driven
glue. It is the natural fit for lightweight integrations, notifications, small
internal automations, HTTP callbacks, and API stitching where the visual editor
is faster than building and deploying a service.

## Start With The Shape Of The Work

Avoid choosing by surface features alone. All four tools have a UI. Several can
run on a schedule. Several can retry. Several store execution history. Those
similarities are not the deciding factor.

Choose Airflow when the schedule and task graph are the main model. A daily
extract, a weekly report build, a dependency-ordered set of maintenance jobs, or
a pipeline that needs manual backfills fits the DAG mental model. The execution
unit is a task instance inside a DAG run.

Choose Dagster when the data asset graph is the main model. A warehouse table,
object-store prefix, feature set, dashboard extract, or model artifact is not
just a task that happened to run. It is a thing with lineage, materialization
history, metadata, owners, dependencies, and freshness expectations. The
execution unit is a run that materializes assets.

Choose Temporal when the business process is the main model. A user onboarding
flow, approval workflow, provisioning flow, payment lifecycle, background
fulfillment process, or long-running application operation often needs to pause,
receive signals, retry activities, survive workers restarting, and resume from
recorded history. The execution unit is a workflow execution with an event
history.

Choose n8n when the integration is the main model. A webhook should trigger a
small set of API calls. A form submission should post a message. A lightweight
scheduled check should notify someone. A one-off internal automation should be
visible and editable without creating a full application. The execution unit is
an n8n workflow execution.

If the same use case seems to fit multiple tools, ask what you would be most
upset to lose:

```text
schedule and task history        Airflow
asset lineage and materializing  Dagster
durable application state        Temporal
visual integration wiring        n8n
```

That answer usually matters more than whether a tool can technically call
Python, run a cron schedule, or show logs.

## Scheduling Is Not The Whole Story

Scheduling says when work should start. Workflow ownership says what state must
be preserved, what can be retried, what humans inspect, and how a failed run is
recovered.

Airflow's scheduler watches DAG definitions and creates DAG runs. It tracks task
instances, dependencies, retries, logs, and run state. That makes it a good
control plane for time-based or dependency-based jobs, but it does not turn the
scheduler into a data model or an application runtime. Heavy compute should run
in the systems designed for compute; Airflow should coordinate it.

Dagster can schedule work too, but scheduling is not its center. The asset
catalog is. Dagster wants to know that an asset exists because a particular
definition materialized it from particular upstreams with particular metadata.
That asset history is the operator interface.

Temporal is not a schedule-first system. A workflow can have timers and can wait
for a very long time, but the point is durable progress through application
logic. A workflow execution stores each decision and result in history so a
worker can replay and continue from the same logical state.

n8n has schedules and webhooks, but it is best treated as integration glue. Its
strength is the speed of connecting systems and inspecting simple execution
paths. If the visual workflow becomes a critical data platform or application
runtime, consider whether the source of truth belongs in code instead.

## State And History

The most important operational difference is where each tool keeps the durable
truth.

Airflow's truth is DAG code plus metadata. The database records DAG runs, task
instances, states, retries, and other scheduler metadata. Logs may have a
separate storage story. In this repo, the Airflow stack includes a tiny
`homelab_smoke` DAG mounted from a ConfigMap to prove DAG parsing and execution.
That is a test fixture, not a long-term pattern for serious DAG libraries. Real
DAG code should have a versioned source of truth.

Dagster's truth is definitions plus event history. Asset definitions, resources,
and user code describe what can be materialized. The run/event storage records
materializations, run state, metadata, and observations. In this repo, the
Dagster stack has a small `homelab_smoke` user-code deployment. That proves the
webserver, daemon, database, user-code loading, and Kubernetes run launcher path.
Real assets should live in a packaged user-code project with tests and reviewed
dependencies.

Temporal's truth is workflow event history. The server records workflow starts,
activity scheduling, completions, failures, timers, signals, retries, and state
transitions. Workers contain the application logic, and workflow code must remain
deterministic because it can replay from history. The database and visibility
store matter because they are the durable record of what each workflow execution
has done. Archival and retention settings matter for long-lived systems.

n8n's truth is workflow definitions, credentials, execution history, database
state, and local app state. The visual editor makes the workflow easy to change,
which is useful and risky. Important automations should have enough exported or
written context that the UI is not the only explanation of what exists. Be
careful with exports because they can reveal endpoint names, workflow structure,
and credential references.

Backups, migrations, and incident response should preserve the right truth. A
copy of a UI screenshot is not a backup. A deployed Helm release is not enough
if the workflow definitions, user-code image, database, PVC, or event history are
missing.

## Retries And Side Effects

Retries are only safe when the work being retried is designed for it.

Airflow tasks should be idempotent when possible. If a task writes a table,
uploads an object, submits a job, or calls an external API, decide what happens
when that task runs twice. Prefer deterministic output locations, transactional
writes, explicit run IDs, and checks that distinguish "already done" from
"failed halfway."

Dagster asset materializations should make the asset state clear. If an asset is
partitioned, the partition should be explicit. If a materialization partially
writes data, the asset code should avoid leaving ambiguous output behind. Asset
metadata is useful because it lets humans verify what was produced, not just
whether Python returned.

Temporal workflows separate orchestration from side effects. Workflow code
coordinates; activities usually perform external side effects. Activity retry
policies, idempotency keys, workflow IDs, task queues, and signal handling are
part of application design, not deployment details. A workflow that retries a
payment, ticket creation, or provisioning call needs explicit duplicate
handling.

n8n workflows often call APIs with user-managed credentials. A retried node may
post the same message twice, create duplicate records, or send repeated
notifications. Build small automations with the same care as code: name them
clearly, record ownership, and know how to replay or stop them.

## Testing Real Runs

Do not stop at "the UI opens." A reachable UI proves routing and login more than
it proves the workflow system.

For Airflow, trigger the smoke DAG and inspect the task log and DAG run history:

```bash
cd pulumi/data/workflow/airflow

pulumi stack output --stack mx hostname
pulumi stack output --stack mx smokeDagId
```

In the UI, trigger `homelab_smoke`. A useful result is: the DAG appears, a
manual run starts, the task runs, logs are written, the task succeeds, and run
history updates. If the DAG is missing, start with DAG processor logs. If the
task never starts, start with scheduler and executor state.

For Dagster, materialize the smoke asset and inspect the run:

```bash
cd pulumi/data/workflow/dagster

pulumi stack output --stack mx url
pulumi stack output --stack mx userCodeDeployment
```

A useful result is: the code location loads, the `homelab_smoke` asset appears,
a materialization run launches, the run completes, and materialization history
records the event. If the UI loads but no assets appear, start with user-code
logs. If assets appear but runs do not launch, inspect the daemon and run
launcher path.

For Temporal, test with an actual client and worker when one exists. The server
can be healthy while an application workflow does nothing because no worker is
polling the task queue.

A useful Temporal test records:

```text
namespace
workflow ID
run ID
task queue
worker deployment
last event in history
```

Start a tiny workflow, confirm a worker polls the intended task queue, watch the
workflow history advance, and inspect the web UI. If a workflow starts and then
waits forever, check the task queue and worker deployment before changing the
Temporal server stack.

For n8n, run the automation path that matters. A manual editor run is enough for
a simple non-webhook workflow. A webhook workflow should be tested through the
real externally reachable URL, because the interesting failure may be ingress,
callback shape, or the caller's network path.

A useful n8n result is: the workflow receives the trigger, transforms the
payload as expected, calls the destination successfully, records execution
history, and behaves acceptably on retry.

## Inspecting The Stacks

Use the stack pages for exact current defaults and stack-specific notes:

```text
Airflow   pulumi/data/workflow/airflow
Dagster   pulumi/data/workflow/dagster
Temporal  pulumi/data/workflow/temporal
n8n       pulumi/data/workflow/n8n
```

Preview changes without applying them:

```bash
just preview pulumi/data/workflow/airflow stack=mx
just preview pulumi/data/workflow/dagster stack=mx
just preview pulumi/data/workflow/temporal stack=mx
just preview pulumi/data/workflow/n8n stack=mx
```

Inspect the live Kubernetes surface:

```bash
kubectl get pods,svc,ingress,pvc -A | rg 'airflow|dagster|temporal|n8n'
kubectl get endpoints -A | rg 'airflow|dagster|temporal|n8n'
```

For focused debugging, work in the target namespace and separate the layers:

```text
definition layer    DAGs, assets, workflows, nodes, user code
execution layer     scheduler, daemon, workers, task queues, run launcher
state layer         PostgreSQL, event history, metadata DB, PVCs
access layer        Service, ingress, Tailscale exposure, UI route
external layer      APIs, credentials, object stores, databases, callers
```

A pod can be ready while user code fails to import. A Service can have endpoints
while the application cannot reach PostgreSQL. A UI can load while no worker can
execute useful work. A workflow can retry correctly while the external system it
calls rejects every request.

## Change Discipline

Treat these stacks as stateful systems. Chart upgrades, image upgrades, database
migrations, task queue names, release names, hostnames, archival paths, PVCs,
and generated secrets are durable operational choices.

For code changes, run the repo checks that match the scope:

```bash
just sync pulumi/data/workflow/<stack>
just check-python
just lint
git diff --check
just preview pulumi/data/workflow/<stack> stack=mx
```

After an apply, test the real workflow path. Trigger a DAG, materialize an
asset, start a Temporal workflow with a worker, or run the n8n webhook/manual
execution. The deployment is only useful once the thing it orchestrates has
actually run.
