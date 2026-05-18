# RustFS

Source: `pulumi/data/storage/rustfs`

RustFS is this repo's S3-compatible object store. It is the place to put
durable file-like data when the right model is "named bytes in a bucket" rather
than "rows in a database" or "files mounted into one pod." Model artifacts,
Iceberg table data, exported datasets, checkpoint files, backups, and ad hoc
data drops all fit that model.

The most useful first-principles distinction is this: object storage is an API,
not a mounted filesystem. A client does not open `/data/foo.parquet` and write
through POSIX calls. It sends HTTP requests such as "put this object in this
bucket under this key" and "read this object back." That is why very different
systems can share RustFS. MLflow can store experiment artifacts there. Trino can
write Iceberg table files there. A batch job can upload a report there. They
all speak the same S3-shaped protocol even though they use the data
differently.

Object storage is also not a database. RustFS stores objects and metadata about
objects. It does not know that an MLflow artifact belongs to a run, or that an
Iceberg data file belongs to a table snapshot. Those meanings live in the
consumer systems. Treat RustFS as the storage plane and treat the consumer's
metadata store as part of the same data contract.

## What This Stack Owns

The RustFS Pulumi project installs the RustFS Helm chart and adds the repo's
access layer around it. In the current program it:

```text
installs Helm chart:          rustfs
uses chart version:           0.3.0
creates namespace:            rustfs, unless rustfs:namespace is changed
runs mode:                    standalone
sets replica count:           1
uses storage class:           local-path, unless rustfs:storageClassName is set
creates console Service:      rustfs-console on port 9001
creates console Ingress:      rustfs through the tailscale ingress class
creates S3 Service:           rustfs-s3 on port 9000
exposes S3 Service as:        rustfs-s3 through Tailscale annotations
generates access key:         Pulumi secret output
generates secret key:         Pulumi secret output
exports endpoint coordinates: namespace, console hostname, S3 hostname
```

The standalone shape is important. This is a small homelab object store, not a
multi-node distributed object-storage cluster. Runtime objects can be recreated
from Pulumi, but the bytes in the backing volume are state. Treat the PVC and
the objects inside it as durable infrastructure state.

The RustFS stack does not create application buckets. That is deliberate.
RustFS owns the storage service. Consumer stacks own the buckets, prefixes, and
application-level verification workflows they require.

## The Object Model

The core S3-compatible model has a few nouns:

```text
bucket       a top-level namespace for objects
object       bytes plus metadata, addressed by a key inside a bucket
key          the object's name, such as runs/123/model.pkl
prefix       a naming convention over keys, such as runs/ or warehouse/
endpoint     the HTTP API location a client talks to
credentials  the access key and secret key used to sign requests
region       a client-side setting; this repo uses us-east-1 for consumers
```

Prefixes are not directories in the POSIX sense. A key named
`warehouse/lab/table/data-000.parquet` can make a console or CLI display a
folder-like tree, but RustFS is still storing an object with that exact key.
That difference matters for tools that list, rename, or delete "folders."
Deleting a prefix means deleting every object whose key starts with that text.
Renaming a prefix usually means copying objects to new keys and deleting the
old keys.

Buckets are stronger contracts than prefixes. A bucket name appears in client
configuration, application code, table metadata, run metadata, backup scripts,
and examples. Renaming or deleting a bucket is a data migration, not a cosmetic
cleanup.

Objects are usually easy to write and hard to interpret later unless the owner
is clear. A bucket with a known owner, lifecycle, and path convention is useful.
A bucket full of anonymous one-off uploads becomes difficult to back up, clean
up, or restore.

## Endpoints

RustFS has two different faces in this repo:

```text
Console for humans:   https://rustfs
S3 API from tailnet:  http://rustfs-s3:9000
S3 API in cluster:   http://rustfs-s3.rustfs.svc.cluster.local:9000
```

The console and the S3 API are separate on purpose. The console is a browser UI
for humans. Applications should use the S3 API. If the console loads but MLflow
artifacts or Trino Iceberg writes fail, the RustFS pod may still be fine. Check
the S3 endpoint, bucket, credentials, and client behavior before changing
console ingress.

The in-cluster endpoint is built from RustFS stack outputs. MLflow and Trino
both use this shape:

```text
http://<s3_hostname>.<namespace>.svc.cluster.local:9000
```

With the current defaults that becomes:

```text
http://rustfs-s3.rustfs.svc.cluster.local:9000
```

From a laptop or another trusted machine on the tailnet, use the Tailscale
hostname and port:

```text
http://rustfs-s3:9000
```

Do not treat those as interchangeable during debugging. A successful laptop
test proves the tailnet path and the credentials used by that shell. It does
not prove that a pod can resolve the cluster DNS name, that the pod has the
right Kubernetes Secret, or that the consumer's bucket exists. Likewise, a
successful in-cluster test does not prove the tailnet route works.

Read endpoint coordinates from Pulumi outputs:

```bash
cd pulumi/data/storage/rustfs

NS="$(pulumi stack output --stack mx namespace)"
S3_HOST="$(pulumi stack output --stack mx s3_hostname)"
CONSOLE_HOST="$(pulumi stack output --stack mx console_hostname)"

printf 'Console: https://%s\n' "$CONSOLE_HOST"
printf 'S3 from tailnet: http://%s:9000\n' "$S3_HOST"
printf 'S3 from cluster: http://%s.%s.svc.cluster.local:9000\n' "$S3_HOST" "$NS"
```

## Credentials

The RustFS stack generates the access key and secret key with Pulumi random
password resources. It passes those values into the RustFS chart and exports
them as Pulumi secret outputs:

```text
access_key
secret_key
```

The non-secret outputs can be read freely:

```bash
cd pulumi/data/storage/rustfs

pulumi stack output --stack mx namespace
pulumi stack output --stack mx chart_version
pulumi stack output --stack mx console_hostname
pulumi stack output --stack mx s3_hostname
```

The credential outputs are secret outputs. A normal output read should show
that the output exists without revealing the value:

```bash
pulumi stack output --stack mx access_key
pulumi stack output --stack mx secret_key
```

Use `--show-secrets` only in a local shell when a client needs the values:

```bash
ACCESS_KEY="$(pulumi stack output --stack mx --show-secrets access_key)"
SECRET_KEY="$(pulumi stack output --stack mx --show-secrets secret_key)"
```

Do not paste the resulting values into docs, chat, commit messages, tickets, or
logs. When writing examples, use environment variables, Kubernetes Secrets, or
Pulumi outputs as placeholders. When debugging Kubernetes, prefer commands that
show shape and references rather than decoded secret data.

Consumer stacks should not copy credential literals. They should read the
RustFS outputs through `StackReference` and materialize credentials into their
own namespace in a Kubernetes Secret. That is what the current MLflow and Trino
stacks do.

Credential rotation is a cross-stack change. If the RustFS generated keys are
replaced, every consumer that reads those outputs needs to reconcile its Secret,
and every running process that consumed the old Secret through environment
variables may need to restart before it uses the new key.

## Buckets And Ownership

RustFS provides the object-store service. Buckets belong to consumers.

The current repo-owned buckets are:

| Bucket | Owner stack | Created by | Used for |
| --- | --- | --- | --- |
| `mlflow` | `pulumi/data/analytics/mlflow` | Job `mlflow-create-bucket` | MLflow artifacts |
| `trino-iceberg` | `pulumi/data/analytics/trino` | Job `trino-iceberg-bucket` | Iceberg table files under `warehouse/` |

MLflow creates its bucket with a Python bootstrap job that calls `head_bucket`
and creates the bucket if RustFS reports that it does not exist. Trino creates
its Iceberg bucket with a `mc mb --ignore-existing` bootstrap job. Both jobs are
idempotent because bucket creation should be safe to repeat during normal
reconciliation.

That ownership split keeps the storage stack small and prevents the RustFS
project from needing to know every application data layout. It also gives each
consumer a better verification path. MLflow is not verified by "bucket exists";
it is verified by logging a run with an artifact through the MLflow API. Trino
Iceberg is not verified by "bucket exists"; it is verified by creating,
inserting into, and reading an Iceberg table.

For a new bucket, choose and document:

```text
owning stack
bucket name
who may write to it
expected prefixes
whether objects are raw data, backups, artifacts, or table-managed files
how it is backed up
how a restore is verified
who may delete test data
```

Prefer creating durable buckets in the owning consumer stack. A manual bucket
is fine for a short investigation, but do not leave unowned data behind. If a
manual bucket becomes useful, move its creation into Pulumi or document the
owner and retention expectation.

## Client Examples

Most S3-compatible clients need the same inputs:

```text
endpoint URL
access key
secret key
region, usually us-east-1 in this repo
bucket name
path-style addressing for clients that need it
```

Some S3 clients default to virtual-hosted bucket addressing, where the bucket
becomes part of the hostname. With private service names, path-style addressing
is usually the less surprising choice. Trino sets
`s3.path-style-access=true`. For Python clients, set path-style explicitly when
the library supports it.

### AWS CLI From The Tailnet

Use this from a trusted shell on a machine that can reach the `rustfs-s3`
tailnet hostname:

```bash
cd pulumi/data/storage/rustfs

HOST="$(pulumi stack output --stack mx s3_hostname)"
ACCESS_KEY="$(pulumi stack output --stack mx --show-secrets access_key)"
SECRET_KEY="$(pulumi stack output --stack mx --show-secrets secret_key)"

AWS_ACCESS_KEY_ID="$ACCESS_KEY" \
AWS_SECRET_ACCESS_KEY="$SECRET_KEY" \
AWS_DEFAULT_REGION="us-east-1" \
aws --endpoint-url "http://$HOST:9000" s3api list-buckets
```

A reversible write/read/delete check:

```bash
BUCKET="rustfs-docs-verification"
KEY="roundtrip.txt"
OBJECT="/tmp/$KEY"

printf 'rustfs round trip\n' > "$OBJECT"

AWS_ACCESS_KEY_ID="$ACCESS_KEY" \
AWS_SECRET_ACCESS_KEY="$SECRET_KEY" \
AWS_DEFAULT_REGION="us-east-1" \
aws --endpoint-url "http://$HOST:9000" s3 mb "s3://$BUCKET"

AWS_ACCESS_KEY_ID="$ACCESS_KEY" \
AWS_SECRET_ACCESS_KEY="$SECRET_KEY" \
AWS_DEFAULT_REGION="us-east-1" \
aws --endpoint-url "http://$HOST:9000" s3 cp "$OBJECT" "s3://$BUCKET/$KEY"

AWS_ACCESS_KEY_ID="$ACCESS_KEY" \
AWS_SECRET_ACCESS_KEY="$SECRET_KEY" \
AWS_DEFAULT_REGION="us-east-1" \
aws --endpoint-url "http://$HOST:9000" s3 cp "s3://$BUCKET/$KEY" -

AWS_ACCESS_KEY_ID="$ACCESS_KEY" \
AWS_SECRET_ACCESS_KEY="$SECRET_KEY" \
AWS_DEFAULT_REGION="us-east-1" \
aws --endpoint-url "http://$HOST:9000" s3 rm "s3://$BUCKET/$KEY"

AWS_ACCESS_KEY_ID="$ACCESS_KEY" \
AWS_SECRET_ACCESS_KEY="$SECRET_KEY" \
AWS_DEFAULT_REGION="us-east-1" \
aws --endpoint-url "http://$HOST:9000" s3 rb "s3://$BUCKET"
```

Use a throwaway bucket name for verification. Do not run write/delete examples
against `mlflow`, `trino-iceberg`, or any bucket with unclear ownership.

### Python With Boto3

Keep credentials outside the source file. For a pod, put them in a Kubernetes
Secret. For a local test, put them in environment variables:

```python
import os

import boto3
from botocore.config import Config

s3 = boto3.client(
    "s3",
    endpoint_url=os.environ["S3_ENDPOINT_URL"],
    aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
    aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
    region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
    config=Config(s3={"addressing_style": "path"}),
)

for bucket in s3.list_buckets()["Buckets"]:
    print(bucket["Name"])
```

Inside Kubernetes, `S3_ENDPOINT_URL` should normally be the cluster DNS endpoint:

```text
http://rustfs-s3.rustfs.svc.cluster.local:9000
```

From a laptop on the tailnet, it should normally be:

```text
http://rustfs-s3:9000
```

### Using MinIO Client

The Trino bootstrap job uses the MinIO client image because `mc` works well
against S3-compatible object stores. For local use, prefer passing credentials
through the environment or an ephemeral alias rather than writing secrets into
shared shell snippets:

```bash
mc alias set rustfs "http://$HOST:9000" "$ACCESS_KEY" "$SECRET_KEY"
mc ls rustfs
mc ls rustfs/trino-iceberg
```

Be mindful that commands with expanded secrets can end up in shell history,
terminal scrollback, or process listings. Use a local terminal, avoid copying
the output into tickets or docs, and remove temporary aliases if they are no
longer needed.

## MLflow As A Consumer

MLflow uses RustFS for artifact bytes. Its run metadata lives in PostgreSQL.
That split is the whole shape:

```text
MLflow run, params, metrics, metadata -> PostgreSQL
MLflow artifacts, models, plots, files -> RustFS bucket mlflow
```

The MLflow stack reads RustFS through a default stack reference:

```text
kzh/rustfs/mx
```

It builds the in-cluster S3 endpoint from the RustFS `s3_hostname` and
`namespace` outputs:

```text
http://rustfs-s3.rustfs.svc.cluster.local:9000
```

It creates a Kubernetes Secret named `mlflow-artifacts-s3` with these keys:

```text
AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY
```

It creates the `mlflow` bucket with the `mlflow-create-bucket` Job, then
configures the MLflow chart with proxied artifact storage. Proxied artifact
storage matters because normal MLflow clients should not need RustFS
credentials. A notebook or training job logs artifacts to MLflow, and the
MLflow server writes those bytes into RustFS.

When MLflow artifacts fail, separate the metadata path from the artifact path.
If a run appears in the UI but the artifact is missing or cannot be downloaded,
PostgreSQL is probably working and the problem is likely in the RustFS
endpoint, bucket, S3 Secret, or MLflow artifact configuration.

Useful checks:

```bash
cd pulumi/data/analytics/mlflow
NS="$(pulumi stack output --stack mx namespace)"

kubectl get pods,svc,ingress,jobs,secrets -n "$NS"
kubectl logs -n "$NS" job/mlflow-create-bucket --tail=100
kubectl logs -n "$NS" deploy/mlflow --tail=200
kubectl describe secret -n "$NS" mlflow-artifacts-s3
```

The end-to-end verification is to log a run with an artifact through the MLflow
client and then open that artifact in the UI. That proves the tracking server,
database, artifact proxy, RustFS credentials, bucket, and object read path
together.

## Trino And Iceberg As Consumers

Trino uses RustFS through its Iceberg catalog. The table files live in RustFS.
The Iceberg catalog metadata lives in PostgreSQL. The query engine is Trino.

The important chain is:

```text
Trino SQL
  -> iceberg catalog
  -> PostgreSQL JDBC catalog metadata
  -> RustFS objects under s3://trino-iceberg/warehouse
```

The Trino stack also reads RustFS through `kzh/rustfs/mx`. It creates a
Kubernetes Secret named `trino-catalog-credentials` containing S3 credentials
under these keys:

```text
TRINO_S3_ACCESS_KEY
TRINO_S3_SECRET_KEY
```

It creates the `trino-iceberg` bucket with the `trino-iceberg-bucket` Job. The
Iceberg warehouse path is:

```text
s3://trino-iceberg/warehouse
```

The rendered Iceberg catalog enables native S3 support, sets the RustFS
endpoint, uses region `us-east-1`, enables path-style access, and reads the S3
keys from environment variables:

```text
fs.native-s3.enabled=true
s3.endpoint=http://rustfs-s3.rustfs.svc.cluster.local:9000
s3.region=us-east-1
s3.path-style-access=true
```

If `tpch` queries work but Iceberg table creation fails, Trino is not entirely
down. Focus on the RustFS bucket job, S3 credentials, S3 endpoint, path-style
configuration, Iceberg PostgreSQL metadata database, and the warehouse path.

Useful checks:

```bash
cd pulumi/data/analytics/trino
NS="$(pulumi stack output --stack mx namespace)"

kubectl get pods,svc,endpoints,jobs,secrets -n "$NS"
kubectl logs -n "$NS" job/trino-iceberg-bucket --tail=100
kubectl logs -n "$NS" job/trino-iceberg-catalog-tables --tail=100
kubectl describe secret -n "$NS" trino-catalog-credentials
kubectl logs -n "$NS" -l app.kubernetes.io/component=coordinator --tail=200
```

The end-to-end verification is an Iceberg round trip:

```sql
show schemas from iceberg;

create schema if not exists iceberg.docs_verification;

create table if not exists iceberg.docs_verification.storage_roundtrip (
    id bigint,
    note varchar
);

insert into iceberg.docs_verification.storage_roundtrip
values (1, 'object storage round trip');

select *
from iceberg.docs_verification.storage_roundtrip;
```

That proves Trino can write table metadata to PostgreSQL, write object files to
RustFS, and read the resulting table back through the Iceberg catalog.

Do not manually edit objects under the Iceberg warehouse. Iceberg tracks table
state with metadata files, manifests, snapshots, and references in the catalog.
Deleting or moving a file that looks unused can corrupt a table even when the
bucket still exists. Use Iceberg and Trino operations to create, rewrite, expire
snapshots, or drop table data.

## Other Clients

Prefer the highest-level API that preserves the meaning of the data:

```text
experiment artifacts      use MLflow
Iceberg tables            use Trino's Iceberg catalog
raw object dumps          use a direct S3 client
application backups       use the application's backup and restore process
```

Direct S3 is the right tool for raw object workflows, backup copies, smoke
tests, and clients that truly own their bucket. It is usually the wrong tool
for editing MLflow internals or Iceberg warehouse files, because those systems
have metadata outside RustFS.

Spark is worth calling out because it also speaks Iceberg. In the current repo,
Spark is wired to the same RustFS-backed Iceberg warehouse that Trino uses. The
catalog names differ by engine:

```text
Spark: trino_iceberg.<schema>.<table>
Trino: iceberg.<schema>.<table>
```

The shared contract is the RustFS warehouse at `s3://trino-iceberg/warehouse`
plus the PostgreSQL JDBC catalog metadata named `trino_iceberg`. Do not edit
the warehouse objects directly; use Iceberg through Spark or Trino so metadata
and files stay consistent.

## Storage, PVCs, And Durability

The Pulumi program sets RustFS to standalone mode with one replica and tells the
chart to use the configured storage class, defaulting to `local-path`. The chart
owns the concrete workload and PVC details. When operating the stack, inspect
the live Kubernetes objects rather than guessing the rendered names:

```bash
cd pulumi/data/storage/rustfs
NS="$(pulumi stack output --stack mx namespace)"

kubectl get pods,sts,deploy,svc,ingress,pvc,pv -n "$NS" -o wide
kubectl describe pvc -n "$NS"
```

With `local-path`, assume the persistent data is tied closely to the cluster's
local storage behavior unless you have verified otherwise. A pod restart is not
the same thing as data loss, but node disk failure, PV deletion, PVC
replacement, or storage-class migration can be data-affecting events.

The PVC is not just implementation detail. It is where RustFS keeps the object
data that backs every bucket. Before changing storage class, chart values,
volume names, workload names, or anything that might replace a PVC, decide how
the existing objects will move and how each consumer will be verified after the
move.

Watch for storage pressure:

```bash
kubectl get pvc -n "$NS"
kubectl describe pod -n "$NS" -l app.kubernetes.io/name=rustfs
kubectl logs -n "$NS" -l app.kubernetes.io/name=rustfs --tail=200
kubectl get events -n "$NS" --sort-by=.lastTimestamp
```

If writes start failing after the service has been stable, include capacity and
PVC health in the first pass. Not every S3 write failure is a credential or
network issue.

## Backup And Restore

Backups need to match the consumer's meaning, not just copy bytes.

For raw buckets, a bucket sync to another object store or backup location can
be enough:

```bash
AWS_ACCESS_KEY_ID="$ACCESS_KEY" \
AWS_SECRET_ACCESS_KEY="$SECRET_KEY" \
AWS_DEFAULT_REGION="us-east-1" \
aws --endpoint-url "http://$HOST:9000" s3 sync \
  "s3://<bucket-name>/" \
  "/path/to/backup/<bucket-name>/"
```

That example shows the shape only. Choose a real backup target deliberately,
protect the credentials, and do not paste object listings or secret-bearing
commands into shared notes.

For MLflow, a useful backup includes both sides:

```text
PostgreSQL MLflow metadata database
RustFS mlflow bucket objects
the Pulumi commit/config that defines the stack shape
```

Restoring only the bucket can leave artifacts without the run metadata that
explains them. Restoring only PostgreSQL can leave runs pointing at missing
artifact objects.

For Trino Iceberg, a useful backup includes:

```text
PostgreSQL Iceberg JDBC catalog database
RustFS trino-iceberg bucket objects under warehouse/
the Trino catalog configuration that names the warehouse and catalog
```

Restore the catalog metadata and object files to a consistent point in time.
Iceberg metadata points at object paths. If the metadata says a snapshot uses a
file and that file is absent, the table can fail even though the bucket exists.
If object files exist but the catalog metadata was restored to an older point,
the extra files may be unreachable from the table.

For a RustFS-level disaster recovery exercise, the safe order is:

```text
1. Recreate or repair the RustFS service from Pulumi.
2. Restore bucket/object data into RustFS without exposing credentials.
3. Restore any consumer metadata stores that must match the objects.
4. Verify direct S3 list/read/write behavior on a non-critical object.
5. Verify MLflow through a run artifact.
6. Verify Trino through an Iceberg read/write round trip.
7. Only then remove old temporary restore data or obsolete buckets.
```

Do not use the web console as the only backup mechanism. It is useful for
inspection, but repeatable backups should be scriptable, logged, and tied to
the owning system's restore plan.

## Debugging RustFS Itself

Start with the stack outputs and the Kubernetes object shape:

```bash
cd pulumi/data/storage/rustfs
NS="$(pulumi stack output --stack mx namespace)"

pulumi stack output --stack mx namespace
pulumi stack output --stack mx chart_version
pulumi stack output --stack mx console_hostname
pulumi stack output --stack mx s3_hostname

kubectl get pods,svc,ingress,endpoints,endpointslice,pvc,secrets -n "$NS"
kubectl get endpoints -n "$NS" rustfs-s3
kubectl get endpoints -n "$NS" rustfs-console
kubectl logs -n "$NS" -l app.kubernetes.io/name=rustfs --tail=200
kubectl get events -n "$NS" --sort-by=.lastTimestamp
```

If a Service has no endpoints, compare selectors and pod labels before changing
clients:

```bash
kubectl describe svc -n "$NS" rustfs-s3
kubectl get pods -n "$NS" --show-labels
kubectl get endpointslice -n "$NS" -l kubernetes.io/service-name=rustfs-s3 -o yaml
```

The S3 Service selector in Pulumi expects chart pods with:

```text
app.kubernetes.io/instance=rustfs
app.kubernetes.io/name=rustfs
```

A chart upgrade that changes labels can break routing even if the pod is
healthy. In that case the durable fix belongs in Pulumi: update the Service
selector, add aliases/migration steps if names changed, and preview the stack.

For credential questions, inspect secret existence and key names without
printing decoded values:

```bash
kubectl get secrets -n "$NS"
kubectl describe secret -n "$NS" <rustfs-chart-secret-name>
kubectl describe secret -n mlflow mlflow-artifacts-s3
kubectl describe secret -n trino trino-catalog-credentials
```

For client behavior, run a direct S3 test from the same place the failing client
runs. If the failing client is a pod, test from a pod or inspect that pod's
environment and Secret references. If the failing client is your laptop, test
from the laptop against the tailnet hostname.

## Common Failure Patterns

Console loads, but S3 clients fail.

The console path and S3 path are different. Check the `rustfs-s3` Service,
endpoints, port `9000`, Tailscale exposure, endpoint URL, credentials, and
path-style behavior.

S3 list works, but writes fail.

Check bucket permissions, bucket existence, object key, available storage, PVC
events, and server logs. A list operation does not prove the backing volume can
accept new data.

Client gets `NoSuchBucket` or a 404.

The endpoint and credentials may be fine, but the bucket is absent or the
client is pointed at the wrong bucket. For repo-owned buckets, inspect the
owning bootstrap job rather than creating a second bucket by hand.

Client gets `AccessDenied`, `SignatureDoesNotMatch`, or a 403.

Check that the access key and secret key are a matching pair from the same
RustFS stack, that the consumer Secret reconciled after any rotation, and that
the client is using the intended endpoint. Do not print the secret values while
checking.

Client tries to reach `bucket.rustfs-s3...` and DNS fails.

The client is probably using virtual-hosted addressing. Configure path-style
addressing when the client supports it.

MLflow runs appear, but artifacts fail.

The MLflow metadata path is working. Check `mlflow-artifacts-s3`,
`MLFLOW_S3_ENDPOINT_URL`, the `mlflow-create-bucket` job, and MLflow server
logs.

Trino `tpch` works, but Iceberg writes fail.

The basic Trino engine is working. Check `trino-iceberg-bucket`,
`trino-iceberg-catalog-tables`, `trino-catalog-credentials`, RustFS S3 access
from the Trino namespace, and the PostgreSQL Iceberg metadata database.

RustFS pod is pending or restarting.

Inspect PVC binding, node placement, image pull status, chart-rendered
environment, and events. If the pod cannot mount storage, client-side changes
will not help.

After a chart upgrade, the Service has no endpoints.

Compare the Service selector with pod labels. The dedicated S3 Service is
created by this Pulumi program because the chart does not expose the needed
Tailscale annotations directly. If chart labels change, update the Pulumi-owned
Service rather than hand-editing the live Service.

## Safe Changes

Storage changes deserve a little more ceremony than stateless app changes
because the dangerous failures are often quiet. A preview can say that a
Service or Secret changed, but the real question is whether existing clients can
still read the objects they care about.

Before changing RustFS, find consumers:

```bash
rg -n "rustfs|s3_hostname|access_key|secret_key|MLFLOW_S3|icebergBucket|trino-iceberg" \
  pulumi docs \
  --glob '!**/uv.lock' \
  --glob '!pulumi/lib/**'
```

Classify the change:

```text
chart upgrade          check labels, ports, PVC behavior, chart values, and clients
endpoint change        preview RustFS plus every consumer that builds endpoint URLs
credential rotation    preview consumers and plan pod restarts or reconciliation
bucket rename          plan data migration and metadata updates
storage class change   plan object movement and restore verification
service selector       verify endpoints before and after
console ingress        verify console separately from S3 API
consumer bucket        change the consumer stack and run that consumer's workflow test
```

Use the repo gates for code changes:

```bash
just sync pulumi/data/storage/rustfs
just check-python
just lint
git diff --check
just preview pulumi/data/storage/rustfs stack=mx
```

If the output contract changes, preview the consumers too:

```bash
just preview pulumi/data/analytics/mlflow stack=mx
just preview pulumi/data/analytics/trino stack=mx
```

Do not run `pulumi up`, `pulumi destroy`, or `just up` from this workflow
unless the person requesting the work explicitly asks for an apply or a
destructive action.

After an approved apply, verify in layers:

```text
1. RustFS pod is ready.
2. rustfs-s3 Service has endpoints.
3. Direct S3 list/read/write/delete works on a throwaway object.
4. Expected buckets still exist.
5. MLflow can log and read an artifact through the UI.
6. Trino can create or read an Iceberg table.
7. Existing important data paths are still visible through their owning app.
```

Keep the distinction between runtime and state clear. Services, pods, ingresses,
and chart-rendered runtime objects are usually repairable. Buckets, object data,
PVC contents, MLflow metadata, and Iceberg metadata are state. Make the durable
change in Pulumi, preview the blast radius, and verify through the client that
actually owns the data.
