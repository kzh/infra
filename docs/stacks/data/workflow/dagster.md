# Dagster

Source: `pulumi/data/workflow/dagster`

Dagster is the workflow system in this repo for software-defined data assets.
That phrase is worth unpacking before looking at pods and chart values, because
it explains what this stack is trying to make easy.

In Airflow, the central object is usually a DAG run made of task instances. In
Dagster, the central object is usually an asset: a table, file set, model,
report, object-store prefix, feature set, or other durable data product that
your platform produces. The Python function is not merely "a task that ran."
It is the definition of how that asset is materialized, what it depends on, and
what metadata should be recorded when it changes.

That shift changes how operators reason about the system. A failed run is still
important, but the deeper questions are asset-shaped:

```text
what asset was supposed to be produced?
what upstream assets did it depend on?
was this materialization fresh, stale, partial, or missing?
what metadata did the run record?
what downstream assets are now affected?
```

Use this stack when the durable thing you care about is the data graph. If the
primary model is "run these tasks in this order on this schedule," Airflow is
usually the more natural fit. Dagster can schedule, retry, launch Kubernetes
jobs, and show run logs, but those are in service of the asset catalog and
materialization history.

## What Pulumi Builds

The Pulumi project is a Python 3.12 project using the `uv` toolchain declared
in `Pulumi.yaml`. The infrastructure code is in `__main__.py`; the project
dependencies are only Pulumi providers:

```text
pulumi
pulumi-kubernetes
pulumi-postgresql
pulumi-random
```

The `mx` stack config currently sets the Dagster namespace, chart version,
hostname, PostgreSQL stack reference, database name, and database user. The
defaults in code match the current stack config file:

```text
Namespace:              dagster
Helm chart:             dagster
Helm repository:        https://dagster-io.github.io/helm
Chart version:          1.13.5
Dagster version export: 1.13.5
Release name:           dagster
Hostname:               dagster
Database:               dagster
Database user:          dagster
Database Secret:        dagster-postgresql-secret
Smoke ConfigMap:        dagster-smoke-definitions
Smoke code path:        /opt/dagster/smoke/definitions.py
User-code deployment:   homelab-smoke
Smoke asset:            homelab_smoke
```

The stack creates these pieces directly:

```text
Kubernetes namespace
PostgreSQL role
PostgreSQL database
Random database password
Kubernetes Secret containing the generated database password
Kubernetes ConfigMap containing the smoke Dagster definitions file
Dagster Helm chart release
Pulumi stack outputs for the useful non-secret identifiers
```

The PostgreSQL database is not chart-managed. The program reads the shared
PostgreSQL stack through a `StackReference`, uses the PostgreSQL provider to
create a Dagster-owned role and database, then tells the Dagster Helm chart to
use that external database. The chart's embedded PostgreSQL subchart is
disabled, and `generatePostgresqlPasswordSecret` is disabled because Pulumi
creates the Secret itself.

The generated password is stored in Kubernetes as
`dagster-postgresql-secret`, under the key expected by the Helm chart. The
secret value is not part of this documentation and should not be copied into
docs, commits, screenshots, or issue text.

## The Runtime Pieces

Think about this deployment as four cooperating parts: the webserver, the
daemon, the user-code location, and the run launcher.

The webserver is the human interface. It serves the UI, reads Dagster metadata,
shows assets and runs, talks to code locations, and lets you launch or inspect
materializations. In this stack the webserver Service is a `ClusterIP` on port
`80`, and the chart exposes it through a Tailscale ingress. Pulumi exports the
URL as:

```text
https://<hostname>
```

The current hostname config is `dagster`, so the stack output is intentionally
short. Let Tailscale and the cluster ingress decide how that hostname resolves
in the tailnet; do not bake private tailnet details into the docs.

The daemon is the background control loop. It is enabled in chart values. It is
responsible for Dagster's background work such as queued runs, schedules,
sensors, run monitoring, and similar orchestration duties. This stack also
enables the chart's run coordinator setting under `dagsterDaemon`, so when a
run is queued the daemon path matters. A webserver that loads is not enough;
the daemon must also be able to coordinate and launch work.

The user-code location is where asset definitions live. In this stack, the only
user-code location is the smoke deployment named `homelab-smoke`. It runs the
`docker.io/dagster/dagster-celery-k8s` image with the same tag as the configured
chart version, exposes gRPC on port `3030`, and starts Dagster with:

```text
--python-file /opt/dagster/smoke/definitions.py
```

That Python file comes from the Pulumi-owned ConfigMap
`dagster-smoke-definitions`. The ConfigMap contains a tiny `Definitions` object
with one asset named `homelab_smoke`. The point of this asset is not business
logic. It proves that the Helm chart can load user code, that the code location
can serve definitions over gRPC, that the webserver can discover the asset, and
that a materialization can be launched and recorded.

The run launcher is configured as `K8sRunLauncher` with
`loadInclusterConfig: true` and `jobNamespace` set to the Dagster namespace.
That means Dagster launches actual Kubernetes Jobs for runs in the `dagster`
namespace. The smoke user-code deployment also has
`includeConfigInLaunchedRuns.enabled` set, so launched runs inherit the relevant
code-location configuration from the deployment. If assets appear in the UI but
materializations never start, the run launcher and daemon are the first places
to inspect.

With the current chart naming, the main rendered workloads are expected to look
like this:

```text
Deployment: dagster-dagster-webserver
Deployment: dagster-daemon
Deployment: dagster-dagster-user-deployments-homelab-smoke
Service:    dagster-dagster-webserver
Service:    homelab-smoke
```

Those names come from the chart plus `fullnameOverride: dagster`; they are
useful when reading pod lists or logs. If a future chart version changes the
rendered names, trust `kubectl get deploy,svc -n dagster` over old notes.

## Storage And Metadata

Dagster has two kinds of state to think about: metadata about the orchestration
system, and the data assets themselves.

The orchestration metadata for this stack lives in the external PostgreSQL
database created by Pulumi. That is where Dagster records run state,
materialization events, asset event history, schedule and sensor state, and the
other metadata that makes the UI more than a static code browser. If PostgreSQL
is unreachable, the UI, daemon, and runs can all fail in ways that look
unrelated until you follow them back to storage.

The assets do not live in Dagster automatically. Dagster records that an asset
was materialized, along with metadata the asset code chooses to emit. The actual
table, object, report, model, or file set must be written by asset code to the
right durable system. This Pulumi project does not configure object storage,
warehouse storage, or a persistent volume for real asset outputs. The smoke
asset simply proves control-plane plumbing.

The project also does not define a custom durable compute-log store. Treat run
logs as operational evidence, not as the long-term source of truth for produced
data. If future assets need durable logs or artifacts, add that deliberately in
the user-code project and chart values rather than assuming the smoke setup
already solved it.

The ConfigMap is not storage. It is a small bootstrap fixture. If real assets
are added, their source of truth should be versioned code packaged into an
image or otherwise deployed through a reviewed, reproducible path.

## Open The UI

Use stack outputs for non-secret identifiers:

```bash
cd pulumi/data/workflow/dagster

pulumi stack output --stack mx url
pulumi stack output --stack mx namespace
pulumi stack output --stack mx userCodeDeployment
pulumi stack output --stack mx database
pulumi stack output --stack mx databaseSecretName
```

Open the exported URL. The useful first check is not just "does the page load?"
It is:

```text
the webserver loads
the homelab-smoke code location is healthy
the homelab_smoke asset appears
a manual materialization launches a run
the run completes
the materialization event appears in asset history
```

That sequence proves the webserver, daemon, external PostgreSQL metadata store,
user-code deployment, and Kubernetes run launcher are all participating.

## How To Read The Smoke Asset

The smoke asset is defined inline in Pulumi because it is infrastructure test
code. Pulumi creates a ConfigMap whose `definitions.py` imports
`Definitions` and `asset`, defines `homelab_smoke`, and registers it:

```python
from dagster import Definitions, asset


@asset
def homelab_smoke() -> str:
    return "..."


defs = Definitions(assets=[homelab_smoke])
```

The return value is intentionally unimportant. The important part is that
Dagster can import the module, expose the `Definitions`, render the asset in
the UI, start a Kubernetes-backed run, and store the event. If this asset fails,
you have a platform problem or a smoke-code import problem before you have a
data product problem.

Do not grow this ConfigMap into a real Dagster repository. It is fine for a
single smoke asset because it keeps the first deployment self-contained. Real
assets need dependencies, tests, owners, resources, secrets, and release
discipline. That belongs in versioned user code and a proper image.

## Asset And Materialization Mental Model

An asset is a named durable output. A materialization is evidence that Dagster
ran code and produced or updated that output. A run is the execution context
that produced the event. Metadata is the extra evidence attached to the event:
row counts, object paths, partitions, schema versions, model metrics, source
timestamps, or validation summaries.

A minimal asset can be tiny:

```python
from dagster import asset


@asset
def daily_signups() -> None:
    ...
```

That tells Dagster the asset exists, but not much else. A more useful asset
declares its dependencies and emits metadata that helps an operator trust the
result:

```python
from dagster import AssetExecutionContext, MaterializeResult, MetadataValue, asset


@asset(deps=["raw_signups"])
def daily_signups(context: AssetExecutionContext) -> MaterializeResult:
    row_count = build_daily_signups_table()
    context.log.info("materialized daily_signups")
    return MaterializeResult(
        metadata={
            "rows": row_count,
            "owner": MetadataValue.text("data-platform"),
        }
    )
```

The example is intentionally generic; the current repo does not yet include a
real Dagster user-code package. The principle is the part to carry forward:
asset code should make the durable output and the evidence around it explicit.

For real data work, prefer asset functions that are idempotent or at least
clear about their side effects. If a materialization can be retried, decide
what happens to partially written output. Use deterministic destinations,
transactional swaps, partitions, temporary paths, or validation steps so a
failed run does not leave an ambiguous asset behind.

Resources are how Dagster code should receive dependencies such as database
clients, object-store clients, Spark sessions, API clients, or model registries.
Avoid hiding those dependencies in arbitrary environment reads inside the asset
body. When resources are explicit, it is much easier to test assets locally,
review secret needs, and understand what a run can touch.

## Adding Real User Code

The current stack proves the deployment path with a ConfigMap-mounted Python
file. A production-shaped Dagster path should be different:

```text
create a user-code project in version control
define assets, resources, checks, schedules, and sensors there
test that project outside the cluster
package it into an image with its Python dependencies
push the image through the repo's chosen image build path
update the Dagster Helm values in Pulumi to point at that image
wire secrets through Pulumi config, Kubernetes Secrets, or existing secret stores
run static checks and a targeted preview
apply only after the user explicitly asks for an apply
verify the code location and materialize a small asset
```

For a real image, the user deployment would usually stop using
`--python-file /opt/dagster/smoke/definitions.py` and use a module or package
entrypoint from the image, for example:

```text
dagsterApiGrpcArgs:
  - --module-name
  - my_dagster_project.definitions
```

That is an example of the desired shape, not the current repo wiring. The
current repo wiring is still the smoke ConfigMap and `homelab-smoke` user-code
deployment.

When adding secrets for real assets, keep the boundary clear. Infrastructure
can create or reference Kubernetes Secrets, and asset resources can consume
them through environment variables or mounted files, but secret values should
not appear in source code, docs, Pulumi outputs pasted into notes, or chart
values committed in plaintext.

## Development And Preview Path

For infrastructure changes to this stack, work from the repo root unless you
have a reason to run a project-local command:

```bash
just sync pulumi/data/workflow/dagster
just check-python
just lint
git diff --check
just preview pulumi/data/workflow/dagster stack=mx
```

`just sync` refreshes the project environment. `just check-python` catches
syntax/import issues across Pulumi entrypoints without contacting the cluster.
`just lint` runs Ruff checks and formatting verification. `git diff --check`
catches whitespace damage. The targeted preview is the first cluster-aware
guardrail for this stack.

Do not run `pulumi up`, `pulumi destroy`, or `just up` as part of ordinary
editing. This repository manages live infrastructure. Apply and destroy actions
need an explicit user request.

After an approved apply, verify behavior through Dagster, not only Pulumi:

```text
open the UI
confirm the code location is healthy
materialize homelab_smoke
confirm the run completes
inspect asset materialization history
check daemon and user-code logs if the run path is slow or stuck
```

## Debugging From First Principles

Start by identifying which layer is failing. The same visible symptom can come
from very different layers.

```text
webserver:      can the UI load and query metadata?
daemon:         are queued runs, schedules, and background loops progressing?
database:       can Dagster read and write metadata?
user code:      can the code location import and serve definitions?
run launcher:   can Dagster create Kubernetes Jobs for runs?
asset code:     did the Python code fail after the platform launched it?
```

A useful read-only inspection sequence is:

```bash
cd pulumi/data/workflow/dagster
NS="$(pulumi stack output --stack mx namespace)"

kubectl get deploy,pods,svc,ingress,configmap,secret -n "$NS"
kubectl get jobs -n "$NS"
kubectl describe ingress -n "$NS"
```

For logs, prefer deployment names after confirming them with `kubectl get
deploy`:

```bash
kubectl logs -n "$NS" deploy/dagster-dagster-webserver --tail=200
kubectl logs -n "$NS" deploy/dagster-daemon --tail=200
kubectl logs -n "$NS" deploy/dagster-dagster-user-deployments-homelab-smoke --tail=200
```

If a materialization launched a Kubernetes Job, inspect the job and its pod:

```bash
kubectl get jobs,pods -n "$NS"
kubectl describe job -n "$NS" <run-job-name>
kubectl logs -n "$NS" job/<run-job-name> --tail=200
```

Common patterns:

The UI does not load. Check the ingress, the webserver Service, the webserver
Deployment, and webserver logs. Because this stack uses Tailscale ingress, do
not debug it as a public LoadBalancer or NodePort service.

The UI loads but the asset is missing. Check the user-code deployment first.
The ConfigMap may not be mounted, the Python file may fail to import, or the
gRPC code location may not be reachable.

The asset appears but materialization does not start. Check daemon logs, run
coordinator behavior, and whether the `K8sRunLauncher` can create Jobs in the
Dagster namespace.

The run starts but fails quickly. Read the run log and the launched job pod log.
At that point the platform may be fine and the failure may be ordinary Python,
dependency, resource, or configuration behavior in user code.

Errors mention PostgreSQL or storage. Check the shared PostgreSQL stack
contract, the Dagster role/database, the Kubernetes Secret name, and the chart
values that point Dagster at the external database. Do not replace this with
chart-managed PostgreSQL unless you intend to migrate metadata storage.

Pods look healthy but behavior is stale. Check whether the ConfigMap or image
actually changed, whether the user-code deployment restarted, and whether the
webserver is still showing an old code location. For image-based user code,
prefer immutable tags or digests for repeatable deploys.

## Safe Changes

Safe changes preserve three things: the metadata database, the resource names
that Kubernetes and Helm already own, and the contract between Dagster and user
code.

Be careful with these values:

```text
namespace
releaseName / chart resource names
databaseName
databaseUser
databaseSecretName
postgresStack
hostname
user-code deployment name
```

Changing them can be correct, but it is a migration, not a cosmetic edit. A
namespace or release-name change can strand Helm-owned resources. A database
name or user change can disconnect Dagster from its event history. A Secret name
change must stay aligned with `global.postgresqlSecretName`. A user-code
deployment rename can change the code-location identity operators see in the
UI.

Chart upgrades should be treated as migrations. Before changing
`chartVersion`, compare the chart values and generated resources, then run a
targeted preview. Pay special attention to webserver, daemon, user deployment,
PostgreSQL, ingress, and run launcher values. The current stack deliberately
uses external PostgreSQL, Tailscale ingress, a `ClusterIP` webserver Service,
and disabled telemetry; keep those choices unless you are intentionally
changing them.

If you add a real user-code image, keep the smoke asset or replace it with an
equally small platform check. A tiny materialization that proves the full run
path is valuable during chart upgrades and storage changes.

If a preview reports that a same-name Kubernetes object already exists, inspect
ownership before changing resource names. It may be a Helm/Pulumi ownership or
replacement issue, not a reason to rename live infrastructure casually.

Do not store private URLs, Pulumi secret values, kubeconfig data, generated
passwords, or copied database credentials in this page. Secret names and output
names are useful; secret values are not.
