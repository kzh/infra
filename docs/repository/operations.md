# Operations

Operations in this repo work best when you keep three pictures in view at the
same time:

```text
the repo        what the owning Pulumi project says should exist
Pulumi state    what Pulumi believes it manages for this stack
the cluster     what Kubernetes and the external access layer are doing now
```

Do not let any one of those pictures pretend to be the whole truth. A docs page
can be stale. A preview can fail because the cluster drifted. A pod can be
healthy while the Service has no endpoints. A URL can be broken because the
application is down, because the private network path is wrong, or because an
operator changed a selector.

Start by finding the owner:

```bash
just projects
rg -n "<service-name>|<hostname>|<namespace>" docs pulumi
```

Then read the service guide and the owning project:

```text
docs/stacks/.../<service>.md
pulumi/<area>/<service>/Pulumi.yaml
pulumi/<area>/<service>/__main__.py
Pulumi.<stack>.yaml and ESC imports, when present
```

Use live inspection to understand the failure. Use the repo for fixes that
should survive reconciliation.

## First Pass

The first pass should change nothing. It should answer:

```text
which project owns the service?
which namespace and Kubernetes objects matter?
is this private Tailscale, public Cloudflare, in-cluster DNS, or direct client access?
does the Service have endpoints?
are the pods ready, pending, crashing, or failing readiness?
is there durable state that could be harmed by a quick fix?
is Pulumi preview clean, blocked, or reporting a meaningful change?
```

Useful read-only commands:

```bash
git status --short
just projects

cd pulumi/<area>/<service>
pulumi stack output --stack mx
pulumi preview --stack mx --diff

kubectl get pods,svc,ingress,endpoints,pvc -n <namespace>
kubectl get events -n <namespace> --sort-by=.lastTimestamp
```

Use the root wrapper for the normal targeted preview:

```bash
just preview pulumi/<area>/<service> mx
```

Do not run `pulumi up`, `pulumi destroy`, or `just up` unless the task is
explicitly to change live infrastructure.

## Broken URLs

A broken URL is a path problem until proven otherwise. Walk it from the client
toward the pod:

```text
client
DNS or tailnet name
Tailscale ingress/service exposure or Cloudflare route
Kubernetes Ingress
Kubernetes Service
Service endpoints
pod readiness
application dependencies
```

For a private Tailscale URL, check both the Kubernetes backend and the tailnet
path:

```bash
kubectl get svc,ingress,endpoints -n <namespace>
kubectl describe ingress -n <namespace> <ingress-name>
kubectl describe svc -n <namespace> <service-name>

tailscale status
tailscale ping <tailnet-hostname>
```

For a public Cloudflare-routed URL, prove the backend before changing edge
settings:

```bash
kubectl get svc,endpoints -n <namespace> <service-name>
kubectl describe ingress -n <namespace> <ingress-name>
kubectl logs -n cloudflare-tunnel \
  -l app.kubernetes.io/name=cloudflare-tunnel-ingress-controller \
  --tail=200
```

Interpret symptoms by layer:

```text
hostname does not resolve
  client DNS, tailnet device state, public DNS, or route reconciliation

connection times out
  client path, tailnet ACL/path, Cloudflare transport, or proxy availability

TLS or hostname error
  host mismatch, ingress host, certificate path, or client using the wrong name

502 or backend unavailable
  proxy reached something, but the backend Service/port/endpoints/app is wrong

500 or app error page
  ingress probably worked; inspect app logs and dependencies
```

Do not stop at "pods are running." The operational claim you want is closer to:

```text
the host resolves from the intended client
the ingress points at the expected Service and port
the Service has ready endpoints
the pods behind those endpoints are ready
the app dependency path is healthy
the service-specific smoke check passed
```

For Airflow, that means the UI loads and `homelab_smoke` runs. For Spark, it
means Spark Connect accepts a tiny query and the UI reflects the job. For Slurm,
it means SSH reaches the login service and a tiny `sbatch` job completes. For a
database-backed app, it means the app can read and write the state it exists to
protect.

## Zero Endpoints

Zero endpoints means Kubernetes has no ready pod behind the Service selector.
Ingress, Tailscale, and Cloudflare cannot fix that.

Start with the Service:

```bash
kubectl describe svc -n <namespace> <service-name>
kubectl get endpoints -n <namespace> <service-name> -o yaml
kubectl get pods -n <namespace> --show-labels
```

Then compare:

```text
Service selector
pod labels
pod readiness condition
container ports and Service targetPort
operator-owned labels after chart/operator upgrades
```

Common causes:

```text
selector drift after a chart or operator upgrade
pod labels changed but the Service stayed old
readiness probe failing, so pods are intentionally excluded
Service targetPort points at a name or number the pod no longer exposes
the app listens on localhost instead of the pod interface
rollout created new pods but the Service still selects an old release label
```

Spark has hit this exact shape before: the visible symptom was a private URL
returning 502, while the real backend problem was an operator-owned Service
selector that no longer matched the Spark Connect pod labels. The durable fix
was not a proxy tweak; it was a repo-owned Service with the selector and port
the stack intended.

When endpoints are empty, decide whether the Service selector is wrong or the
pods are not ready. That distinction decides the next move:

```text
selector wrong, pods ready
  fix the owning Pulumi code, chart values, or transform

selector right, pods not ready
  inspect readiness, logs, dependencies, storage, and events

targetPort wrong
  fix the Service or chart values in the owning stack

operator owns the broken Service
  consider whether a repo-owned Service should pin the stable contract
```

## Pod Failures

Pod status is a clue, not the diagnosis. Read status, events, and logs together:

```bash
kubectl get pods -n <namespace> -o wide
kubectl describe pod -n <namespace> <pod-name>
kubectl logs -n <namespace> <pod-name> --all-containers --tail=200
kubectl get events -n <namespace> --sort-by=.lastTimestamp
```

Useful interpretations:

```text
Pending
  scheduling, storage binding, node resources, taints, affinity, or image pulls

ImagePullBackOff
  image name, tag, digest, registry auth, or platform mismatch

CrashLoopBackOff
  app startup, config, credentials, migrations, permissions, or missing files

Running but not Ready
  readiness probe, dependency connection, wrong port, slow startup, or app health

Completed or failed Jobs
  chart hooks, install/update jobs, database migrations, or one-time setup tasks

Evicted
  node pressure; check whether the data path survived and whether pods rescheduled
```

For multi-component services, inspect the component that owns the symptom. An
Airflow DAG parse failure belongs first in the DAG processor logs. A scheduling
failure belongs first in scheduler logs. A Hermes browser automation failure may
belong in the Camofox sidecar. A Slurm queue issue often belongs first in
`squeue` and `scontrol show job`, then Kubernetes if scheduler state and pod
state disagree.

Use service-native checks before falling through to generic Kubernetes:

```text
Airflow     trigger `homelab_smoke`, read DAG processor and scheduler logs
Dagster     materialize the smoke asset, inspect code-location and daemon logs
Spark       run `select 1`, write/read a tiny Iceberg table, inspect Spark UI and connect server logs
Slurm       run `sinfo`, `squeue`, `srun hostname`, then a tiny `sbatch`
Postgres    inspect CNPG cluster status before blaming consumers
RustFS      test the S3 API before blaming MLflow, Trino, or Spark
MediaWiki   check install/update/compat Jobs before judging the Deployment
Immich      protect the media PVC and database before touching chart behavior
Hermes      preserve the PVC and use exported runtime validation commands
```

## Preview Failures

Preview is where code, config, Pulumi state, providers, and live cluster state
meet. A preview failure is evidence. Classify it before editing more code.

Run the project-local preview when you need the most direct signal:

```bash
cd pulumi/<area>/<service>
pulumi preview --stack mx --diff
```

Use the wrapper when you want the repo's common command shape:

```bash
just preview pulumi/<area>/<service> mx
```

Classification guide:

```text
missing config
  required Pulumi config, secret config, ESC value, or provider environment

bad ESC import
  stack imports an environment that does not exist or no longer contains a key

StackReference contract issue
  producer output name, type, secret-ness, or meaning changed

provider cannot connect
  local auth, kube context, Tailscale path, database hostname, or API reachability

live-state drift
  resource changed outside Pulumi, operator rewrote fields, or state is stale

Helm/chart migration issue
  hook, release name, values schema, rendered name, labels, or immutable fields

same-name object conflict
  chart now renders an object that already exists under that Kubernetes name

provider await issue
  resource is healthy enough for this stack, but provider wait logic is noisy

real program bug
  Python error, bad import, wrong Output use, missing dependency, invalid resource args
```

Do not hide required config behind a default just to make preview quiet. Required
values are often the protection that keeps a stack from creating the wrong
hostname, database, bucket, or credential path.

Read replacements carefully. Replacement of a Deployment can be ordinary.
Replacement of these deserves a pause:

```text
PVCs
database clusters
object-store buckets
generated Secrets
tailnet identities
Services with stable client names
CRDs or operator-owned custom resources
StackReference producer outputs
```

For broad work, `just preview-all` is intentionally `mx`-only and writes logs
under `/tmp/pulumi-mx-previews-<timestamp>`. When reporting a broad preview,
include the log directory and classify failures. Do not paste secret-bearing log
output into docs or PR text.

## Helm Drift And Chart Upgrades

Treat Helm upgrades as migrations. Charts own generated names, labels,
selectors, hooks, Jobs, CRDs, and default values. A small version bump can change
which object a Service selects or whether a migration Job runs.

Before changing a chart:

```text
read the current values in __main__.py
check the chart version and release name
read chart release notes or values/schema changes when available
identify PVCs, database migrations, hooks, and CRDs
preview with --diff
look for replacements, same-name conflicts, selector changes, and hook Jobs
```

During preview, pay special attention to:

```text
release name changes
chart API changes, such as Release versus Chart behavior
resource prefix changes
selector and label changes
Service targetPort changes
immutable fields on Services, PVCs, StatefulSets, and CRDs
hook Jobs that Pulumi may wait on or try to recreate
resources that already exist because an old chart or operator created them
```

Some odd-looking transforms are operational guardrails. Immich preserves a
server selector because otherwise the chart can disturb the Service/Deployment
relationship. Some stacks use `pulumi.com/skipAwait` because provider waiting is
known to be noisy for a specific resource. Some rendered resources need
`delete_before_replace` because the chart creates same-name objects that cannot
be updated in place.

Do not remove those guardrails during cleanup unless you understand the live
failure they protect against and have previewed the replacement behavior.

When Helm drift is real, prefer a durable Pulumi repair:

```text
pin a stable release name
preserve Kubernetes metadata names
add aliases for Pulumi renames
add a targeted transform for a chart-rendered resource
use delete_before_replace only where replacement is the intended migration
create a repo-owned Service when an operator-owned Service cannot be trusted
```

Live cleanup can be part of a migration, but it should be explicit and approved.
Deleting a stuck Job is different from deleting a PVC, database, or tailnet
identity.

## Stateful Changes

Before changing a stateful stack, name the durable state and the consumers.

Examples:

```text
PostgreSQL      database cluster, roles, databases, CA material, StackReference outputs
RustFS          object data, buckets, access credentials, consumer endpoint contracts
Immich          media PVC plus PostgreSQL metadata
MediaWiki       MySQL data plus uploaded images PVC
Airflow         metadata database, DAG source path, generated admin secret
Temporal        database schemas plus archival PVC
Spark           shared Iceberg catalog, preserved legacy warehouse PVC, client-facing Connect/UI names
Slurm           controller state if persistence is enabled, login hostname, job outputs
Hermes          runtime PVC, Codex home, browser state, login/config state
golink          SQLite database and tsnet state on PVC
```

A backup story should match the state model. For Immich, the database without
the media library is incomplete, and the media library without the database is
also incomplete. For RustFS, bucket names and prefixes are application
contracts. For PostgreSQL, producer outputs are APIs consumed by many stacks.

Stateful change questions:

```text
what exact PVC, bucket, database, Secret, or identity holds the durable state?
which stacks consume this output or credential?
does preview show replacement, deletion, or rename?
is the service using a Helm hook or migration Job?
what small smoke test proves the state survived?
is rollback a code revert, a data restore, or both?
```

If the answer is not clear, keep the change small and inspect more. A quick fix
that damages state is not quick.

## Repo-Backed Fix Or Live Inspection

Use live inspection when the goal is to learn what is happening:

```text
read pods, Services, endpoints, ingresses, events, logs, and CR status
check Tailscale or Cloudflare reachability
run service-native smoke tests
compare live labels/selectors against the Pulumi program
confirm whether preview and live state disagree
```

Use a repo-backed fix when the desired behavior should survive the next
reconcile, restart, chart upgrade, or rebuild:

```text
Service selector, port, ingress, hostname, or exposure changes
chart values, release names, images, resources, probes, and hooks
database, bucket, role, Secret, or StackReference contract changes
dashboard, ServiceMonitor, PodMonitor, and metrics wiring
runtime image changes and app configuration
CRD-backed custom resources and generated binding consumers
```

Use a one-time live action only when the action is operational cleanup or
emergency recovery, and only with the right permission:

```text
delete a completed/stuck Job before a chart rerun
restart a Deployment after confirming config already changed
refresh Pulumi state after an interrupted update
port-forward for inspection
exec a read-only diagnostic command
```

Do not let a live patch become the final state. If a `kubectl patch` proves a
selector or port fix, encode the fix in the owning Pulumi project and preview
it. If a pod edit proves an app config problem, move the config into the image,
chart values, Secret, or Pulumi program that owns it.

## Reporting Results

A useful operations report is specific about evidence and careful about
boundaries. Include:

```text
project and stack inspected
preview command and whether it was clean, blocked, or showed changes
namespace and object types checked
pod readiness, Service endpoints, and ingress/backend state
relevant service-native smoke test
whether the finding is live-state drift, config, code, chart behavior, or data risk
what remains unverified
```

Avoid secret-bearing detail. Say "missing required secret config" instead of
naming or printing the value. Say "the admin password is retrievable with the
local Pulumi output command" instead of copying it. Include log paths for broad
preview sweeps, not private log excerpts.

The final bar is not "a pod is running." The final bar is that the intended user
path works and the repo still describes how it works.
