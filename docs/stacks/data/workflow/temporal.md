# Temporal

Source: `pulumi/data/workflow/temporal`

Temporal is a durable workflow runtime for application code. The basic idea is
simple but powerful: an application asks Temporal to start a workflow, Temporal
records every important event in that workflow's history, and worker processes
replay that history whenever they need to continue the work. If a worker dies,
is redeployed, or loses local memory, the workflow does not have to start over.
Another worker can read the recorded history and resume from the same logical
state.

That makes Temporal different from the schedule-first tools in this directory.
Airflow is a scheduler for DAGs. Dagster is an asset orchestrator. Temporal is
closer to an event-sourced application runtime. Use it when the thing being
coordinated is a business or application process that needs durable timers,
retries, signals, human steps, long waits, and crash recovery.

Temporal is not a replacement for good application design. It gives you a
durable control plane for workflows, but your activities still call real
systems, write real rows, send real messages, and create real side effects.
Workflow IDs, task queue names, retry policies, idempotency, and worker deploys
are part of the design, not details to bolt on later.

## What This Stack Deploys

The Pulumi project creates the Kubernetes surface and installs the official
Temporal Helm chart. It does not contain application workflow code and it does
not deploy application workers.

The stack reads the shared PostgreSQL stack through a `StackReference`, creates
the Kubernetes namespace configured for this project, creates a PVC for
file-backed archival, writes the PostgreSQL password into a Kubernetes Secret,
and installs the `temporal` chart from
`https://temporalio.github.io/helm-charts`.

The current repo wiring is:

```text
Pulumi project:              pulumi/data/workflow/temporal
Python runtime:              Python 3.12 through uv
Pulumi packages:             pulumi, pulumi-kubernetes
Helm chart:                  temporal
Helm chart version:          1.2.0
Chart appVersion:            1.31.0
Chart repository:            https://temporalio.github.io/helm-charts
Required config:             namespace, postgres_stack
PostgreSQL outputs read:     rw_service_fqdn, port, username, password
Default database:            temporal
Visibility database:         temporal_visibility
Database Secret:             temporal-db-credentials
Secret key used by chart:    password
Archival PVC:                temporal-archival
Archival PVC size:           20Gi
Archival access mode:        ReadWriteOnce
Archival mount path:         /var/temporal-archive
History archive URI:         file:///var/temporal-archive/history
Visibility archive URI:      file:///var/temporal-archive/visibility
Frontend Tailscale hostname: temporal-frontend
Web ingress class:           tailscale
Web ingress TLS host:        temporal
Schema Helm hooks:           disabled
Compatibility shims:         disabled
```

Several of those values are configurable in Pulumi code. `default_db`,
`visibility_db`, `archival_pvc_name`, `archival_storage_size`, and
`archival_mount_path` have code defaults. `namespace` and `postgres_stack` are
required config. The database password is not hard-coded; it comes from the
PostgreSQL stack output and is passed into the chart through the Kubernetes
Secret.

The chart's server components run with `server.replicaCount: 1`. The stack
enables SQL persistence for both the default workflow store and the visibility
store, sets `createDatabase: true`, sets `manageSchema: true`, and uses the
`postgres12` plugin/driver for both stores.

The stack also turns on Temporal namespace creation in chart config, but the
Python program does not define custom Temporal namespace names. With the pinned
chart, that leaves the chart's namespace defaults in control unless the chart
values are changed deliberately.

## What This Stack Does Not Deploy

This stack is the Temporal server foundation. It does not deploy the workers
that execute your application workflows and activities. That distinction is the
source of many confusing incidents.

Temporal has three application-facing actors:

```text
client       starts workflows, sends signals, queries state, waits for results
server       stores history, routes tasks, manages timers, persists visibility
worker       runs workflow code and activity code for specific task queues
```

The chart deploys Temporal's own server-side components. The application worker
is separate. It usually lives in the application stack that owns the business
logic, not in `pulumi/data/workflow/temporal`.

If the Temporal UI is reachable and the server pods are healthy, an application
workflow can still sit forever if no worker is polling the task queue that the
client used. That is not automatically a Temporal server outage. First check
whether the right worker deployment is alive, configured for the right Temporal
namespace, and polling the exact task queue.

## Durable Workflow Mental Model

A workflow execution is not just a function call. It is a durable conversation
between workflow code, Temporal, workers, timers, signals, activities, and
external systems.

A normal flow looks like this:

```text
client asks frontend to start a workflow
Temporal records WorkflowExecutionStarted
matching places workflow tasks on the requested task queue
a worker polls that task queue
the worker runs deterministic workflow code
the workflow schedules activities, timers, child workflows, or waits for signals
Temporal records each decision and result in event history
the workflow completes, fails, continues as new, waits, retries, or is canceled
```

The workflow history is the durable truth. It records events such as workflow
start, task scheduling, activity completion, timer firing, signal receipt,
retry, failure, cancellation, and completion. Workers use that history to
replay workflow code and reconstruct state.

Replay is why workflow code must be deterministic. If the workflow reads the
current time directly, generates random values directly, iterates over an
unordered external result, or performs side effects during replay, the code can
make a different decision from the one already recorded in history. Temporal
SDKs provide workflow-safe APIs for time, sleep, cancellation, signals, and
other deterministic operations. Use those APIs inside workflow code.

Activities are where most side effects belong. An activity can call an HTTP
API, write to a database, send email, submit a Spark job, create a ticket, or
talk to another service. Activities can be retried, so they should be designed
with duplicate handling in mind. Use idempotency keys, deterministic output
locations, transaction boundaries, or "already done" checks for anything that
would be expensive or harmful to do twice.

The clean mental split is:

```text
workflow code    deterministic orchestration, timers, signals, retries
activity code    side effects, external calls, database writes, compute calls
Temporal server  event history, task routing, timers, visibility, persistence
workers          your deployed application code polling task queues
```

## Namespaces, Task Queues, And Workers

Temporal namespaces group workflow executions. They are the top-level
application boundary for workflow IDs, retention, visibility, and many
operational commands. The Temporal CLI defaults to the `default` namespace, and
the pinned chart has a default namespace configuration unless the chart values
are changed.

Task queues connect work to workers. A client starts a workflow with a task
queue name. Workers poll one or more task queues. Matching hands workflow tasks
and activity tasks to workers that are polling those queues.

The task queue string is an application contract. `image-imports`,
`image_imports`, and `ImageImports` are different queues. A worker polling the
wrong name is invisible to the workflow that needs it. A worker polling the
right name but using old code can be just as confusing, because the server may
be healthy while the task keeps failing or replaying.

For every real Temporal application, keep these values easy to find:

```text
Temporal namespace
frontend address used by clients
workflow type names
workflow ID convention
task queue names
worker deployment name
worker image/version
activity retry policies
workflow timeout policy
signal and query names
```

When someone reports that a workflow is stuck, ask for:

```text
namespace
workflow ID
run ID
task queue
worker deployment
last event in workflow history
whether task queue pollers are present
```

Without that shape, you can only guess whether the issue is ingress, frontend,
matching, worker deployment, application code, replay, an activity dependency,
or persistence.

## Server Components

The official chart deploys multiple Temporal server roles. Component names
matter during debugging because each role answers a different question.

The frontend is the API edge. SDK clients, workers, and CLI commands talk to
the frontend. In this repo, the frontend Service is annotated for Tailscale
exposure with hostname `temporal-frontend`. The chart's frontend gRPC port is
`7233`; the chart also defines a frontend HTTP port. Client and worker routing
should be verified from the live Service because Service names and labels are
chart-rendered resources.

History owns workflow histories and state transitions. If histories cannot be
loaded, persisted, or advanced, workflows cannot make durable progress. Broad
SQL errors across components often show up here.

Matching owns task dispatch. If workflow histories are advancing to scheduled
tasks but workers are not receiving anything, matching and task queue pollers
are where to look.

The chart's worker component runs Temporal internal background work. It is not
your application worker. Application workers live outside this server chart.

The web component is the Temporal UI. It is a human inspection surface for
workflow executions and namespaces. The web UI being reachable does not prove
the frontend gRPC path is reachable by workers, and it does not prove any
application worker is polling.

The admintools component is useful for operational CLI access from inside the
cluster when it is enabled by the chart defaults. Prefer checking the live
Deployment/Pod names before assuming an exact resource name.

The schema jobs manage database initialization and upgrades. This repo sets
`schema.useHelmHooks: false`, so schema work is represented in the Kubernetes
resources Pulumi manages rather than being hidden behind Helm hook behavior.
Schema changes are still stateful database changes and should be treated with
care.

## Persistence And Archival

Temporal stores the default workflow history store and the visibility store in
PostgreSQL. This stack consumes the shared PostgreSQL stack instead of deploying
a chart-managed database.

The `postgres_stack` config value points to the producer stack. The consumer
reads:

```text
rw_service_fqdn  PostgreSQL read-write service host
port             PostgreSQL port
username         PostgreSQL user
password         PostgreSQL password
```

The Python program formats `rw_service_fqdn` and `port` into `host:port`, passes
the username into chart values, and stores the password in the
`temporal-db-credentials` Secret. The Helm chart reads the key named `password`
from that Secret for both the default and visibility SQL stores.

The default database name is `temporal`. The visibility database name is
`temporal_visibility`. Both are passed as chart values with database creation
and schema management enabled. The visibility store is what makes workflow
listing and search-like UI/CLI views work. The default store is the core
workflow history and state store.

The pinned chart also has server persistence defaults that are not repeated in
the Python file. One especially important default is the number of history
shards. Treat shard count as initialized database identity: once a Temporal
cluster has been created against a persistence store, changing shard count is
not a routine tuning knob.

Archival is enabled for both history and visibility. This stack uses the
filestore archival provider and mounts a PVC at `/var/temporal-archive`. The
namespace defaults point history archival to
`file:///var/temporal-archive/history` and visibility archival to
`file:///var/temporal-archive/visibility`.

That is a reasonable small-cluster pattern, but it has a specific durability
model. A PVC-backed file archive is not the same as object-store archival with
independent lifecycle controls, cross-zone redundancy, object versioning, or
separate backup policies. If workflow history is business-critical, the backup
story must cover both PostgreSQL and the archival PVC.

Do not rename the databases, Secret, PVC, archival path, or namespace casually.
Those names are operational identity. A preview that wants to replace a PVC,
delete a Secret, or recreate schema jobs deserves close reading before any
apply.

## Access Paths

There are two main access paths.

The web UI is exposed through a Tailscale ingress. The Python values enable the
ingress, set `className: tailscale`, and set a TLS host of `temporal`; they do
not override the chart's `web.ingress.hosts` rule list. Use the UI to inspect
namespaces, workflow executions, histories, statuses, and failures, but read
the exact browser route from the live Ingress instead of copying one into docs.

The frontend API is exposed through the Temporal frontend Service. This is the
path SDK clients, workers, and CLI commands use. In this repo the Service has
Tailscale annotations:

```text
tailscale.com/expose:   true
tailscale.com/hostname: temporal-frontend
```

Inside the cluster, prefer Kubernetes DNS for workers. Outside the cluster,
use the Tailscale-exposed frontend address when that is the intended route.
Always include the Temporal port, normally `7233`, in SDK and CLI addresses.

Keep UI and frontend debugging separate:

```text
UI loads, frontend fails       ingress/web is not the same as frontend gRPC
frontend works, UI fails       server may be usable while web ingress is broken
both fail                      check namespace, pods, services, endpoints, SQL
workflow starts but stalls     check task queue pollers and application workers
```

## Inspect The Live Stack

Start from the project directory so Pulumi reads the right stack config:

```bash
cd pulumi/data/workflow/temporal
NS="$(pulumi config get namespace --stack mx)"

kubectl get pods,svc,ingress,pvc,secrets,jobs -n "$NS"
kubectl get endpoints -n "$NS"
kubectl get endpointslices -n "$NS"
```

Discover labels before writing a narrow selector into an incident command:

```bash
kubectl get pods -n "$NS" --show-labels | rg temporal
kubectl get svc -n "$NS" --show-labels | rg temporal
kubectl get jobs -n "$NS" --show-labels | rg temporal
```

Then inspect by component:

```bash
kubectl logs -n "$NS" -l app.kubernetes.io/name=temporal --tail=200
kubectl describe pods -n "$NS" | rg -n 'temporal|Warning|Failed|BackOff'
kubectl describe pvc -n "$NS"
kubectl describe ingress -n "$NS"
```

If the frontend path is the question, inspect the Service and endpoints first:

```bash
kubectl get svc -n "$NS" | rg 'frontend|temporal'
kubectl get endpoints -n "$NS" | rg 'frontend|temporal'
kubectl describe svc -n "$NS" temporal-frontend
```

If that exact Service name ever changes with chart rendering, use the first two
commands to find the current frontend Service rather than editing docs or code
from memory.

If persistence is the question, check whether errors are local to one component
or across the server:

```bash
kubectl logs -n "$NS" -l app.kubernetes.io/name=temporal --tail=500 | rg -i 'sql|postgres|schema|visibility|persistence|timeout|refused'
kubectl get jobs -n "$NS" | rg 'schema|temporal'
kubectl describe jobs -n "$NS" | rg -n 'schema|temporal|Warning|Failed'
```

Do not print or copy Secret data while debugging. The existence, name, key, and
mount/reference shape are useful; the value is not useful in docs or chat.

## CLI Examples

The local Temporal CLI accepts an address and namespace on most commands. The
default namespace is `default`, but use the namespace your application actually
targets if it differs.

From a machine that can reach the Tailscale frontend route:

```bash
ADDR="temporal-frontend:7233"
TNS="default"

temporal operator namespace list --address "$ADDR"
temporal operator namespace describe --address "$ADDR" --namespace "$TNS"
temporal workflow list --address "$ADDR" --namespace "$TNS"
```

For a known workflow execution:

```bash
ADDR="temporal-frontend:7233"
TNS="default"
WORKFLOW_ID="replace-with-workflow-id"
RUN_ID="replace-with-run-id"

temporal workflow describe \
  --address "$ADDR" \
  --namespace "$TNS" \
  --workflow-id "$WORKFLOW_ID" \
  --run-id "$RUN_ID"

temporal workflow show \
  --address "$ADDR" \
  --namespace "$TNS" \
  --workflow-id "$WORKFLOW_ID" \
  --run-id "$RUN_ID"
```

For a task queue:

```bash
ADDR="temporal-frontend:7233"
TNS="default"
TASK_QUEUE="replace-with-task-queue"

temporal task-queue describe \
  --address "$ADDR" \
  --namespace "$TNS" \
  --task-queue "$TASK_QUEUE"
```

The task queue command reports recent pollers and backlog statistics. If there
are no recent pollers, the server may be healthy but no worker is available for
that queue.

Starting a workflow from the CLI only works if a worker with that workflow type
is already polling the task queue:

```bash
temporal workflow start \
  --address "$ADDR" \
  --namespace "$TNS" \
  --workflow-id "example-$(date +%Y%m%d%H%M%S)" \
  --type ExampleWorkflow \
  --task-queue "$TASK_QUEUE" \
  --input '{"name":"Temporal"}'
```

If the command starts a workflow and then the execution waits, inspect the task
queue pollers before changing the Temporal server stack.

## Client And Worker Example

Application code should connect to the frontend address, use an explicit
Temporal namespace, and choose task queues that match the deployed workers.
Inside Kubernetes, the address is usually the frontend Service DNS name plus
port `7233`. Outside Kubernetes, use the approved Tailscale frontend route.

A tiny Python shape looks like this:

```python
from datetime import timedelta

from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.worker import Worker


@activity.defn
async def compose_greeting(name: str) -> str:
    return f"hello, {name}"


@workflow.defn
class GreetingWorkflow:
    @workflow.run
    async def run(self, name: str) -> str:
        return await workflow.execute_activity(
            compose_greeting,
            name,
            start_to_close_timeout=timedelta(seconds=10),
        )


async def run_worker() -> None:
    client = await Client.connect(
        "temporal-frontend.<namespace>.svc.cluster.local:7233",
        namespace="default",
    )
    worker = Worker(
        client,
        task_queue="greetings",
        workflows=[GreetingWorkflow],
        activities=[compose_greeting],
    )
    await worker.run()


async def start_workflow() -> str:
    client = await Client.connect(
        "temporal-frontend.<namespace>.svc.cluster.local:7233",
        namespace="default",
    )
    handle = await client.start_workflow(
        GreetingWorkflow.run,
        "Temporal",
        id="greeting-example",
        task_queue="greetings",
    )
    return await handle.result()
```

That example is intentionally small. A real application should make the
frontend address, namespace, and task queue configurable. It should also define
activity retry behavior, timeouts, worker identity, shutdown behavior, and
idempotency for any external side effect.

Keep workflow implementation details in application repos or application
stacks. This Temporal stack should remain the shared server foundation unless
there is a deliberate reason to couple a specific worker to it.

## Reading Workflow History

History is the best debugging artifact Temporal gives you. A workflow history
answers questions that pod health cannot answer:

```text
did the workflow start?
which task queue did it use?
did a worker complete workflow tasks?
which activity was scheduled?
did the activity start?
did it fail, timeout, retry, or complete?
was a signal received?
did a timer fire?
did replay fail?
```

Use the UI for a human scan and the CLI for copyable, structured inspection:

```bash
temporal workflow show \
  --address "$ADDR" \
  --namespace "$TNS" \
  --workflow-id "$WORKFLOW_ID" \
  --run-id "$RUN_ID" \
  --output json
```

When a workflow appears stuck, look at the last meaningful event:

```text
WorkflowTaskScheduled       a worker needs to poll and complete workflow code
ActivityTaskScheduled       an activity worker needs to poll the activity task
ActivityTaskStarted         the worker has the task; inspect worker logs
ActivityTaskFailed          inspect failure details and retry policy
TimerStarted                the workflow may simply be waiting
WorkflowExecutionSignaled   signal arrived; workflow code may still need to act
WorkflowTaskFailed          replay, determinism, or worker code may be broken
```

Do not jump straight from "the workflow is open" to "the server is broken." The
history usually tells you which layer is responsible for the next step.

## Debugging By Symptom

If the web UI does not load, inspect the web Deployment/Pod, Service, ingress,
and Tailscale route. The UI is HTTP. The frontend API is gRPC. Fixing one path
does not necessarily fix the other.

If CLI or SDK clients cannot connect, inspect the frontend Service, endpoints,
and route. From inside the cluster, test against the Kubernetes Service. From
outside the cluster, test against the Tailscale hostname. Make sure the client
is using port `7233` and the expected TLS setting for that route.

If every server component logs PostgreSQL errors, treat it as a persistence
issue. Check the `postgres_stack` config, whether the producer stack outputs
still exist, whether the Secret exists with the expected key, whether schema
jobs ran, and whether the shared PostgreSQL stack is healthy.

If the UI lists workflows but a specific workflow never progresses, inspect the
history and task queue. Check for pollers with `temporal task-queue describe`.
Then inspect the application worker deployment logs. The server can only route
tasks to workers that exist and poll.

If activities fail repeatedly, read the activity failure in history and the
application worker logs. The problem may be an external API, database, object
store, credential, timeout, retry policy, payload shape, or an ordinary
application exception.

If workflow tasks fail during replay, suspect nondeterministic workflow code or
a worker code change that is not compatible with open histories. Use SDK replay
tests when possible before deploying worker changes that affect workflow logic.

If visibility/listing is broken but histories still run, inspect the visibility
store and schema. Temporal has separate default and visibility SQL stores in
this stack, even though both use the shared PostgreSQL producer.

If archival fails, inspect the PVC, volume mount, permissions, and archival
paths. This stack uses filestore archival on the `temporal-archival` PVC; a
database-only check does not prove archival is usable.

If a preview shows unexpected service updates, read the diff instead of
assuming it is harmless. This repo has previously surfaced Temporal live-state
drift in service labels, ports, and chart/runtime metadata during targeted
previews. Drift may be safe to reconcile, but it should be identified rather
than hidden inside an unrelated docs or code change.

## Upgrades

Temporal upgrades are stateful runtime upgrades. They are not just image bumps.
The chart version, server version, schema jobs, persistence settings, search or
visibility behavior, frontend service shape, and CLI/SDK compatibility can all
matter.

Before changing the chart version or server image behavior:

```text
read the chart changelog and Temporal server release notes
check whether schema migrations are required
check whether the chart values changed shape
check whether shims are still appropriate
check whether services, labels, or ports will change
check whether CLI and SDK versions used by applications remain compatible
preview the Temporal stack with a diff
plan a post-apply workflow/client/worker check
```

This repo currently pins chart `1.2.0`, whose chart metadata reports appVersion
`1.31.0`, and explicitly disables `shims.dockerize` and
`shims.elasticsearchTool`. Those shims existed for older image compatibility in
the chart defaults; do not turn them back on without a concrete reason from the
chart/server version you are deploying.

Schema behavior is especially important. This stack sets both SQL stores to
`manageSchema: true` and sets `schema.useHelmHooks: false`. A version change
can therefore produce schema Job changes in Pulumi preview. Read those changes
as database migration work, not as disposable Kubernetes noise.

Worker upgrades are a separate concern from server upgrades. If application
workflow code changes in a way that open histories cannot replay, existing
workflow executions can fail even when the server upgrade is perfect. Use
workflow versioning patterns, worker deployment discipline, task queue
partitioning, or replay tests for meaningful workflow code changes.

## Safe Changes In This Repo

For docs-only changes to this page, keep the edit scoped to
`docs/stacks/data/workflow/temporal.md`.

For code changes to the Temporal stack, start with the source and config
contract:

```bash
cd /Users/kevin/Code/Repos/github.com/kzh/infra

git status --short
sed -n '1,240p' pulumi/data/workflow/temporal/__main__.py
sed -n '1,120p' pulumi/data/workflow/temporal/Pulumi.yaml
```

Then use the repo checks:

```bash
just sync pulumi/data/workflow/temporal
just check-python
just lint
git diff --check
just preview pulumi/data/workflow/temporal stack=mx
```

Do not run `just up`, `pulumi up`, `pulumi destroy`, or destructive `kubectl`
commands unless the user explicitly asks for an apply or cleanup.

Treat these as durable identity:

```text
Pulumi project name
Kubernetes namespace
PostgreSQL StackReference output names
database names
database Secret name and key
Temporal chart version
history shard count
frontend Service identity and Tailscale hostname
web ingress class and TLS host
archival PVC name
archival mount path
history and visibility archive URIs
task queue names used by applications
workflow IDs used by applications
```

Changing any of those can be correct, but it should be deliberate. A rename may
force replacement. A database name change can make the server look empty or
start schema initialization in the wrong place. A task queue rename can strand
workflows until workers and clients move together. A PVC replacement can lose
the file archive unless there is a migration or backup plan.

After an apply, do not stop at healthy pods. Verify the actual work path:

```text
server pods are ready
frontend Service has endpoints
web UI opens through the intended route
Temporal namespace is visible
CLI can list or describe the namespace through the intended frontend address
an application worker is polling the expected task queue
a tiny workflow can start, advance history, and complete
the archive PVC is mounted if archival matters to the change
```

That is the difference between "Kubernetes accepted the manifests" and
"Temporal is usable for durable workflows."
