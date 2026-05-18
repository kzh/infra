# Slurm

Source: `pulumi/data/analytics/slurm`

Slurm is a batch scheduler. The core idea is simple: users submit work, the
scheduler places that work on available compute, and the cluster keeps enough
state for people to see what is queued, running, finished, or stuck.

That model is different from the Kubernetes model even when Slurm itself is
running on Kubernetes. A Kubernetes user usually thinks in Deployments, Jobs,
Pods, Services, labels, and controllers. A Slurm user thinks in login nodes,
partitions, allocations, jobs, job steps, requested CPUs and memory, queue
state, and output files. This stack exists so users can work through the Slurm
interface while Pulumi and Slinky take care of the Kubernetes objects underneath.

Use this page when the shape of the work is "submit a script to a queue" rather
than "run a long-lived service" or "create a Kubernetes-native batch object".
The best fit is CPU-oriented, HPC-shaped work where the natural interface is
`ssh`, `sinfo`, `sbatch`, `srun`, `squeue`, and `scontrol show job`.

This repo intentionally keeps the current Slurm stack small. It is a CPU-only
Slinky Slurm deployment. It does not currently wire GPU resources, Pyxis, Enroot,
Slurm accounting storage, or a shared home filesystem.

## What Pulumi Builds

The Pulumi project installs three chart-backed pieces:

1. The Slinky operator CRDs from the Slinky chart repository.
2. The Slinky Slurm operator in the operator namespace.
3. A Slurm cluster custom-resource set in the Slurm namespace.

The stack also exposes the Slurm login service through Tailscale, enables
controller metrics, creates a ServiceMonitor with the monitoring release label,
and loads a Grafana dashboard ConfigMap from
`pulumi/data/analytics/slurm/dashboards/slurm-overview.json`.

The important repo-owned defaults are:

```text
Pulumi project:                    pulumi/data/analytics/slurm
Pulumi runtime:                    Python 3.12 through uv
Operator namespace:                slinky
Slurm namespace:                   slurm
Slinky chart version:              1.1.0
Chart repository:                  ghcr.io/slinkyproject/charts
LoginSet enabled by repo:          slinky
Login service exported by repo:    slurm-login-slinky
Login hostname config default:     slurm
Login service exposure:            ClusterIP plus Tailscale Service exposure
REST API replicas:                 1
Controller metrics:                enabled
ServiceMonitor label default:      release=kube-prometheus-stack
Controller persistence default:    disabled
Controller persistence class:      local-path, when enabled
Controller persistence size:       4Gi, when enabled
Dashboard ConfigMap:               slurm-dashboard-slurm-overview
```

Most Slurm behavior is still chart default behavior. The chart supplies a
default `slinky` NodeSet and a default `all` partition. This repo enables the
`slinky` LoginSet and overrides its Service to be a `ClusterIP` with Tailscale
exposure annotations.

That split matters when changing the stack. Pulumi owns the chart version,
selected chart values, namespaces, Tailscale annotations, metrics wiring, and
dashboard. The Slinky charts and operator own the detailed Kubernetes resources
that implement Slurm components.

## The Mental Model

A Slurm cluster has a controller, compute nodes, partitions, jobs, and job
steps.

The controller is the scheduler brain. It accepts job submissions, decides when
jobs can run, tracks node state, records job state while the job is active, and
answers commands such as `squeue`, `sinfo`, and `scontrol`.

Compute nodes are where work runs. In this Slinky deployment, compute nodes are
Kubernetes pods managed through the Slinky NodeSet custom resource. Users do not
usually create those pods directly. They ask Slurm for CPUs, memory, time, and a
partition, and Slurm places the job on a node that can satisfy the request.

A partition is a named pool of nodes plus policy. The current chart default is
an `all` partition that includes all NodeSets, is marked as the default
partition, is up, and has no chart-level maximum time limit. That does not mean
the cluster has unlimited physical resources. It means Slurm will not reject a
job because of that partition's configured `MaxTime`.

A job is the durable unit submitted to the queue. `sbatch` creates a job from a
script and returns a job ID. A job can be pending, running, completing, failed,
canceled, or finished.

A job step is a command running inside an allocation. `srun hostname` is a tiny
job step. Inside a batch script, each `srun ...` line is also a step. This is why
`sbatch` and `srun` are related but not interchangeable: `sbatch` is the usual
batch submission path, while `srun` is the usual "run this command inside an
allocation" path.

Pending is not automatically bad. Pending means Slurm has accepted the job but
has not found a valid time and place to run it yet. The reason might be resource
pressure, partition state, a node not being ready, a job waiting behind another
job, or a request that no available node can satisfy.

## Slurm On Kubernetes Through Slinky

Slinky gives Kubernetes a Slurm-shaped control plane. The Slinky operator
watches Slurm custom resources and reconciles the Kubernetes objects needed for
Slurm components.

For this stack, the useful Slinky resources are:

```text
Controller:  slurm
LoginSet:    slurm-login-slinky
NodeSet:     slurm-worker-slinky
RestApi:     slurm
```

You can see them from the Slurm namespace:

```bash
cd pulumi/data/analytics/slurm
NS="$(pulumi stack output --stack mx namespace)"

kubectl get controllers,loginsets,nodesets,restapis -n "$NS"
kubectl get pods,svc,endpoints,pvc -n "$NS"
```

Use those Kubernetes resources to understand whether the infrastructure exists.
Use Slurm commands to understand whether Slurm can schedule work. A healthy pod
does not guarantee a job request is valid, and a pending Slurm job does not
automatically mean Kubernetes is broken.

## Login And Submission Path

The intended human path is:

1. Read the login hostname from the Pulumi stack output.
2. Connect to the login service over the tailnet.
3. Run Slurm commands from the login environment.
4. Submit work with `sbatch` or run short commands with `srun`.
5. Inspect queue and job state with `squeue`, `sinfo`, and `scontrol`.

Start by reading the non-secret stack outputs:

```bash
cd pulumi/data/analytics/slurm

pulumi stack output --stack mx loginHostname
pulumi stack output --stack mx loginService
pulumi stack output --stack mx namespace
pulumi stack output --stack mx operatorNamespace
```

The Pulumi code exposes the Service through Tailscale. It does not print or
document a private credential. Use the SSH identity configured for the running
image and cluster policy:

```bash
ssh <configured-user>@<login-hostname>
```

If the hostname resolves and the TCP connection reaches SSH, but authentication
fails, that is different from a Tailscale or Kubernetes exposure failure. The
network path can be healthy while login credentials or authorized keys still
need to be configured deliberately.

For operator debugging, a cluster admin can also enter the login pod directly.
That is not the normal user path, but it is useful when separating SSH exposure
from Slurm behavior:

```bash
cd pulumi/data/analytics/slurm
NS="$(pulumi stack output --stack mx namespace)"

kubectl get pods -n "$NS" | rg 'login|slinky'
kubectl exec -n "$NS" -it <login-pod> -- bash -l
```

Submit jobs from the login environment unless you have intentionally configured
a local Slurm client, authentication, and config path on your laptop. The repo
does not set up a laptop-native Slurm CLI path.

## First Commands After Login

Once you are inside the login environment, begin with read-only scheduler state:

```bash
sinfo
sinfo -Nel
sinfo -o "%P %D %t %c %m %G %f"
squeue
scontrol ping
```

The first `sinfo` shows partitions and node availability. `sinfo -Nel` is more
node-oriented. The formatted `sinfo` command shows partition, node count, state,
CPU count, memory, generic resources, and features in a compact view.

Then run the smallest possible command:

```bash
srun --partition=all --time=00:02:00 --cpus-per-task=1 --mem=128M hostname
```

That proves the login path, controller path, scheduler, and at least one compute
node. It does not prove shared storage, batch output behavior, metrics, or any
application-specific runtime dependency.

If `srun hostname` works, submit a tiny batch job:

```bash
sbatch <<'SBATCH'
#!/usr/bin/env bash
#SBATCH --job-name=slurm-smoke
#SBATCH --partition=all
#SBATCH --time=00:02:00
#SBATCH --cpus-per-task=1
#SBATCH --mem=128M
#SBATCH --output=slurm-smoke-%j.out
#SBATCH --error=slurm-smoke-%j.err

set -euo pipefail

echo "job=${SLURM_JOB_ID}"
echo "node=$(hostname)"
echo "partition=${SLURM_JOB_PARTITION}"
date -u
SBATCH
```

Immediately inspect the queue:

```bash
squeue
squeue -o "%.18i %.9P %.24j %.8u %.2t %.10M %.6D %R"
```

When you have the job ID, inspect the job:

```bash
scontrol show job <job-id>
```

Use `scancel` if you need to stop a job you submitted:

```bash
scancel <job-id>
```

## `sbatch`, `srun`, `squeue`, And Friends

`sinfo` answers "what does the scheduler think the cluster has?" It is the best
first command for partitions, nodes, and node state.

`squeue` answers "what jobs is the scheduler managing right now?" It shows job
IDs, users, state, runtime, node counts, and pending reasons.

`scontrol show job <job-id>` answers "what exactly did Slurm record for this
job?" It includes the requested resources, partition, working directory, command,
state, reason, allocated nodes when present, and timestamps. When a job is
pending and the reason in `squeue` is too compact, use `scontrol`.

`srun` runs a command inside an allocation. It is good for quick checks,
interactive commands, and steps inside a batch script:

```bash
srun --partition=all --time=00:02:00 --cpus-per-task=1 --mem=128M hostname
srun --partition=all --time=00:02:00 --cpus-per-task=1 --mem=128M env | rg '^SLURM_'
```

`sbatch` submits a script and returns immediately with a job ID. It is the normal
batch path:

```bash
sbatch job.sh
```

`salloc` asks for an allocation and then gives you an interactive shell or a
place to run steps. Use it when you need an interactive session with allocated
resources:

```bash
salloc --partition=all --time=00:10:00 --cpus-per-task=1 --mem=512M
srun hostname
exit
```

`sacct` is the usual Slurm accounting history command, but this repo does not
currently enable Slurm accounting storage. Prefer `squeue`, `scontrol`, job
output files, and Kubernetes logs for this stack unless accounting is added
later.

## Partitions And Resources

The current default partition is `all`. Because it is the default partition,
jobs that omit `--partition` should land there as long as the chart default
remains in place. Being explicit is still clearer in examples and automation:

```bash
#SBATCH --partition=all
```

Slurm schedules from the requested resources. A job that asks for more CPU or
memory than any Slurm node can provide will wait. A job that asks for a partition
that does not exist will be rejected. A job that asks for a GPU resource in this
repo's current Slurm stack should not be expected to run because GPU resources
are not wired here.

Keep requests small until you know what Slurm sees:

```bash
sinfo -o "%P %D %t %c %m"
```

Then request CPU, memory, and wall-clock time deliberately:

```bash
sbatch <<'SBATCH'
#!/usr/bin/env bash
#SBATCH --job-name=resource-check
#SBATCH --partition=all
#SBATCH --time=00:05:00
#SBATCH --cpus-per-task=1
#SBATCH --mem=256M
#SBATCH --output=resource-check-%j.out
#SBATCH --error=resource-check-%j.err

set -euo pipefail

echo "job=${SLURM_JOB_ID}"
echo "cpus=${SLURM_CPUS_PER_TASK}"
echo "node=$(hostname)"
python3 - <<'PY'
import os
print("submit_dir=", os.environ.get("SLURM_SUBMIT_DIR"))
print("job_id=", os.environ.get("SLURM_JOB_ID"))
PY
SBATCH
```

For a multi-step script, use `srun` inside the batch script so Slurm tracks the
work as job steps:

```bash
cat > two-step.sh <<'SCRIPT'
#!/usr/bin/env bash
#SBATCH --job-name=two-step
#SBATCH --partition=all
#SBATCH --time=00:05:00
#SBATCH --cpus-per-task=1
#SBATCH --mem=256M
#SBATCH --output=two-step-%j.out
#SBATCH --error=two-step-%j.err

set -euo pipefail

srun hostname
srun bash -lc 'echo "running on $(hostname) with job ${SLURM_JOB_ID}"'
SCRIPT

sbatch two-step.sh
```

For repeated independent work, use an array job:

```bash
sbatch <<'SBATCH'
#!/usr/bin/env bash
#SBATCH --job-name=array-check
#SBATCH --partition=all
#SBATCH --time=00:05:00
#SBATCH --cpus-per-task=1
#SBATCH --mem=128M
#SBATCH --array=1-4
#SBATCH --output=array-check-%A-%a.out
#SBATCH --error=array-check-%A-%a.err

set -euo pipefail

echo "array_job=${SLURM_ARRAY_JOB_ID}"
echo "task=${SLURM_ARRAY_TASK_ID}"
echo "node=$(hostname)"
SBATCH
```

Do not tune a real workload by guessing. Read what Slurm sees, request the
smallest resource shape that is honest for the job, and increase from there.

## Files, Working Directories, And Storage

Batch scheduling becomes much easier when you separate three places:

```text
Submission directory:  where `sbatch` was run
Execution directory:   where the job process starts on a compute node
Durable storage:       where inputs and outputs should survive pod replacement
```

Slurm records the submission directory in `SLURM_SUBMIT_DIR`. Output paths in
`#SBATCH --output` and `#SBATCH --error` are interpreted by Slurm and the
execution environment. If the login pod and compute pods do not share the same
filesystem, a relative output path may not be visible where you expect it to be.

That point is important in this repo. The current Pulumi stack does not mount a
shared `/home`, shared project directory, or shared scratch filesystem into both
the LoginSet and NodeSet. The Slinky chart supports additional volumes and
volume mounts, but this repo has not wired them. Treat local pod filesystems as
temporary unless you have added and verified a shared storage design.

For quick interactive checks, prefer `srun` because output comes back to the
terminal:

```bash
srun --partition=all --time=00:02:00 --cpus-per-task=1 --mem=128M bash -lc 'hostname; date -u'
```

For `sbatch`, make output handling explicit. During early testing, use small
jobs and confirm where the output lands before relying on the path:

```bash
mkdir -p logs

sbatch <<'SBATCH'
#!/usr/bin/env bash
#SBATCH --job-name=output-check
#SBATCH --partition=all
#SBATCH --time=00:02:00
#SBATCH --cpus-per-task=1
#SBATCH --mem=128M
#SBATCH --output=logs/%x-%j.out
#SBATCH --error=logs/%x-%j.err

set -euo pipefail
pwd
hostname
date -u
SBATCH
```

For real work, use durable inputs and outputs. Good destinations are a shared
PVC that is mounted into both login and worker pods, object storage, a database,
or another repo-managed data service. The job script should make those paths
obvious. Slurm should be the scheduler, not the only place where the data flow
is documented.

Avoid putting secrets in job scripts, output files, Slurm job names, command
arguments, or environment dumps. Job metadata and logs tend to be copied into
tickets, dashboards, shells, and debugging transcripts.

## Controller State, Job History, And Persistence

Pulumi sets `controllerPersistenceEnabled` to `false` by default. That disables
the controller PVC that would otherwise retain Slurm controller save-state
across controller pod recreation.

This is acceptable for a small lab-style stack, but it is a real behavior
choice. If the controller pod is replaced, active scheduler state may not behave
like a production Slurm installation with durable controller state. Also,
because accounting is not enabled, long-term historical job records are not the
contract of this stack.

If the cluster starts carrying important batch work, revisit persistence before
the workload becomes important:

```text
controllerPersistenceEnabled
controllerPersistenceStorageClass
controllerPersistenceStorageSize
accounting storage
shared input/output storage
backup and restore expectations
```

Do not turn those on as isolated toggles without a preview and a recovery plan.
Storage changes affect how users trust job state and output.

## REST API

The Slurm chart includes a `RestApi` custom resource, and this repo sets
`restapiReplicas` to `1` by default. That means the Slurm REST component is part
of the deployment shape.

The repo does not currently document a public REST client path, publish REST
tokens, or expose the REST API through Tailscale as a user-facing endpoint. Treat
the CLI path through the login environment as the supported path unless the
stack is deliberately extended for REST clients.

If a future change exposes REST, it should also document authentication, token
lifetime, network exposure, and which clients are expected to use it. Do not
invent those details in an operational note or shell history.

## Debugging From The User Symptom

Start from what failed, not from the layer you are most familiar with.

If the login hostname is unreachable, inspect the Tailscale and Service path:

```bash
cd pulumi/data/analytics/slurm

OP_NS="$(pulumi stack output --stack mx operatorNamespace)"
NS="$(pulumi stack output --stack mx namespace)"
LOGIN_SERVICE="$(pulumi stack output --stack mx loginService)"

kubectl get pods,svc -n "$OP_NS"
kubectl get pods,svc,endpoints -n "$NS"
kubectl get endpoints -n "$NS" "$LOGIN_SERVICE"
tailscale status
tailscale ping <login-hostname>
```

If the Service has no endpoints, focus on the LoginSet and login pods before
debugging tailnet routing. If the hostname does not appear in Tailscale state,
inspect the Service annotations and the Tailscale operator logs from the
networking stack.

If SSH reaches the host but authentication fails, the network path is probably
working. Check the login image and authorized-key or identity configuration
rather than changing the Service type or hostname.

If SSH works but Slurm commands fail, inspect Slurm and Slinky state:

```bash
scontrol ping
sinfo -Nel
squeue

kubectl get controllers,loginsets,nodesets,restapis -n "$NS"
kubectl get pods -n "$NS" -o wide
kubectl describe controllers -n "$NS"
kubectl describe nodesets -n "$NS"
kubectl describe loginsets -n "$NS"
```

If a job is pending, ask Slurm why before inspecting pods:

```bash
squeue -j <job-id> -o "%.18i %.9P %.24j %.8u %.2t %.10M %.6D %R"
scontrol show job <job-id>
```

Common pending reasons include resources not available, partition not available,
nodes not ready, job priority, dependency constraints, or a request that does
not fit any node.

If a job fails immediately, read the Slurm output and error path first. Then
inspect Kubernetes logs if the failure looks like infrastructure:

```bash
kubectl get pods -n "$NS" -o wide
kubectl logs -n "$NS" <slurm-pod-name> --tail=200
kubectl describe pod -n "$NS" <slurm-pod-name>
```

If the operator is not reconciling, inspect the operator namespace:

```bash
kubectl get deploy,pods,svc -n "$OP_NS"
kubectl logs -n "$OP_NS" deploy/slurm-operator --tail=200
kubectl logs -n "$OP_NS" deploy/slurm-operator-webhook --tail=200
```

If metrics or the dashboard are empty, check scraping before editing dashboard
JSON:

```bash
kubectl get servicemonitors -n "$NS"
kubectl get svc -n "$NS" | rg 'controller|metrics|slurm'
kubectl get configmap -n "$NS" slurm-dashboard-slurm-overview
```

The dashboard can only show what Prometheus scrapes. A dashboard edit will not
fix a missing ServiceMonitor label, disabled controller metrics, or a controller
that is not exposing metrics.

## Reading Job State

These commands are the usual Slurm loop:

```bash
sinfo
squeue
squeue -u "$USER"
scontrol show job <job-id>
scancel <job-id>
```

A compact watch command is useful while learning:

```bash
watch -n 2 'squeue -o "%.18i %.9P %.24j %.8u %.2t %.10M %.6D %R"'
```

For node detail:

```bash
sinfo -Nel
scontrol show nodes
```

For partition detail:

```bash
scontrol show partition
```

When the queue and Kubernetes disagree, write down the specific disagreement:

```text
Slurm says node is down, Kubernetes pod is running
Slurm says job is running, worker pod is restarting
Slurm says no nodes are available, NodeSet has no ready pods
Service has endpoints, but SSH cannot authenticate
Service has no endpoints, but LoginSet exists
```

That keeps the debugging path concrete.

## Safe Changes

Change this stack through Pulumi, not by patching live operator-created objects.
Live patches are easy to lose and hard to explain later. The source of truth is
`pulumi/data/analytics/slurm/__main__.py`, the project config, and any
service-local assets such as dashboards.

Use the repo checks before changing behavior:

```bash
just sync pulumi/data/analytics/slurm
just check-python
just lint
git diff --check -- docs/stacks/data/analytics/slurm.md pulumi/data/analytics/slurm
just preview pulumi/data/analytics/slurm stack=mx
```

For a docs-only change, the path-limited `git diff --check` is usually the most
relevant check. For a Pulumi code change, run the targeted preview and classify
any failure as code regression, missing config, live-state drift, provider
behavior, or an external dependency before editing further.

Treat these as behavior changes:

```text
chartVersion
loginHostname
operatorNamespace
namespace
controllerPersistenceEnabled
controllerPersistenceStorageClass
controllerPersistenceStorageSize
restapiReplicas
loginsets values
nodesets values
partitions values
metrics and ServiceMonitor labels
dashboard JSON
```

Treat these as especially sensitive changes:

```text
Helm release names
CRD ownership
resource names exported as stack outputs
Tailscale hostnames
SSH identity and authorized-key handling
shared storage mounts
controller persistence
accounting storage
GPU or generic-resource wiring
```

Changing release names or trying to adopt existing chart-owned CRDs can create
ownership conflicts. Changing login hostnames affects users. Enabling
persistence or shared storage changes the durability contract. Adding GPUs,
Pyxis, Enroot, or accounting is a new operational design, not a small cleanup.

After an intentional apply, verify at the level users actually experience:

```bash
pulumi stack output --stack mx loginHostname
tailscale ping <login-hostname>
ssh <configured-user>@<login-hostname>
sinfo
srun --partition=all --time=00:02:00 --cpus-per-task=1 --mem=128M hostname
sbatch <tiny-script-that-writes-to-a-known-durable-path>
squeue
```

Then verify Kubernetes and observability:

```bash
NS="$(pulumi stack output --stack mx namespace)"
kubectl get controllers,loginsets,nodesets,restapis -n "$NS"
kubectl get pods,svc,endpoints,pvc -n "$NS"
kubectl get servicemonitors -n "$NS"
```

Do not call the stack healthy just because the Helm release exists. The contract
is that a user can reach the login path, Slurm can see a usable partition, a tiny
job can run, and outputs go somewhere understandable.

## When To Use Another Tool

Use Slurm when the important interface is a scheduler queue and a batch script.
Use Spark when the important interface is distributed data processing. Use
Trino when the work is federated SQL. Use Airflow or Dagster when the important
interface is a scheduled or dependency-aware workflow. Use a Kubernetes Job when
the work is naturally container-shaped and Kubernetes-native.

Running Slurm on Kubernetes does not mean every Slurm job should be debugged as
a Kubernetes Job. Start with Slurm's own state. Drop into Kubernetes when the
Slurm components, Services, pods, storage, or operator reconciliation disagree
with what Slurm reports.
