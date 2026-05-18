# MLflow

Source: `pulumi/data/analytics/mlflow`

MLflow is the experiment memory for this repo. It does not train models by
itself, schedule jobs, run Spark, or serve production inference. Its job is to
record what happened when some other compute ran: the code version, data
reference, parameters, metrics, artifacts, plots, model files, and enough notes
to understand the result later.

That division is important. Notebooks, Spark drivers, Slurm jobs, Airflow tasks,
Dagster assets, and local scripts are compute surfaces. MLflow is the shared
tracking surface those runtimes can write to. If an experiment only exists in a
notebook output cell, it is easy to lose. If it is logged to MLflow, the run has
a durable identity, searchable metadata, and artifact storage that other tools
can refer to.

In this repo, MLflow has two backing stores:

```text
PostgreSQL:  experiments, runs, params, metrics, tags, model registry metadata
RustFS/S3:   artifact bytes, plots, reports, model files, environment files
```

The MLflow pod is the API and browser layer in front of those stores. Clients
talk to MLflow. MLflow talks to PostgreSQL and RustFS.

## What This Stack Deploys

The Pulumi project reads two producer stacks:

```text
PostgreSQL stack reference:  kzh/postgresql/mx by default
RustFS stack reference:      kzh/rustfs/mx by default
```

From PostgreSQL, it gets the cluster name, Kubernetes namespace, Tailscale
hostname, admin username, and admin password. Pulumi uses that connection to
create an MLflow database and role. The running MLflow server then connects to
PostgreSQL through the in-cluster read-write service:

```text
<cnpg_cluster_name>-rw.<postgres_namespace>.svc.cluster.local:5432
```

From RustFS, it gets the S3 service hostname, namespace, access key, and secret
key. Pulumi creates a Kubernetes Secret for the MLflow server and runs a bucket
bootstrap Job that creates the artifact bucket if it does not already exist.

The source currently pins and configures these repo-level choices:

```text
Helm chart:          community mlflow chart 1.8.1
Namespace:           mlflow by default
Ingress class:       tailscale
Ingress host:        mlflow by default
Public host:         same as ingress_host unless configured
Database name:       mlflow by default
Database role:       mlflow by default
Artifact bucket:     mlflow by default
S3 endpoint:         RustFS service DNS on port 9000
AWS region value:    us-east-1
Artifact mode:       proxied through the MLflow server
Bucket job image:    burakince/mlflow:3.7.0
```

The chart-managed PostgreSQL and MySQL options are disabled. The backend store
is the shared PostgreSQL stack, and the artifact root is the RustFS bucket.
Database migrations and database connection checks are enabled in the chart
values, so startup problems can surface as migration or connectivity failures
rather than only as generic pod failures.

The stack also generates the Flask server secret key and stores it in
Kubernetes. Do not copy secret values into docs, notebooks, issue comments, or
chat. For debugging, it is enough to verify that a Secret exists, that it has
the expected keys, and that the pod references it.

## How Requests Flow

There are three common paths to keep straight.

The browser path is private through Tailscale:

```text
browser -> https://<ingress_host> -> Tailscale ingress -> MLflow service -> MLflow pod
```

The metadata path is from the MLflow server to PostgreSQL:

```text
MLflow pod -> PostgreSQL read-write service -> MLflow database
```

The artifact path is from clients through the MLflow server to RustFS:

```text
client -> MLflow tracking API -> MLflow pod -> RustFS S3 endpoint -> artifact bucket
```

That last path is deliberate. The stack sets `proxiedArtifactStorage` to true,
which means a normal MLflow client does not need RustFS credentials just to log
artifacts. The client uploads an artifact to the tracking server, and the server
writes it to RustFS with its own S3 configuration.

This is safer and easier to operate than handing RustFS keys to every notebook
or training job. It also means artifact failures usually belong to the server's
RustFS configuration, the artifact bucket, or the server-to-RustFS network path,
not to a missing S3 key in the client.

## Open The Tracking UI

Read the hostname from the stack output rather than guessing:

```bash
cd pulumi/data/analytics/mlflow
pulumi stack output --stack mx ingress_host
```

Then open:

```text
https://<ingress_host>
```

The Helm values configure allowed hosts and CORS around the public host. If the
browser path is failing, use the intended host first. Port-forwarding can be
useful later, but it bypasses the ingress and host-header behavior this stack is
actually configured to use.

Useful non-secret outputs are:

```bash
cd pulumi/data/analytics/mlflow

pulumi stack output --stack mx namespace
pulumi stack output --stack mx chart_version
pulumi stack output --stack mx ingress_host
pulumi stack output --stack mx bucket
pulumi stack output --stack mx database
pulumi stack output --stack mx database_secret_name
```

Do not use `--show-secrets` for casual inspection. None of the ordinary user
workflows below require printing secret values.

## The Tracking URI

Every MLflow client needs to know where the tracking server is. If you do not
set the tracking URI, the Python client may write to a local `mlruns` directory.
That is fine for a one-off local experiment, but it is not this shared service.

For a laptop or any environment that can reach the private Tailscale ingress:

```bash
cd pulumi/data/analytics/mlflow
export MLFLOW_TRACKING_URI="https://$(pulumi stack output --stack mx ingress_host)"
```

In Python you can either rely on the environment variable or set it explicitly:

```python
import mlflow

mlflow.set_tracking_uri("https://mlflow")
```

The exact hostname depends on where the client runs. A laptop normally uses the
Tailscale hostname. A notebook pod may also use that private hostname, as in the
JupyterHub examples in this docs tree. If you want to use in-cluster service DNS
instead, inspect the live Service and use the port the chart exposes:

```bash
cd pulumi/data/analytics/mlflow
NS="$(pulumi stack output --stack mx namespace)"
kubectl get svc -n "$NS"
```

The important contract is not the spelling of one client URL. The important
contract is that all clients log to the same tracking server for shared
experiments.

## Experiments, Runs, Metrics, Artifacts, And Models

MLflow's vocabulary is small, and using it precisely makes the tracking server
much more useful.

An experiment is a named collection of related runs. Use one experiment for a
project, task, benchmark, dataset family, or model family. Good experiment names
are boring and searchable: `spark-feature-checks`, `notebook-baselines`,
`daily-forecasting`, `document-embedding-eval`.

A run is one execution. If you press "run all" in a notebook, submit a Spark
job, run a training script, or launch an Airflow task that trains a model, that
execution can be one MLflow run. A run gets an ID, timestamps, params, metrics,
tags, and an artifact location.

Parameters are inputs you chose: model type, learning rate, feature set,
dataset version, prompt template, Spark partition count, train/test split, or
any other knob that explains the run. Treat params as part of the identity of
the run. In MLflow, a parameter key in a run is not meant to be rewritten with a
different value later.

Metrics are measurements: accuracy, loss, latency, row count, runtime seconds,
mean absolute error, memory use, or an evaluation score. Metrics can be logged
over time with steps, so they work for training curves as well as final scores.

Tags are searchable context: git commit, notebook path, owner, environment,
data snapshot, job ID, feature branch, or whether the run is a smoke check,
baseline, candidate, or production comparison. Tags are a good place for stable
identifiers. They are not a place for secrets.

Artifacts are files: plots, reports, confusion matrices, sample predictions,
config files, model files, serialized vectorizers, schemas, or logs that are
safe to keep. In this repo those bytes land in RustFS, behind the MLflow server.

Models are a special kind of artifact plus metadata. When you call an MLflow
flavor helper such as `mlflow.sklearn.log_model`, the model files are stored as
artifacts and MLflow records enough metadata to load them later. If you register
a model, the registry metadata lives in PostgreSQL while the model files still
live in artifact storage.

That split is why backup and restore need both PostgreSQL and RustFS. A run
record without artifact bytes is incomplete. Artifact bytes without the run and
model metadata are hard to discover and compare.

## A First Python Run

This example proves the whole path: Python client, tracking API, PostgreSQL
metadata, RustFS artifacts, and browser visibility.

```python
from pathlib import Path

import mlflow

mlflow.set_tracking_uri("https://mlflow")
mlflow.set_experiment("docs-first-run")

with mlflow.start_run(run_name="hello-mlflow") as run:
    mlflow.set_tag("source", "docs/stacks/data/analytics/mlflow.md")
    mlflow.log_param("runtime", "python")
    mlflow.log_param("artifact_proxy", "enabled")
    mlflow.log_metric("ok", 1.0)

    report = Path("/tmp/mlflow-first-run.txt")
    report.write_text(
        f"MLflow run {run.info.run_id} wrote this artifact.\n",
        encoding="utf-8",
    )
    mlflow.log_artifact(str(report), artifact_path="reports")
```

After it runs, refresh the UI and check four things:

```text
the experiment exists
the run exists
the params and metric are visible
the report artifact can be opened or downloaded
```

If the first three work but the artifact does not, PostgreSQL is probably fine
and the investigation should move toward RustFS, the bucket, the S3 endpoint, or
artifact proxy behavior.

## Logging Useful Context

The best MLflow runs answer the questions you will ask later:

```text
What code produced this?
What data did it use?
What changed from the previous run?
What metric should I compare?
Where are the outputs?
Could I rerun it?
```

A training script can log that context directly:

```python
import os
import subprocess
from pathlib import Path

import mlflow


def optional_git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "unknown"


mlflow.set_tracking_uri(os.environ.get("MLFLOW_TRACKING_URI", "https://mlflow"))
mlflow.set_experiment("training-baselines")

with mlflow.start_run(run_name="baseline"):
    mlflow.set_tag("git_commit", optional_git_commit())
    mlflow.set_tag("entrypoint", "train.py")
    mlflow.log_param("dataset", "example-v1")
    mlflow.log_param("model_family", "ridge")
    mlflow.log_param("feature_set", "basic")
    mlflow.log_metric("validation_loss", 0.42)

    Path("/tmp/config.yaml").write_text("model: ridge\n", encoding="utf-8")
    mlflow.log_artifact("/tmp/config.yaml", artifact_path="config")
```

Prefer stable references over bulky copies. If the input data is already in a
database table, RustFS prefix, Iceberg table, or versioned export, log that
reference as a param or tag. Do not upload a large dataset as an MLflow artifact
just because the API allows files. MLflow artifacts are best for model outputs,
reports, plots, small configs, evaluation samples, and files that belong to the
run.

## Logging Metrics Over Time

Metrics can be a time series. That is useful for training loops, iterative
experiments, and evaluation batches:

```python
import math

import mlflow

mlflow.set_tracking_uri("https://mlflow")
mlflow.set_experiment("metric-series-demo")

with mlflow.start_run(run_name="curve"):
    for step in range(20):
        loss = math.exp(-step / 5)
        mlflow.log_metric("loss", loss, step=step)
```

Use step numbers that mean something. For model training they might be epochs or
batches. For evaluation they might be dataset slices. For a workflow task they
might be retry numbers or phases.

## Logging A Model

The stack is a tracking server and model registry surface, not a model serving
stack. You can log and register models here, but serving them is a separate
runtime decision.

Here is a small scikit-learn example:

```python
import mlflow
import mlflow.sklearn
from sklearn.datasets import load_diabetes
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_squared_error
from sklearn.model_selection import train_test_split

mlflow.set_tracking_uri("https://mlflow")
mlflow.set_experiment("model-logging-demo")

X, y = load_diabetes(return_X_y=True)
X_train, X_test, y_train, y_test = train_test_split(
    X,
    y,
    test_size=0.2,
    random_state=42,
)

alpha = 0.5
model = Ridge(alpha=alpha)
model.fit(X_train, y_train)
predictions = model.predict(X_test)
loss = mean_squared_error(y_test, predictions)

with mlflow.start_run(run_name="ridge") as run:
    mlflow.log_param("model_family", "ridge")
    mlflow.log_param("alpha", alpha)
    mlflow.log_metric("mse", loss)
    model_info = mlflow.sklearn.log_model(model, name="model")

    print(run.info.run_id)
    print(model_info.model_uri)
```

Use the returned model URI rather than rebuilding it by hand. If you decide a
model should be managed through the registry, register it under a clear name:

```python
registered = mlflow.register_model(
    model_info.model_uri,
    "docs-ridge-demo",
)
print(registered.name, registered.version)
```

Use registry names carefully. A registered model is a shared namespace. Names
such as `test` or `final` become confusing quickly. Prefer names that say what
the model is for, not how you felt about one run when you registered it.

## Using MLflow From JupyterHub

JupyterHub is the natural place to begin because notebook pods run near the
cluster services. The notebook should still log to the shared tracking server,
not to a local `mlruns` directory inside the user home.

Start a notebook with a small connection check:

```python
import mlflow

mlflow.set_tracking_uri("https://mlflow")
mlflow.set_experiment("notebook-checks")

with mlflow.start_run(run_name="jupyterhub-check"):
    mlflow.set_tag("runtime", "jupyterhub")
    mlflow.log_metric("ok", 1)
```

If the notebook image does not already include the MLflow client package, use a
notebook-local install for exploration:

```python
%pip install mlflow scikit-learn
```

Once the dependency becomes part of a repeated workflow, put it in the image or
job environment that owns that workflow. Reinstalling the same packages by hand
in many notebooks makes experiments harder to reproduce.

Also be deliberate about secrets in notebooks. A notebook should not need RustFS
access keys to log artifacts through this stack. If a notebook starts growing
extra S3 credential cells just for MLflow artifacts, step back and check whether
the tracking URI is pointing at this server and whether artifact proxying is
working.

## Using MLflow With Spark

Spark does the distributed compute. MLflow records the run. The clean pattern is
for the Spark driver to create or continue the MLflow run, log params and final
metrics, and attach compact artifacts such as reports or plots.

For Spark Connect exploration:

```python
from pathlib import Path

import mlflow
from pyspark.sql import SparkSession

mlflow.set_tracking_uri("https://mlflow")
mlflow.set_experiment("spark-mlflow-checks")

spark = SparkSession.builder.remote("sc://<spark-connect-host>:15002").getOrCreate()

with mlflow.start_run(run_name="spark-count"):
    mlflow.set_tag("runtime", "spark-connect")
    mlflow.log_param("spark_version", spark.version)

    df = spark.range(1000).selectExpr("id", "id % 10 as bucket")
    count = df.count()
    mlflow.log_metric("row_count", count)

    summary = df.groupBy("bucket").count().orderBy("bucket").toPandas()
    report_path = Path("/tmp/spark-buckets.csv")
    summary.to_csv(report_path, index=False)
    mlflow.log_artifact(str(report_path), artifact_path="reports")

spark.stop()
```

For distributed training, do not let every executor create its own unrelated
run unless that is the actual experiment design. A common pattern is:

```text
driver starts one MLflow run
driver logs dataset and job parameters
Spark executors process data
driver collects or computes final metrics
driver logs metrics and compact artifacts
large outputs are written to the proper data store
MLflow stores references and model artifacts
```

If the job writes a large table, the table belongs in the data plane, not as an
MLflow artifact. Log the table name, path, snapshot ID, or object prefix so the
run points to the data without duplicating it.

## Using MLflow From Workflow Jobs

Airflow and Dagster should own scheduling, retries, dependencies, and task
boundaries. MLflow should own experiment history.

For a task that trains or evaluates a model, set `MLFLOW_TRACKING_URI` in the
task environment and log from the task code:

```python
import os

import mlflow

mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
mlflow.set_experiment("workflow-training")

with mlflow.start_run(run_name="daily-eval"):
    mlflow.set_tag("scheduler", "airflow-or-dagster")
    mlflow.log_param("dataset", "daily-snapshot")
    mlflow.log_metric("score", 0.91)
```

Keep the run ID in task logs when it helps connect scheduler history to MLflow
history:

```python
with mlflow.start_run(run_name="daily-eval") as run:
    print(f"mlflow_run_id={run.info.run_id}")
```

That is usually enough. The scheduler does not need to know RustFS credentials
for MLflow artifacts, and MLflow does not need to become the scheduler.

## PostgreSQL And RustFS Are Both Part Of MLflow State

It is tempting to think "MLflow data" lives in one place. In this deployment it
does not.

PostgreSQL holds the structured tracking data:

```text
experiments
runs
params
metrics
tags
artifact URIs
registered model names
registered model versions
```

RustFS holds the object bytes:

```text
logged artifacts
plots and reports
serialized models
model flavor metadata files
environment files
other run output files
```

The MLflow server ties those two sides together. When you click an artifact in
the UI, the UI is reading run metadata from PostgreSQL and then fetching object
data through the artifact path backed by RustFS.

Operationally, that means:

```text
PostgreSQL restore without RustFS restore: runs may exist but artifacts can be missing.
RustFS restore without PostgreSQL restore: objects may exist but MLflow may not know them.
Bucket rename: old runs may still point at the old artifact location.
Database rename: the server may start with an empty history or fail migrations.
StackReference output change: consumers can break without code changing in MLflow.
```

Treat the PostgreSQL database name, database role, RustFS endpoint, artifact
bucket, and artifact proxy mode as data contracts. They are not cosmetic Helm
values.

## What Not To Log

Do not log secrets as params, tags, metrics, artifacts, model metadata, or text
notes. MLflow is designed to make experiment data easy to browse. That is useful
for research and harmful for accidental credentials.

Avoid logging:

```text
API keys
database passwords
private keys
session cookies
raw kubeconfigs
decrypted Pulumi secrets
full environment dumps
private user identifiers that are not needed for the experiment
```

Log stable references instead:

```text
secret name or config key, if that name is safe to expose
dataset version
table name
object prefix
git commit
container image tag
workflow run ID
Spark application ID
```

Be careful with automatic logging features. Autologging can be helpful, but it
can also capture more than you intended. Use explicit logging for sensitive
workflows or inspect what the framework logs before adopting it broadly.

## Debug The Browser Path First

If the UI does not load, start with Kubernetes and ingress state:

```bash
cd pulumi/data/analytics/mlflow
NS="$(pulumi stack output --stack mx namespace)"

kubectl get pods,deploy,svc,ingress,job -n "$NS"
kubectl describe ingress -n "$NS"
kubectl get events -n "$NS" --sort-by=.lastTimestamp | tail -40
```

Then read the server logs:

```bash
kubectl logs -n "$NS" deploy/mlflow --tail=200
```

A browser failure can be a pod failure, Service selector issue, Tailscale
ingress problem, allowed-host mismatch, TLS issue, or a server startup failure.
Do not jump straight to the client library until the UI path is understood.

The chart is configured with:

```text
allowedHosts:        <public_host>,localhost:*
corsAllowedOrigins:  https://<public_host>
```

If a host works through one name but not another, compare the browser hostname
with `ingress_host` and `public_host` configuration before changing code.

## Debug Tracking Writes

If the UI loads but a script does not show new runs, first confirm where the
client is logging:

```python
import mlflow

print(mlflow.get_tracking_uri())
```

If that prints a local path or a hostname you did not intend, fix the tracking
URI before debugging the server.

A compact write check is:

```python
import mlflow

mlflow.set_tracking_uri("https://mlflow")
mlflow.set_experiment("write-check")

with mlflow.start_run(run_name="metadata-only"):
    mlflow.log_param("path", "metadata")
    mlflow.log_metric("ok", 1)
```

If this fails, inspect server logs and PostgreSQL connectivity. The MLflow pod
uses a Kubernetes Secret for database username and password. Verify the Secret
without printing its values:

```bash
cd pulumi/data/analytics/mlflow
NS="$(pulumi stack output --stack mx namespace)"
DB_SECRET="$(pulumi stack output --stack mx database_secret_name)"

kubectl describe secret -n "$NS" "$DB_SECRET"
kubectl logs -n "$NS" deploy/mlflow --tail=200
```

`kubectl describe secret` shows key names and sizes, not decoded values. Avoid
`kubectl get secret -o yaml` during routine debugging.

## Debug Artifact Writes

If a run appears but artifact upload fails, the metadata path worked and the
artifact path did not. Start with the bucket bootstrap Job:

```bash
cd pulumi/data/analytics/mlflow
NS="$(pulumi stack output --stack mx namespace)"

kubectl get job -n "$NS" mlflow-create-bucket
kubectl logs -n "$NS" job/mlflow-create-bucket --tail=100
```

The Job uses the same RustFS endpoint shape and S3 credentials source as the
stack's artifact setup. If it failed, look for RustFS reachability, bucket
creation, or credential reference problems. If it succeeded, the running MLflow
server can still be misconfigured, so check the server logs and deployment env
names:

```bash
kubectl logs -n "$NS" deploy/mlflow --tail=200
kubectl get deploy -n "$NS" mlflow \
  -o jsonpath='{range .spec.template.spec.containers[0].env[*]}{.name}{"\n"}{end}'
```

The deployment should have the non-secret S3 endpoint setting and should
reference Secrets for credentials. You rarely need the actual credential values
to debug an artifact path.

A useful artifact-only Python check is:

```python
from pathlib import Path

import mlflow

mlflow.set_tracking_uri("https://mlflow")
mlflow.set_experiment("artifact-check")

artifact = Path("/tmp/artifact-check.txt")
artifact.write_text("artifact upload check\n", encoding="utf-8")

with mlflow.start_run(run_name="artifact-upload"):
    mlflow.log_artifact(str(artifact))
```

If metadata logs but this fails, stay on RustFS, S3 endpoint, bucket, or
artifact proxying until proven otherwise.

## Reading Common Failures

The UI works but no new runs appear. The client is often writing somewhere else.
Print `mlflow.get_tracking_uri()` from the same process that logs the run.

The UI works and runs appear, but artifact upload fails. PostgreSQL is probably
not the failing component. Inspect the bucket Job, RustFS endpoint, S3 Secret
references, `MLFLOW_S3_ENDPOINT_URL`, and MLflow server logs.

The server starts with database migration errors. The chart has database
migration enabled. Check PostgreSQL reachability, the MLflow database and role,
and whether a chart upgrade changed migration expectations.

The browser reports a host or origin problem. Compare the browser URL with the
stack's `ingress_host`, `public_host`, `allowedHosts`, and `corsAllowedOrigins`.

Old runs appear but artifacts are missing. Check whether the bucket name,
artifact root, RustFS data, or artifact path changed. Run metadata can outlive
the object bytes it points at.

The artifact bucket exists but uploads still fail. Bucket creation is only one
piece. The live MLflow server still needs the endpoint, region value, credential
Secret, and network path to RustFS.

The pod is healthy but the service is not useful. For MLflow, a healthy pod is
only a starting point. The real proof is logging a run and reading back an
artifact through the UI or API.

## Change The Stack Safely

This stack is small, but it sits on durable data. Be especially careful with:

```text
chart version
database name
database role name
PostgreSQL stack reference
RustFS stack reference
artifact bucket name
artifact proxy mode
S3 endpoint construction
ingress host
public host
generated secret resources
the bucket bootstrap Job
the deployment transformation that adds pulumi.com/patchForce
```

Changing any of those can alter where MLflow looks for history or where it
writes new artifacts. A preview can show a Kubernetes replacement, but it cannot
by itself tell you whether old runs still point to the right object paths.

For code changes in this project, use the repo's normal gates:

```bash
just sync pulumi/data/analytics/mlflow
just check-python
just lint
git diff --check -- docs/stacks/data/analytics/mlflow.md pulumi/data/analytics/mlflow
just preview pulumi/data/analytics/mlflow stack=mx
```

Do not run an apply command unless the user explicitly asked for an apply. When
an apply is approved and completed, verify the user path, not just Kubernetes
readiness:

```text
open the UI through the intended hostname
log a metadata-only run
log an artifact
open the artifact from the UI
optionally log and load a tiny model
```

If the PostgreSQL producer stack changes, preview MLflow as a consumer. If the
RustFS producer stack changes, preview MLflow and any other object-store
consumers. Producer outputs are internal APIs in this repo.

## A Practical Working Style

Use MLflow for comparison, not as a dumping ground. A good run is compact,
searchable, and reproducible:

```text
experiment name says what is being studied
run name says what this attempt changed
params capture chosen inputs
metrics capture measured outputs
tags capture stable context
artifacts capture files worth keeping
models are logged when they are useful to load or register later
```

A notebook can be the first draft of an experiment. A Spark job can do the heavy
work. A workflow system can schedule it. MLflow gives all of those runtimes a
shared memory.

When in doubt, run the smallest end-to-end check: set the tracking URI, log one
param, one metric, and one artifact, then read them back in the UI. That single
round trip exercises the parts of the stack that matter most.
