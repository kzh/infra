# Airflow

Source: `pulumi/data/workflow/airflow`

Airflow is the workflow control plane for this repo. Its core job is not to do
all of the compute itself; its job is to remember what workflows exist, decide
when they should run, track each task's state, retry the parts that are allowed
to retry, and make the history inspectable later.

The useful first-principles model is:

```text
DAG code defines the graph.
DAG parsing turns Python files into known workflow definitions.
The scheduler creates DAG runs and decides which tasks are ready.
The executor runs ready tasks.
The metadata database is the source of truth for state.
The API/UI lets humans and tools inspect and trigger work.
Logs explain what happened inside each task.
```

That separation matters when operating the stack. A reachable UI means the
front door is alive. It does not prove that DAGs parse, that the scheduler is
making progress, that tasks can execute, or that logs are useful. The built-in
smoke DAG exists to prove the full loop.

## What This Stack Deploys

The Pulumi project creates an Airflow 3 deployment in the `airflow` namespace
using the Apache Airflow Helm chart.

Important defaults from `__main__.py` and `Pulumi.mx.yaml`:

```text
Namespace:             airflow
Helm release name:     airflow
Chart repository:      https://airflow.apache.org
Chart version:         1.21.0
Airflow version:       3.2.0
Executor:              LocalExecutor
Ingress class:         tailscale
Hostname output:       airflow
Admin username:        admin
Smoke DAG id:          homelab_smoke
Smoke DAG mount:       /opt/airflow/dags/homelab_smoke.py
Metadata database:     PostgreSQL database named airflow
Postgres source:       stack reference kzh/postgresql/mx
Storage class:         local-path
Triggerer storage:     5Gi
Task log persistence:  disabled
Metrics path:          StatsD -> ServiceMonitor -> Prometheus
Dashboard asset:       dashboards/airflow-overview.json
```

The stack does not use the chart-managed PostgreSQL or Redis services. Pulumi
creates an Airflow database role and database in the shared PostgreSQL stack,
generates Airflow secrets, mounts the smoke DAG from a ConfigMap, creates a
Tailscale-backed ingress, creates a StatsD `ServiceMonitor`, and publishes an
Airflow Grafana dashboard ConfigMap.

The chart's old `webserver` component is disabled. In this Airflow 3 shape, the
API server is the front door for the API and UI. That is why most UI and health
debugging starts with the `airflow-api-server` deployment rather than an
`airflow-webserver` deployment.

## Component Mental Model

Think about Airflow as a few cooperating services sharing one metadata database.

The API server is the human and automation entrypoint. It serves the UI/API,
handles login, reads DAG and run state from the metadata database, and exposes
health information. If the URL fails, login is broken, or the UI shows stale
state, start with the API server logs, Service, and ingress.

The DAG processor reads DAG files, imports them as Python modules, and records
their serialized definitions. If a DAG does not appear in the UI, the DAG
processor is usually more interesting than the scheduler. Python import errors,
missing packages, syntax errors, top-level side effects, and slow DAG parsing
all show up here.

The scheduler is the decision-maker. It looks at known DAG definitions,
schedules DAG runs, evaluates dependencies, moves task instances through states,
and hands runnable tasks to the executor. If the DAG appears but tasks never
start, the scheduler is the first place to inspect.

The executor is the task-running mechanism. This stack uses `LocalExecutor`.
That means there is no Celery worker pool, no Redis broker, and no
KubernetesExecutor task pod path. Ready tasks run as local Airflow task
processes in the Airflow deployment rather than being distributed to separate
worker services. In this repo, `allowPodLaunching` and `allowJobLaunching` are
disabled, which reinforces that this stack is not currently configured to
launch arbitrary Kubernetes Pods or Jobs for tasks.

The triggerer handles deferrable operators. Deferrable operators can pause
while waiting for external events without occupying a worker slot the whole
time. This stack enables the triggerer and gives it a small persistent volume.
If a deferrable task stalls, include the triggerer in the debug path.

The metadata database is Airflow's memory. DAG runs, task instances,
connections, variables, users, pools, permissions, and scheduler heartbeats all
flow through it. In this repo it is an external PostgreSQL database managed via
the `kzh/postgresql/mx` stack reference, not a throwaway chart subdependency.
Database connectivity or migration problems can make every other component look
broken, so check migration jobs and database-related logs before changing
unrelated Helm values.

## How A Run Moves Through The System

For a manual run of `homelab_smoke`, the path is:

```text
You trigger the DAG in the UI/API.
Airflow creates a DAG run row in the metadata database.
The scheduler sees the run and finds ready task instances.
LocalExecutor starts the ready task process.
The task writes state and logs.
The scheduler marks the task and DAG run complete.
The API server reads the final state back for the UI.
```

For a scheduled DAG, the scheduler creates DAG runs from the DAG's timetable.
Airflow schedules around logical dates and data intervals, not just "run this
file now." A daily DAG usually runs after the interval it represents has closed.
For example, a run representing Monday's data may be created on Tuesday. That
model is what makes backfills and catchup possible.

The important fields in a DAG definition are:

```python
@dag(
    dag_id="example",
    schedule="@daily",
    start_date=datetime(2026, 1, 1),
    catchup=False,
)
def example():
    ...
```

`dag_id` is the stable name Airflow stores in the metadata DB. Rename it only
when you are comfortable creating a new workflow identity.

`schedule` controls automatic DAG run creation. `schedule=None` means manual
only. Cron strings, presets such as `@daily`, and timetable objects create
scheduled runs.

`start_date` is the first logical date Airflow considers. It is not simply "the
time this code was deployed." A bad `start_date` is a common reason a DAG seems
inactive.

`catchup=False` tells Airflow not to automatically create every missed interval
between `start_date` and now. This is often the safer default for homelab and
ops workflows. Use catchup intentionally for data pipelines where historical
intervals matter.

Retries are normal Airflow behavior. Write task code so a retry is safe:
deterministic output paths, transactional writes where possible, external calls
that tolerate duplicates, and task boundaries that make partial failure easy to
reason about.

## The Smoke DAG

Pulumi creates a ConfigMap named `airflow-smoke-dag` and mounts one file:

```text
/opt/airflow/dags/homelab_smoke.py
```

The DAG is intentionally tiny:

```python
from __future__ import annotations

from datetime import datetime

from airflow.decorators import dag, task


@dag(
    dag_id="homelab_smoke",
    schedule=None,
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["homelab"],
)
def homelab_smoke():
    @task
    def hello():
        print("hello from Airflow on mx0")

    hello()


homelab_smoke()
```

This is not meant to be business logic. It proves the chain:

```text
DAG file mounted
Python import works
DAG processor records the DAG
UI/API can see it
manual trigger creates a run
scheduler notices the task
LocalExecutor runs the task
task logs are written
metadata state updates to success
```

When changing Airflow itself, use this DAG as the first validation target. A
successful `homelab_smoke` run is stronger evidence than healthy Pods alone.

## Access And Outputs

Start in the project directory:

```bash
cd pulumi/data/workflow/airflow
```

Useful non-secret outputs:

```bash
pulumi stack output --stack mx namespace
pulumi stack output --stack mx hostname
pulumi stack output --stack mx smokeDagId
pulumi stack output --stack mx adminUsername
pulumi stack output --stack mx releaseName
pulumi stack output --stack mx chartVersion
pulumi stack output --stack mx airflowVersion
```

Retrieve the admin password only when needed:

```bash
pulumi stack output --stack mx --show-secrets adminPassword
```

Do not paste that value into docs, commit messages, screenshots, issue text, or
long-lived notes. The command is useful because the password is a stack output;
the value itself should stay local to the operator retrieving it.

The public URL is the Tailscale HTTPS hostname from the ingress. The stack
exports the short hostname, and the Tailscale ingress controller publishes the
tailnet FQDN in live Kubernetes status.

```bash
NS="$(pulumi stack output --stack mx namespace)"

kubectl get ingress -n "$NS"
kubectl get ingress -n "$NS" airflow-ingress \
  -o jsonpath='{.status.loadBalancer.ingress[0].hostname}{"\n"}'
```

Open the `https://...` hostname shown by the ingress and log in with the admin
username plus the password output above.

## Operating The Smoke DAG

From the UI, find `homelab_smoke`, trigger it manually, and inspect the run.

The expected result is:

```text
The DAG appears in the DAG list.
A manual trigger creates a DAG run.
The `hello` task moves into a running state.
The task succeeds.
The task log includes the smoke message.
The DAG run becomes successful.
```

The same validation can be done from inside an Airflow component:

```bash
cd pulumi/data/workflow/airflow
NS="$(pulumi stack output --stack mx namespace)"

kubectl -n "$NS" exec deploy/airflow-scheduler -- airflow dags list
kubectl -n "$NS" exec deploy/airflow-scheduler -- airflow dags list-import-errors
kubectl -n "$NS" exec deploy/airflow-scheduler -- airflow dags trigger homelab_smoke
```

If the DAG is missing, look at DAG processor logs before changing the scheduler.
If the DAG exists but the task stays queued or scheduled, look at scheduler and
executor behavior. If the task succeeds but logs are unavailable, jump to the
logging section below.

## DAG Authoring In This Repo

The current stack uses a ConfigMap-mounted DAG for the smoke path. That is a
good pattern for a single validation DAG because the code is small, reviewed
with the infrastructure, and deployed by Pulumi.

For real workflows, choose a deliberate deploy path instead of adding ad hoc UI
state:

```text
Small repo-owned smoke or ops DAG:
  Keep it near the Airflow Pulumi project and mount it through chart values.

Larger DAG library:
  Use a reviewed repo-backed sync path or build DAGs into a custom Airflow image.

DAGs with extra Python dependencies:
  Put dependencies in the Airflow runtime image or supported DAG packaging path,
  not in the Pulumi project's infra-only pyproject.toml unless Pulumi itself
  imports them.

DAGs that call other services:
  Store endpoints and credentials in Airflow connections, variables, or
  Kubernetes/Pulumi-backed secrets. Do not hard-code secret values in DAG files.
```

The key property is reproducibility. A DAG that matters should have a versioned
source of truth and a deploy path that can be reviewed, previewed, and rolled
forward. Creating important connections, variables, or DAG code by clicking in
the UI is fine for exploration, but it should not be the only record of how the
workflow works.

When writing a DAG, keep top-level Python cheap. Airflow imports DAG files
repeatedly. Network calls, database queries, Spark submissions, large local file
reads, and dynamic package installation should not happen at import time. Put
work inside tasks.

Good task boundaries are boring in the best way:

```text
read input manifest
submit Spark job
wait for completion
validate row counts
publish output marker
send notification
```

Avoid one task that does everything. Airflow is most useful when the graph tells
you what failed and what can be retried.

## Connections And Variables

Airflow connections and variables live in the metadata database. They can be
created through the UI, the Airflow CLI, environment variables, or chart values.
Sensitive connection fields are encrypted using the Airflow Fernet key managed
by this stack.

Use connections for service credentials and endpoints:

```text
PostgreSQL databases
HTTP APIs
S3-compatible object storage
Spark or Trino endpoints
Webhook destinations
```

Use variables for small runtime settings:

```text
default dataset name
notification channel
feature flag for a DAG branch
non-secret path or bucket name
```

Do not use variables as a secret store just because they are convenient. If a
value is sensitive, prefer a connection, a Kubernetes Secret, a Pulumi secret
config value, or another secret-backed integration. Do not put secret values in
`Pulumi.mx.yaml`, DAG source files, or committed examples.

Low-risk inspection commands:

```bash
cd pulumi/data/workflow/airflow
NS="$(pulumi stack output --stack mx namespace)"

kubectl -n "$NS" exec deploy/airflow-scheduler -- airflow connections list
```

Be careful with commands that print full connection URIs or variable values.
For example, variable inspection can reveal values even when the variable name
looks harmless. Those commands are useful during private operations, but their
output does not belong in repo docs or shared logs.

For durable repo-managed connection injection, prefer chart values that refer to
Kubernetes Secrets or Pulumi secret outputs rather than inline plaintext. Airflow
also supports environment naming conventions such as `AIRFLOW_CONN_<CONN_ID>`
and `AIRFLOW_VAR_<KEY>`, but those should still be backed by safe secret
plumbing when the value is sensitive.

## Logs

This stack explicitly sets:

```text
logs.persistence.enabled = false
```

That choice avoided storage problems during first deployment, but it has an
operational consequence: task logs are not durable across all Pod restarts and
reschedules. Treat UI task logs as useful for recent runs, not as an audit log.

For a fresh task failure, first use the UI task log. It is usually the most
direct explanation because it shows the task's own stdout/stderr and Airflow
context.

If the UI log is missing or incomplete, inspect component logs:

```bash
cd pulumi/data/workflow/airflow
NS="$(pulumi stack output --stack mx namespace)"

kubectl logs -n "$NS" deploy/airflow-dag-processor --tail=200
kubectl logs -n "$NS" deploy/airflow-scheduler --tail=200
kubectl logs -n "$NS" deploy/airflow-api-server --tail=200
kubectl logs -n "$NS" deploy/airflow-triggerer --tail=200
```

For deployment and migration issues, inspect Jobs too:

```bash
kubectl get jobs -n "$NS"
kubectl logs -n "$NS" job/airflow-run-airflow-migrations --tail=200
kubectl logs -n "$NS" job/airflow-create-user --tail=200
```

If a future change needs durable task logs, do not just flip on persistence and
hope the chart makes it work. Airflow log storage needs to match the executor
and storage backend. With `local-path`, shared ReadWriteMany-style log access is
not something to assume. A more durable design may be remote logging to object
storage, a storage class with the right access mode, or an executor-specific log
strategy. Preview the chart diff and test with `homelab_smoke` before trusting
historical logs.

## Metrics And Dashboard

The chart enables StatsD. Pulumi creates a `ServiceMonitor` that selects the
Airflow StatsD service:

```text
tier=airflow
component=statsd
release=airflow
```

The `ServiceMonitor` gets the monitoring release label from config, defaulting
to `kube-prometheus-stack`, so the Prometheus stack can discover it. Pulumi also
loads `dashboards/airflow-overview.json` into a Grafana dashboard ConfigMap
labeled with `grafana_dashboard=1`.

If metrics or the dashboard look empty:

```bash
cd pulumi/data/workflow/airflow
NS="$(pulumi stack output --stack mx namespace)"

kubectl get svc -n "$NS" -l component=statsd
kubectl get servicemonitor -n "$NS" airflow-statsd -o yaml
kubectl get configmap -n "$NS" -l grafana_dashboard=1,app=airflow
```

Check selectors and labels before editing dashboard JSON. An empty dashboard is
often a scrape discovery issue rather than a visualization issue.

## Debugging By Symptom

Start every debug pass by collecting the live shape:

```bash
cd pulumi/data/workflow/airflow
NS="$(pulumi stack output --stack mx namespace)"

kubectl get pods,svc,ingress,jobs,pvc,configmap -n "$NS"
kubectl get events -n "$NS" --sort-by=.lastTimestamp
```

Then narrow by symptom.

The URL does not load:

```bash
kubectl get ingress -n "$NS" airflow-ingress -o wide
kubectl describe ingress -n "$NS" airflow-ingress
kubectl get svc -n "$NS" airflow-api-server -o wide
kubectl get endpoints -n "$NS" airflow-api-server
kubectl logs -n "$NS" deploy/airflow-api-server --tail=200
```

Healthy API server Pods do not guarantee a working public URL. Check the
Service endpoints and ingress status so you can tell whether the issue is
Airflow, Kubernetes Service selection, or Tailscale ingress.

The UI loads but the DAG is missing:

```bash
kubectl logs -n "$NS" deploy/airflow-dag-processor --tail=300
kubectl -n "$NS" exec deploy/airflow-scheduler -- airflow dags list
kubectl -n "$NS" exec deploy/airflow-scheduler -- airflow dags list-import-errors
kubectl get configmap -n "$NS" airflow-smoke-dag -o yaml
```

This usually means a parse/import problem, a missing mount, or DAG code that is
too slow or side-effectful at import time.

The DAG appears but tasks do not run:

```bash
kubectl logs -n "$NS" deploy/airflow-scheduler --tail=300
kubectl -n "$NS" exec deploy/airflow-api-server -- \
  python -c 'import urllib.request; print(urllib.request.urlopen("http://localhost:8080/api/v2/monitor/health").read().decode())'
kubectl get pods -n "$NS" -o wide
```

For this stack, remember that `LocalExecutor` does not have separate Celery
workers. Looking for Redis or worker Deployments will lead you away from the
actual issue unless the executor has been changed.

Tasks fail immediately:

```bash
kubectl logs -n "$NS" deploy/airflow-scheduler --tail=300
kubectl logs -n "$NS" deploy/airflow-dag-processor --tail=300
kubectl describe pods -n "$NS" -l component=scheduler
```

Read the task log in the UI first when available. Then look for missing Python
packages, missing Airflow connections, permission errors, bad paths, or a task
that performs work at DAG import time instead of inside the task body.

Database migrations or startup fail:

```bash
kubectl get jobs -n "$NS"
kubectl logs -n "$NS" job/airflow-run-airflow-migrations --tail=300
kubectl logs -n "$NS" deploy/airflow-api-server --tail=300
kubectl logs -n "$NS" deploy/airflow-scheduler --tail=300
```

The metadata DB comes from the shared PostgreSQL stack. Avoid printing database
passwords while debugging. If credentials or host values need inspection, keep
that local and summarize the result without exposing values.

The health endpoint is useful for separating front-door issues from subsystem
issues:

```bash
kubectl -n "$NS" exec deploy/airflow-api-server -- \
  python -c 'import urllib.request; print(urllib.request.urlopen("http://localhost:8080/api/v2/monitor/health").read().decode())'
```

Look for metadatabase, scheduler, triggerer, and DAG processor health in the
returned JSON.

## Upgrades

Treat Airflow upgrades as migrations. The Helm chart can change Kubernetes
objects, default values, migration Jobs, component names, security context,
logging behavior, executor behavior, and the Airflow application version.

Before changing `airflow:chartVersion`, inspect the chart you are moving to:

```bash
helm show chart airflow --repo https://airflow.apache.org --version <version>
helm show values airflow --repo https://airflow.apache.org --version <version>
```

Read the Apache Airflow release notes for the app version included by the chart.
Database migrations may be one-way in practice, so know what backup or rollback
story you have before applying.

Be especially careful with:

```text
release name
namespace
metadata database name and user
Fernet key
API/JWT secret keys
executor
log persistence
DAG delivery method
Helm hook behavior
chart values removed or renamed upstream
```

The current stack pins the Helm release name to `airflow` and sets
`delete_before_replace=True` on the Helm release. That was chosen because failed
first installs can leave Helm-owned resources behind. Do not casually remove it
or rename the release; both choices affect how Pulumi and Helm reconcile live
objects.

The current stack also sets:

```text
createUserJob.useHelmHooks = false
migrateDatabaseJob.useHelmHooks = false
```

Those settings avoid Helm hook behavior that complicated first install. If a
chart upgrade changes how these jobs work, understand the new behavior before
reverting to chart defaults.

If switching away from `LocalExecutor`, design the rest of the system at the
same time. Celery needs a broker and worker services. KubernetesExecutor needs
Pod launching permissions and log handling. More concurrency may need database,
CPU, memory, and pool changes. Executor changes are architecture changes, not
just string changes.

## Safe Change Workflow

This is live infrastructure. Make changes in small, reviewable steps and let
Pulumi show the live diff before applying.

For ordinary code or config changes:

```bash
git status --short
just sync pulumi/data/workflow/airflow
just check-python
just lint
git diff --check
just preview pulumi/data/workflow/airflow stack=mx
```

Do not run `pulumi up`, `pulumi destroy`, or `just up` unless the current task
explicitly asks for an apply or destructive action.

When reviewing a preview, separate the cause:

```text
expected chart diff
invalid chart value
missing config
bad ESC/reference value
live cluster drift
provider behavior
real program bug
```

Do not fix a preview by changing unrelated values until you know which category
you are in.

Keep Pulumi resource names and Kubernetes `metadata.name` values stable unless
you are intentionally migrating state. Renaming `airflow`, the namespace, the
database, the role, or the smoke DAG changes identity and can force replacement
or split Airflow history.

Pass Pulumi outputs directly as resource inputs when possible. Use `apply` only
to transform values, as the Fernet key generation does. Do not create resources
inside `apply` callbacks.

Keep secrets secret:

```text
Use generated secrets or Pulumi secret config.
Do not hard-code credentials in DAG files or Pulumi YAML.
Do not paste secret stack outputs into docs.
Do not print full connection URIs in shared logs.
Review diffs before committing.
```

After any applied change, validate behavior rather than stopping at a successful
deployment:

```bash
cd pulumi/data/workflow/airflow
NS="$(pulumi stack output --stack mx namespace)"

kubectl rollout status -n "$NS" deploy/airflow-api-server
kubectl rollout status -n "$NS" deploy/airflow-dag-processor
kubectl rollout status -n "$NS" deploy/airflow-scheduler
kubectl rollout status -n "$NS" deploy/airflow-triggerer
kubectl -n "$NS" exec deploy/airflow-scheduler -- airflow dags list-import-errors
kubectl -n "$NS" exec deploy/airflow-scheduler -- airflow dags list
```

Then trigger `homelab_smoke` and confirm the run completes. That is the repo's
smallest end-to-end proof that Airflow is still useful, not merely installed.
