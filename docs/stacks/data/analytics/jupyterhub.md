# JupyterHub

Source: `pulumi/data/analytics/jupyterhub`

JupyterHub is the shared notebook front door for this repo. A notebook gives a
person a fast feedback loop: write code, run it, inspect the output, adjust the
idea, and leave enough notes beside the code to remember why the experiment
worked. JupyterHub adds the multi-user and infrastructure pieces around that
loop. Instead of everyone running a separate notebook server on a laptop, the
Hub accepts a browser session and starts a Kubernetes-backed notebook server for
that user.

The main reason to run notebooks in the cluster is proximity. The browser is
still local, but the Python process, filesystem mount, DNS resolver, and network
path are inside Kubernetes. That makes notebooks a good place to explore Trino,
Spark Connect, MLflow, RustFS-backed artifacts, private databases, and other
cluster services without turning every local machine into a custom tunnel and
credential bundle.

That power comes with a boundary: JupyterHub is an interactive workbench, not a
hidden production scheduler. Use it to discover, debug, explain, and prototype.
When a notebook becomes something that should run again without a human clicking
cells, move the runtime into a repo-backed job, workflow, Spark application, or
service.

## What This Stack Provides

Pulumi creates the `jhub` namespace and installs the official JupyterHub Helm
chart at version `4.3.5`. The chart owns the Hub, proxy, user-spawning machinery,
and notebook-server pod templates. This repo sets the deployment shape around
that chart rather than hand-building the Hub.

The important configured values are:

```text
Namespace:            jhub
Ingress host:         jupyterhub
Ingress class:        tailscale
Proxy service type:   ClusterIP
Hub database:         SQLite on a 5Gi PersistentVolumeClaim
Notebook image:       ghcr.io/kzh/jupyter:py313-amd64
Image pull policy:    Always
User storage:         dynamic 60Gi PersistentVolumeClaim per user
User PVC access:      ReadWriteOnce
Spawn timeout:        600 seconds
Cull idle servers:    disabled
Hub NetworkPolicy:    disabled
User scheduler:       disabled
Placeholder pods:     disabled
Pod priority:         disabled
```

Those choices say a lot about the intended scale. This is a private,
cluster-local notebook service with a Tailscale browser path, persistent user
homes, and a chart-managed Hub database. It is not currently modeled as a large
campus-style Hub with a custom scheduler, aggressive idle culling, complex auth
policy, or a shared external Hub database.

The Pulumi program also applies two targeted chart transforms. The
`hub-db-dir` PVC ignores metadata drift because chart/provider metadata can be
noisy there. The chart-rendered `hub` ConfigMap and Secret are set to
`delete_before_replace` so Helm-rendered name collisions are handled deliberately
during replacement. If you change chart values that affect those resources,
expect the preview to show that behavior and read it carefully.

## The Notebook Server Model

There are two different processes to keep straight.

The Hub is the coordinator. It handles the web application, tracks Hub state,
and asks a spawner to create or stop user servers. Its own state lives in the
chart-managed SQLite database on the `hub-db-dir` PVC. That database matters for
the Hub, but it is not where notebook files, datasets, or experiment artifacts
belong.

A user server is the actual notebook environment. When a person starts a server,
JupyterHub creates a single-user pod from `ghcr.io/kzh/jupyter:py313-amd64`.
That pod runs the Jupyter server, mounts the user's persistent home storage, and
executes notebook code. If the user stops the server, the pod can go away while
the PVC remains. If the user starts again later, a new pod should mount the same
home storage.

That split explains most operational behavior:

```text
Browser login or Hub page issue:      inspect ingress, proxy, and Hub
Spawn hangs or fails:                 inspect Hub logs, events, image pulls, PVCs, and user pods
Notebook code fails to import:        inspect the single-user image and Python environment
Files disappear after restart:        inspect where the notebook wrote the files and which PVC mounted
Cluster service cannot be reached:    inspect DNS and network from inside the user pod
```

The notebook process is inside a Kubernetes pod. It is not running on the
laptop, even though the UI is in the laptop browser. That is the most important
mental model when choosing hostnames, debugging package imports, or deciding
where files are stored.

## Users, Spawners, And Storage

The Pulumi source does not define a repo-specific user list, role mapping, or
custom authenticator. It leaves those concerns to the chart defaults and the
private Tailscale exposure model. If authentication or authorization becomes a
real policy boundary, treat it as a first-class chart change: inspect the chart
defaults, decide the desired identity model, preview the rendered resources, and
verify the login flow through the intended hostname.

The spawner creates notebook pods on demand. The configured `startTimeout` is
`600` seconds, which is intentionally generous for cold image pulls and slow PVC
attachment. A spawn timeout does not automatically mean the Hub is broken. It
usually means one of the pieces needed for the single-user pod did not become
ready in time.

Each user gets dynamic storage with a requested capacity of `60Gi`. The chart
default mounts user storage into the notebook home directory. The practical
result is that files under the user's home directory should survive pod restarts,
while files written to temporary container paths may disappear whenever the pod
is replaced.

In a new notebook, verify the home directory before trusting the session:

```python
from pathlib import Path
import os
import sys

home = Path.home()
print(sys.version)
print("home:", home)
print("cwd:", Path.cwd())
print("user:", os.environ.get("JUPYTERHUB_USER"))

probe = home / "jupyterhub-storage-smoke.txt"
probe.write_text("hello from jupyterhub\n", encoding="utf-8")
print(probe.read_text(encoding="utf-8"))
```

Then stop and restart the server from the Hub UI and read the same file again.
That simple check proves more than it looks like: the server can start, the
home mount is writable, the PVC survives a pod restart, and the notebook is
running as the expected user environment.

Do not treat user PVCs as the system of record for shared data. They are good
for notebooks, scratch analysis, local virtual environments, and small working
files. Shared datasets, model artifacts, warehouse tables, workflow definitions,
and service configuration belong in the systems that own those jobs:

```text
Notebook files and scratch work:       user PVC
Experiment runs and artifacts:         MLflow, backed by PostgreSQL and RustFS
Object-style data and exported files:  RustFS/S3 through an owning service or secret path
Federated SQL access:                  Trino
Analytical tables:                     ClickHouse or an explicit lakehouse design
Spark table experiments:               Spark's trino_iceberg catalog backed by RustFS
Scheduled workflows:                   Airflow or Dagster code
Batch/HPC-style runs:                  Slurm or another explicit job runner
```

Deleting or replacing user PVCs deletes user notebook state. Increasing the PVC
size is usually a different class of change from changing PVC identity, access
modes, storage class, namespace, or release name. Review storage diffs with
that distinction in mind.

## Opening The Hub

The intended browser path is:

```text
https://jupyterhub
```

That hostname is private to the trusted network path that understands the
Tailscale ingress. If the page does not load, start with the browser path before
debugging notebook servers:

```bash
kubectl get ingress,svc,endpoints,pods,pvc -n jhub
kubectl describe ingress -n jhub jupyterhub
kubectl logs -n jhub deploy/hub --tail=200
```

If the Hub page loads, start the default server. A successful start creates a
single-user notebook pod. Check it from Kubernetes if you want to see what the
Hub created:

```bash
kubectl get pods -n jhub -l component=singleuser-server -o wide
kubectl get pvc -n jhub
```

The browser page proves the ingress, proxy, and Hub are reachable. Starting a
server proves the image, spawner, scheduler, PVC provisioning, and user pod path
are working.

## In-Cluster DNS

A notebook server resolves names from inside Kubernetes. That is useful, but it
means laptop hostnames and pod hostnames are not always interchangeable.

Kubernetes service DNS has this shape:

```text
<service>.<namespace>.svc.cluster.local
```

Short names only work reliably inside the same namespace. A notebook pod runs in
`jhub`, so `trino` by itself would first mean a service named `trino` in `jhub`.
The Trino service in the `trino` namespace should be addressed as:

```text
trino.trino.svc.cluster.local:8080
```

The RustFS S3 API service is:

```text
rustfs-s3.rustfs.svc.cluster.local:9000
```

Spark Connect is similar, but read the Spark stack outputs before hard-coding
the namespace and service name:

```bash
cd pulumi/data/analytics/spark
pulumi stack output --stack mx namespace
pulumi stack output --stack mx spark_connect_name
```

From a notebook pod, the fully qualified Spark Connect target is:

```text
sc://<spark_connect_name>.<spark_namespace>.svc.cluster.local:15002
```

From a laptop client, the Spark docs use the Tailscale-exposed hostname instead:

```bash
cd pulumi/data/analytics/spark
pulumi stack output --stack mx spark_connect_hostname
```

That client-location distinction is the common source of confusing network
failures. The right hostname depends on where the code is running.

For HTTP services with private ingresses, such as MLflow, `https://mlflow` may
be the clean path from a browser or a trusted client. From a pod, the in-cluster
service FQDN can be cleaner, but confirm the service and port first:

```bash
kubectl get svc -n mlflow
```

To test raw DNS and ports from the same place notebook code runs, exec into the
single-user pod:

```bash
kubectl get pods -n jhub -l component=singleuser-server
kubectl exec -n jhub -it <notebook-pod> -- bash
```

Then run small checks from inside that shell:

```bash
getent hosts trino.trino.svc.cluster.local
python - <<'PY'
import socket

for host, port in [
    ("trino.trino.svc.cluster.local", 8080),
    ("rustfs-s3.rustfs.svc.cluster.local", 9000),
]:
    with socket.create_connection((host, port), timeout=5):
        print(f"{host}:{port} reachable")
PY
```

This avoids guessing from the laptop. If the same hostname works locally but not
in the pod, or works in the pod but not locally, that is a routing/context
difference rather than a universal service outage.

## Packages And The Single-User Image

The single-user image is built from
`pulumi/data/analytics/jupyterhub/images/singleuser/Dockerfile` and pushed as
`ghcr.io/kzh/jupyter:py313-amd64`.

The current image starts from `quay.io/jupyter/minimal-notebook:python-3.13` and
adds the things the repo wants available to new user servers:

```text
Ubuntu 24.04 based Jupyter Docker Stacks image
Python 3.13 notebook environment
uv as a global package-management tool
JupyterHub single-user package
JupyterLab and Jupyter LSP packages
python-lsp-server
CPU PyTorch, torchvision, and torchaudio
native build tooling
Cairo/Pango headers
LaTeX packages used by MathTex and Manim-style workflows
```

It also sets `PYTHONNOUSERSITE=1`. That is a deliberate guard against packages
in `~/.local` silently shadowing image-installed libraries. If a package install
appears to succeed but the import still fails, check where it was installed.
For durable shared dependencies, prefer putting the dependency into the image
instead of relying on user-site installs.

Notebook-local installs are still useful for exploration:

```python
%pip install trino
```

or:

```python
%pip install mlflow
```

Treat that as a scratchpad move. If several notebooks need the same dependency,
or if the dependency is part of a workflow you want to reproduce, update the
single-user image with pinned versions and push a new image.

The image tag is mutable and the chart uses `pullPolicy: Always`. That means new
servers pull the current registry contents for `ghcr.io/kzh/jupyter:py313-amd64`.
That is convenient for a small private deployment, but it also means the tag
alone is not a full historical record of the bytes that ran. For a higher
reproducibility bar, use an explicit version tag or digest and update the Pulumi
value deliberately.

The project-local image commands are in `pulumi/data/analytics/jupyterhub/Justfile`:

```bash
cd pulumi/data/analytics/jupyterhub
just build
just push
```

Do not put secrets into the image. Put durable secrets in Kubernetes Secrets,
Pulumi config, ESC, or the owning service's secret path. A notebook image is a
runtime environment, not a credential store.

## Working With Cluster Services

The clean notebook pattern is to install or bake in the client library, point it
at the service endpoint appropriate for a pod, and keep credentials out of the
notebook whenever the server can proxy or own them.

For Trino, the in-cluster service is the natural path from JupyterHub:

```python
import trino

conn = trino.dbapi.connect(
    host="trino.trino.svc.cluster.local",
    port=8080,
    user="notebook",
    catalog="tpch",
    schema="tiny",
)

cur = conn.cursor()
cur.execute("select * from nation limit 5")
print(cur.fetchall())
```

That proves the notebook can reach Trino and Trino can answer a simple catalog
query. It does not prove every production catalog is healthy, so test the
catalog you actually plan to use.

For Spark Connect, use the Kubernetes service name from inside JupyterHub. If
the Spark namespace output is `spark` and the connect name output is
`spark-connect`, the target is:

```python
from pyspark.sql import SparkSession

spark = SparkSession.builder.remote(
    "sc://spark-connect.spark.svc.cluster.local:15002"
).getOrCreate()

spark.sql("select 1 as ok").show()
spark.sql("show namespaces in trino_iceberg").show(truncate=False)
spark.stop()
```

If that fails, check whether the Python client package is installed and whether
the Spark service DNS matches the current stack outputs. A notebook pod should
not need the laptop-oriented Tailscale hostname for ordinary in-cluster Spark
Connect access.

For MLflow, prefer the tracking API over direct object-store credentials. Use
the hostname that is valid from the notebook pod. `https://mlflow` is the
private ingress-style path; if that is not reachable from the pod, confirm the
service and port with `kubectl get svc -n mlflow` and use the in-cluster service
URL instead.

```python
import mlflow

mlflow.set_tracking_uri("https://mlflow")
mlflow.set_experiment("jupyterhub-smoke")

with mlflow.start_run():
    mlflow.log_param("source", "jupyterhub")
    mlflow.log_metric("ok", 1.0)
```

The MLflow stack is configured for proxied artifact storage, so clients should
not need RustFS access keys just to log artifacts. If direct S3 access is needed
for a separate use case, design that as an explicit secret and access pattern
rather than copying credentials into notebook cells.

## Moving Notebooks To Jobs

A useful notebook often starts as a mix of discovery, commentary, plotting,
data access, and half-finished checks. That is fine while learning. The move to
a job is the moment you separate the repeatable computation from the interactive
conversation around it.

Start by identifying the part of the notebook that should run again:

```text
inputs:          data locations, dates, table names, model names, parameters
pure logic:      transforms, queries, training steps, validations
outputs:         tables, objects, artifacts, metrics, reports
runtime:         Spark, Python job, Airflow task, Dagster asset, Slurm job
observability:   logs, MLflow runs, task status, output checks
ownership:       which repo path and stack should define the workflow
```

Then move the logic into normal Python modules or job code and leave the
notebook as a client, explanation, or visualization layer. A notebook can still
call the code, but it should not be the only copy of the code.

For example, this is notebook-shaped:

```python
df = spark.table("trino_iceberg.demo.events")
cleaned = df.where("event_time is not null")
cleaned.write.mode("overwrite").parquet("/some/path")
```

The job-shaped version has named inputs and outputs:

```python
def clean_events(spark, source_table: str, output_path: str) -> None:
    (
        spark.table(source_table)
        .where("event_time is not null")
        .write.mode("overwrite")
        .parquet(output_path)
    )
```

Once it is a function, a workflow system can pass parameters, retry it, log it,
and review changes in Git. The notebook can keep a small cell that imports the
function and runs it against a tiny sample.

Choose the runtime by the work, not by where the idea started:

```text
Use Airflow or Dagster when the main problem is scheduling, dependencies, and retries.
Use Spark when the main problem is distributed data transformation.
Use Trino when the main problem is federated SQL query access.
Use MLflow when the main problem is experiment tracking and artifact history.
Use Slurm when the main problem is batch-style compute with explicit resource requests.
Use a service when the main problem is an always-on API or application.
```

The notebook remains valuable after the move. It becomes the place to inspect
results, explain the analysis, compare runs, or debug a small slice without
making production state depend on a browser session.

## Debugging Spawn Failures

When a user server will not start, work from the Hub outward to the spawned pod.
The Hub knows what it asked Kubernetes to create, while Kubernetes knows whether
the pod, image pull, scheduling, and PVC attach succeeded.

Start broad:

```bash
kubectl get pods,svc,pvc,events -n jhub --sort-by=.metadata.creationTimestamp
kubectl logs -n jhub deploy/hub --tail=300
```

Then inspect the user pod:

```bash
kubectl get pods -n jhub -l component=singleuser-server -o wide
kubectl describe pod -n jhub <notebook-pod>
kubectl logs -n jhub <notebook-pod> --all-containers --tail=200
```

Common spawn blockers have different signatures:

```text
ImagePullBackOff:       image tag, registry access, architecture, or pushed image availability
Pending pod:            scheduling constraints, resource pressure, or PVC provisioning
ContainerCreating:      image pull still running, volume attach, or mount setup
Spawn timeout:          the pod did not become ready before the Hub's 600 second limit
PVC pending:            storage class/provisioner problem or access-mode mismatch
Immediate crash:        single-user image, command, environment, or permissions issue
```

If the image was just rebuilt, remember that the chart uses `pullPolicy: Always`
but already-running servers keep running the old container. Stop and start the
user server to get a new pod. If the registry tag was reused, verify the image
was actually pushed.

## Debugging Network Issues

Network debugging starts by naming the client location. A laptop, the Hub pod,
a single-user pod, a Spark driver, and a Trino worker can all have different DNS
search paths and route options.

For notebook code, test from the single-user pod:

```bash
kubectl exec -n jhub -it <notebook-pod> -- bash
```

Inside the pod:

```bash
getent hosts trino.trino.svc.cluster.local
python - <<'PY'
import socket

host = "trino.trino.svc.cluster.local"
port = 8080
with socket.create_connection((host, port), timeout=5):
    print("connected")
PY
```

If DNS fails, check the service name and namespace first. If DNS resolves but
the connection fails, inspect the target service and endpoints:

```bash
kubectl get svc,endpoints -n trino
kubectl get pods -n trino -o wide
```

If a browser URL works but a pod cannot use the same name, check whether the
browser path is going through Tailscale ingress while the pod should be using a
ClusterIP service. If a pod path works but the browser does not, check ingress,
TLS, and Tailscale exposure.

NetworkPolicy is disabled for the Hub in this stack. If you enable network
policies later, write down the intended traffic first: browser to proxy, proxy
to Hub, Hub to user pods, user pods to internal services, DNS, and any required
egress. Notebook failures caused by policy can look like package, Spark, Trino,
or MLflow failures until you test the path directly.

## Debugging Storage Issues

Storage problems usually show up as pending spawns, missing files, or permission
errors.

Start with PVCs:

```bash
kubectl get pvc -n jhub
kubectl describe pvc -n jhub <claim-name>
```

Then compare the pod's mounted volumes with where the notebook writes files:

```bash
kubectl describe pod -n jhub <notebook-pod>
kubectl exec -n jhub -it <notebook-pod> -- df -h
kubectl exec -n jhub -it <notebook-pod> -- sh -lc 'pwd; echo "$HOME"; ls -la "$HOME"'
```

If files disappear after restart, the first question is whether they were
written under the persistent home directory. Files in `/tmp`, image layers, or
other container-local paths are not user state. If files under the home
directory disappear, check whether the user got a different PVC because of a
username, server name, namespace, release, or chart template change.

`ReadWriteOnce` storage means a PVC normally attaches to one node at a time.
If a pod is stuck after a node disruption or fast restart, look for volume
attach/detach events in the pod description and namespace events.

## Debugging Package And Kernel Issues

Package failures belong to the single-user image unless the user intentionally
installed something inside the running notebook. Start by checking the active
Python executable and import path:

```python
import site
import sys

print(sys.executable)
print(sys.version)
print(site.getsitepackages())
print(sys.path)
```

If an import fails in a notebook but works in a different environment, confirm
that both environments are the same image and Python version. The current image
is Python 3.13. Some data and ML packages lag new Python releases, so pinning a
package may also require confirming Python 3.13 wheel support.

If the package is needed once, `%pip install ...` is fine. If it is needed by
the team or by a workflow, bake it into the image and push the image. Repeated
manual installs are slow, hard to reproduce, and easy to lose when servers are
recreated.

When updating the image, keep dependency changes explicit. A good image diff
answers:

```text
which package changed?
why does every notebook need it?
is the version pinned?
does it support Python 3.13 and linux/amd64?
does it require native libraries?
how did you test import and a tiny use case?
```

## Safe Changes

Treat JupyterHub changes as infrastructure changes even when they look like
notebook convenience work. The Hub controls user access to persistent notebook
state, and the single-user image controls every new server's runtime.

Use the repo commands for static validation and preview:

```bash
just sync pulumi/data/analytics/jupyterhub
just check-python
just lint
git diff --check -- docs/stacks/data/analytics/jupyterhub.md pulumi/data/analytics/jupyterhub
just preview pulumi/data/analytics/jupyterhub stack=mx
```

Do not apply or destroy this stack unless the user explicitly asks for that
operation. A preview is enough to understand the planned infrastructure change
before an apply is approved.

Review these changes with extra care:

```text
Chart version:       may change resource names, labels, auth behavior, defaults, and migrations
Namespace/release:   can orphan or replace Hub state and user PVCs
Hub database:        affects Hub state, not user notebook files, but still changes service behavior
User storage:        can affect every user's persistent home directory
Notebook image:      affects every new server and may break imports or kernels
Image tag policy:    mutable tags are convenient but less reproducible than version tags or digests
Ingress/auth:        changes who can reach the Hub and how login works
NetworkPolicy:       can block Hub-to-user or notebook-to-service traffic if incomplete
Scheduling knobs:    can leave servers pending if the cluster cannot satisfy the constraints
Cull settings:       changes whether idle user servers keep running
```

After an approved apply, verify the user path, not just Kubernetes readiness:

```text
open https://jupyterhub
start a user server
write and read a small file in the home directory
restart the server and read the file again
import the packages the image is expected to provide
connect to at least one internal service from the notebook pod
stop the server and confirm the Hub remains healthy
```

A healthy Hub pod is only the control-plane layer. JupyterHub is ready for use
when a real user can spawn a server, keep files across restarts, import the
expected libraries, and reach the cluster services the notebook is meant to
explore.
