# Spark

Source: `pulumi/data/analytics/spark`

Spark is the distributed compute engine in this analytics area. Use it when the
work is bigger, more parallel, more stateful, or more awkward than a single
local Python process or one SQL query. The basic model is simple: a Spark client
describes a dataframe or SQL plan, Spark turns that plan into jobs and stages,
and a group of JVM processes runs the work across partitions.

This repo does not expose Spark as a public, general-purpose cluster. It creates
a small, private, Kubernetes-backed Spark Connect service with an Iceberg-capable
runtime. That service is good for interactive PySpark from a laptop, Marimo,
or a repo-owned workflow runner. It is also a good foundation for growing into
durable jobs, but the default sizing is intentionally modest.

The most important thing to keep in your head is where code runs:

- Your Python client can run on a laptop, in Marimo, in Airflow, in Dagster,
  or in a Kubernetes Job.
- The Spark Connect server runs in Kubernetes and behaves like the driver-side
  Spark session for client requests.
- Executors run in Kubernetes and do the distributed work.
- Paths such as `/var/lib/spark/warehouse` are paths inside Spark pods, not
  paths on your laptop. The active Iceberg warehouse is object storage, not that
  mounted path.
- The Spark UI is the truth source for what Spark actually executed.

If you remember only one operational rule: healthy pods are not enough. A Spark
URL or UI can fail while the driver pod is healthy if the Service has no
endpoints. Always check Services, endpoints, pod labels, and ingress together.

## What This Stack Builds

Pulumi installs and wires these pieces:

| Piece | What it does |
| --- | --- |
| Namespace | The Kubernetes namespace from the stack config key `namespace`. |
| Spark operator chart | Helm chart `spark-operator` version `2.5.0` from `https://kubeflow.github.io/spark-operator`. |
| Spark operator webhook | Enabled by chart values. |
| Operator metrics | Enabled with a chart-created `PodMonitor` labeled for the monitoring release. |
| Legacy warehouse PVC | A preserved `PersistentVolumeClaim` named `spark-warehouse`, default size `20Gi`, default storage class `local-path`. It is still mounted, but it is not the active Iceberg catalog warehouse. |
| Iceberg credentials Secret | A Spark namespace Secret named `spark-iceberg-credentials`, copied from the Trino catalog credentials and expanded with a Secret-backed `spark-defaults.conf`. |
| `SparkConnect` custom resource | The durable Spark Connect server declaration. |
| Spark Connect service | Operator-created service named by `connect_name`, listening on port `15002`. |
| Tailscale Connect service | Repo-owned `ClusterIP` service named `<connect_name>-endpoint`, annotated for Tailscale exposure on port `15002`. |
| Spark UI service | Repo-owned `ClusterIP` service named `<connect_name>-ui`, targeting port `4040`. |
| Spark UI ingress | Tailscale ingress named `spark-connect-ui`, using the configured UI hostname and port `4040`. |
| Grafana dashboard | ConfigMap-loaded dashboard `spark-overview.json`. |
| Stack outputs | Hostnames, image, chart version, Iceberg package versions, shared catalog name, RustFS endpoint, active warehouse URI, and legacy local warehouse URI. |

The current runtime constants in `__main__.py` are:

```text
Spark operator chart: 2.5.0
Spark version:        4.1.1
Java version:         21
Iceberg version:      1.10.1
Iceberg runtime jar:  iceberg-spark-runtime-4.0_2.13
Iceberg AWS bundle:   iceberg-aws-bundle
PostgreSQL JDBC jar:  postgresql-42.7.11
Catalog name:         trino_iceberg
Warehouse URI:        s3://trino-iceberg/warehouse
RustFS S3 endpoint:   http://rustfs-s3.rustfs.svc.cluster.local:9000
Legacy local path:    /var/lib/spark/warehouse
Default image:        ghcr.io/kzh/spark:4.1.1-iceberg1.10.1-lakehouse-java21
Connect port:         15002
UI port:              4040
```

The default Spark resources are intentionally small:

```text
Connect server cores:      1
Connect server memory:     1g
Executor instances:        1
Executor cores:            1
Executor memory:           1g
Executor dynamic scaling:  not enabled by this stack
```

That is enough to prove the wiring, run smoke tests, create small Iceberg
tables, and do light exploration. It is not a large compute pool. If a query is
slow on this default shape, check the Spark UI before assuming the query or
Spark itself is broken. The cluster may simply be doing real distributed work
with one small executor.

## The First-Principles Model

Spark separates the client API from the distributed execution engine.

In ordinary local PySpark, your Python process starts or attaches to a local JVM
driver, and that driver schedules work. In this repo, the common path is Spark
Connect. Spark Connect splits that relationship across the network:

1. Your Python process imports `pyspark` and builds a logical plan.
2. The Python client sends that plan to the Spark Connect server over port
   `15002`.
3. The Connect server in Kubernetes owns the Spark session, catalogs, SQL
   config, driver-side planning, and the Spark UI.
4. The server asks Kubernetes for executors.
5. Executors read data, run tasks, shuffle data, cache partitions, and write
   output.
6. Results come back to the client only when you run an action.

This explains several common surprises.

Creating a dataframe is usually lazy:

```python
df = spark.range(1_000_000).where("id % 2 = 0")
```

That line describes a plan. It does not immediately run one million rows of
work. Spark starts work when you call an action such as:

```python
df.count()
df.show()
df.collect()
df.writeTo("trino_iceberg.demo.table").append()
spark.sql("insert into trino_iceberg.demo.table select ...")
```

`collect()` is not a harmless inspection command. It pulls data back into your
client process. Use `limit`, `show`, `count`, `printSchema`, and the Spark UI
before collecting anything that might be large.

File paths are evaluated from the Spark pods' point of view. If you run this
from your laptop:

```python
spark.read.parquet("/Users/kevin/some-file.parquet")
```

the Spark server and executors will look for that path inside their containers,
not on the laptop. For small toy data, create a dataframe in Python and send it
through the client. For real data, put the data somewhere the Spark pods can
reach: the configured warehouse, an object store, a mounted volume, a database,
or another cluster service.

## Driver, Connect Server, And Executors

In this stack, the Spark Connect server is the long-lived entrypoint. The
container name in the pod template is `spark-kubernetes-driver`, and that is a
useful mental model: this pod coordinates the Spark application for Connect
sessions. It is not where all distributed work should happen.

The driver-side process is responsible for:

- accepting Spark Connect client requests;
- holding session state and Spark configuration;
- analyzing SQL and dataframe plans;
- creating jobs and stages;
- requesting executors;
- tracking task status;
- exposing the Spark UI on port `4040`;
- reporting errors back to the client.

Executors are responsible for:

- running tasks on partitions;
- reading and writing data;
- doing joins, aggregations, sorts, and UDF work;
- caching dataframe partitions when asked;
- holding shuffle data while a job runs.

The default stack creates one executor with one core and `1g` memory. That
keeps the baseline footprint small, but it also means many examples that look
"distributed" are only distributed in shape, not in capacity. To scale a real
workload, change the `executor` section of the `SparkConnect` spec, consider
dynamic allocation deliberately, and preview the Kubernetes changes before
applying them.

Driver memory and executor memory solve different problems. If the driver is
failing while planning, collecting too much data, or tracking very large job
metadata, increasing executors will not fix it. If executor tasks are spilling,
being killed, or crawling through shuffles, increasing driver memory will not
fix it. The UI and pod logs tell you which side is hurting.

## Names, Ports, And Services

Do not memorize private hostnames. Read the stack outputs when you need live
values:

```bash
cd pulumi/data/analytics/spark

pulumi stack output --stack mx namespace
pulumi stack output --stack mx chart_version
pulumi stack output --stack mx spark_image
pulumi stack output --stack mx spark_connect_name
pulumi stack output --stack mx spark_connect_hostname
pulumi stack output --stack mx spark_ui_hostname
pulumi stack output --stack mx iceberg_version
pulumi stack output --stack mx iceberg_catalog
pulumi stack output --stack mx iceberg_warehouse
pulumi stack output --stack mx iceberg_s3_endpoint
pulumi stack output --stack mx iceberg_credentials_secret
pulumi stack output --stack mx legacy_local_iceberg_warehouse
```

The important names are:

| Name | Default or shape | Meaning |
| --- | --- | --- |
| `connect_name` | `spark-connect` | The `SparkConnect` resource name and service-name family. |
| `connect_hostname` | defaults to `connect_name` | Tailscale hostname for the Connect endpoint. |
| `ui_hostname` | `spark` | Tailscale ingress hostname for the Spark UI. |
| Operator Connect service | `<connect_name>` | Service created from the `SparkConnect` server service spec. |
| Repo Connect endpoint service | `<connect_name>-endpoint` | Repo-owned service annotated with `tailscale.com/expose=true`. |
| Repo UI service | `<connect_name>-ui` | Repo-owned service used by the UI ingress. |
| UI ingress | `spark-connect-ui` | Tailscale ingress to the UI service. |
| Iceberg catalog | `trino_iceberg` | Spark's shared Iceberg catalog name. It intentionally matches Trino's JDBC catalog name. |
| Iceberg warehouse | `s3://trino-iceberg/warehouse` | RustFS-backed warehouse shared with Trino. |
| Legacy warehouse PVC | `spark-warehouse` | Preserved local warehouse storage from the older Spark-only setup. |

Ports are:

| Port | Protocol | Used for |
| --- | --- | --- |
| `15002` | TCP | Spark Connect clients. |
| `4040` | HTTP behind Tailscale ingress | Spark UI. |

The client URL is:

```text
sc://<spark_connect_hostname>:15002
```

The UI URL is:

```text
https://<spark_ui_hostname>
```

Inside Kubernetes, use service DNS rather than assuming the Tailscale hostname
is the right path. If your client pod is outside the Spark namespace, use a
fully qualified service name:

```text
sc://<spark_connect_name>.<namespace>.svc.cluster.local:15002
```

or:

```text
sc://<spark_connect_name>-endpoint.<namespace>.svc.cluster.local:15002
```

The operator-created service and the repo-owned endpoint service both target
the Connect server. The endpoint service exists because it is the one annotated
for Tailscale exposure and because repo-owned service selectors are easier to
reason about during upgrades.

## The Repo-Owned Selectors Matter

The two repo-owned Services select the Connect server with this label set:

```text
spark-role=connect-server
spark-version=4.1.1
sparkoperator.k8s.io/connect-name=<connect_name>
sparkoperator.k8s.io/launched-by-spark-operator=true
```

That selector is operationally important. The Spark UI path has previously
failed because a service selector pointed at an old Spark version label. The
pods were healthy, but the backend service had no endpoints, so ingress had
nowhere to send traffic.

When Spark version or operator behavior changes, verify that the live pod labels
still match the repo-owned services:

```bash
cd pulumi/data/analytics/spark

NS="$(pulumi stack output --stack mx namespace)"
CONNECT="$(pulumi stack output --stack mx spark_connect_name)"

kubectl get pods -n "$NS" \
  -l "sparkoperator.k8s.io/connect-name=$CONNECT" \
  --show-labels

kubectl get endpoints -n "$NS" "$CONNECT-endpoint" "$CONNECT-ui"
kubectl describe svc -n "$NS" "$CONNECT-endpoint"
kubectl describe svc -n "$NS" "$CONNECT-ui"
```

If either endpoint list is empty, traffic cannot reach the pod through that
service. Fix the selector or the labels before debugging browser behavior,
client libraries, or TLS.

## Connecting From A Laptop

Use a PySpark client that matches the server's Spark line and includes the
Spark Connect client dependencies. This stack currently sets
`SPARK_VERSION = "4.1.1"`, so a practical one-off local smoke test is:

```bash
cd pulumi/data/analytics/spark

export SPARK_CONNECT_HOST="$(pulumi stack output --stack mx spark_connect_hostname)"

uv run \
  --with pyspark==4.1.1 \
  --with grpcio \
  --with grpcio-status \
  --with pandas \
  --with pyarrow \
  --with zstandard \
  python - <<'PY'
import os

from pyspark.sql import SparkSession

host = os.environ["SPARK_CONNECT_HOST"]

spark = (
    SparkSession.builder
    .remote(f"sc://{host}:15002")
    .appName("docs-laptop-smoke")
    .getOrCreate()
)

print("Spark version:", spark.version)
spark.sql("select 1 as ok").show()
spark.range(5).withColumnRenamed("id", "number").show()

spark.stop()
PY
```

That small test proves several things at once:

- your local client can resolve and reach the Connect hostname;
- the Spark Connect server accepts sessions;
- SQL analysis works;
- at least one executor can run tasks;
- results can return to the client.

If that fails before anything appears in the Spark UI, suspect client version,
DNS, Tailscale reachability, service endpoints, or the Connect server logs. If
the UI shows a job and the job fails, suspect Spark runtime, executor resources,
Iceberg/catalog config, data paths, or application code.

Keep local scripts explicit about session lifetime:

```python
from pyspark.sql import SparkSession

spark = SparkSession.builder.remote("sc://...:15002").getOrCreate()
try:
    # real work here
    ...
finally:
    spark.stop()
```

Stopping the session matters during iterative work. It releases session state
and makes debugging less confusing.

## Connecting From Marimo Or Another Pod

When the client runs inside Kubernetes, the best endpoint is usually Kubernetes
service DNS. Read the namespace and connect name from outputs:

```bash
cd pulumi/data/analytics/spark

NS="$(pulumi stack output --stack mx namespace)"
CONNECT="$(pulumi stack output --stack mx spark_connect_name)"
printf 'sc://%s.%s.svc.cluster.local:15002\n' "$CONNECT" "$NS"
```

In a notebook, that becomes:

```python
from pyspark.sql import SparkSession

spark = (
    SparkSession.builder
    .remote("sc://spark-connect.spark.svc.cluster.local:15002")
    .appName("notebook-smoke")
    .getOrCreate()
)

spark.sql("select 1 as ok").show()
```

Replace `spark` in the service DNS name with the actual namespace output if the
stack uses a different namespace.

From a pod in the same namespace as Spark, the short name may work:

```text
sc://spark-connect:15002
```

From another namespace, prefer the fully qualified name. That avoids debugging a
DNS search-path issue as if it were a Spark issue.

## A First PySpark Session

A good first session checks the runtime before touching real data:

```python
from pyspark.sql import SparkSession, functions as F

spark = SparkSession.builder.remote("sc://spark-connect:15002").getOrCreate()

print("Spark version:", spark.version)
spark.sql("show catalogs").show(truncate=False)
spark.sql("show namespaces in trino_iceberg").show(truncate=False)

df = spark.range(0, 20).withColumn("bucket", F.col("id") % 4)
df.groupBy("bucket").count().orderBy("bucket").show()
df.explain()
```

The calls have different meanings:

- `show catalogs` proves the session can see configured catalogs.
- `show namespaces in trino_iceberg` proves the shared Iceberg catalog is registered.
- `range(...).groupBy(...).count()` proves executor-side work runs.
- `explain()` prints the plan so you can see what Spark intends to do.

Use `spark.conf.set` for session-level tuning while exploring:

```python
spark.conf.set("spark.sql.shuffle.partitions", "8")
```

The Spark default shuffle partition count is often too high for a tiny
one-executor development deployment. Lowering it for small experiments reduces
empty tasks and makes the UI easier to read. Do not treat that as a universal
production setting; tune it for the workload.

## Reading Data Without Fooling Yourself

Spark dataframe code can look like ordinary Python, but the expensive parts run
later and elsewhere. Start every unfamiliar dataset with shape discovery:

```python
df = spark.table("trino_iceberg.demo.some_table")

df.printSchema()
df.limit(10).show(truncate=False)
print("rows:", df.count())
```

Then make the plan narrower:

```python
small = (
    df.select("event_time", "kind", "user_id")
    .where("event_time >= timestamp'2026-01-01 00:00:00'")
)

small.groupBy("kind").count().orderBy("count", ascending=False).show(20)
```

General habits that keep Spark pleasant:

- Select only the columns you need.
- Filter early when the filter is meaningful.
- Use `limit` for inspection, not `collect`.
- Call `explain()` when a query surprises you.
- Watch the SQL and Stages tabs in the UI for shuffles and skew.
- Cache only when you reuse the same dataframe, and unpersist when finished.
- Write small smoke outputs before overwriting important tables.

Caching is not magic. It uses executor memory and can make a small cluster
worse if you cache more than it can hold:

```python
cached = small.cache()
cached.count()

# Use cached repeatedly...

cached.unpersist()
```

## Iceberg In This Stack

Iceberg is a table format. It gives a lake-style table a real metadata layer:
schemas, snapshots, manifests, partition specs, and table history. Instead of
treating a directory of Parquet files as a pile of files, Spark can treat it as
a table with transactional metadata.

This stack configures Spark to use the same RustFS-backed Iceberg storage plane
as Trino. The active Spark catalog is named:

```text
trino_iceberg
```

That name is deliberate. Apache Iceberg's Spark JDBC catalog uses the Spark
catalog name as the JDBC `catalog_name`. Trino's Iceberg connector uses
`trino_iceberg` as its internal JDBC catalog name. Matching the names is what
makes Spark and Trino see the same Iceberg metadata rows, not merely the same
bucket.

The active catalog shape is:

```text
Spark SQL catalog:        trino_iceberg
Spark catalog type:       jdbc
JDBC metadata database:   trino_iceberg in the shared PostgreSQL stack
JDBC catalog name:        trino_iceberg
Warehouse:                s3://trino-iceberg/warehouse
S3 endpoint:              RustFS service on port 9000
FileIO implementation:    org.apache.iceberg.aws.s3.S3FileIO
Credentials source:       spark-iceberg-credentials Secret
Defaults mount:           /opt/spark/conf/spark-defaults.conf
```

The important configs are written into a Secret-backed `spark-defaults.conf`
mounted into the Spark Connect server pod. Keeping them in that file avoids
putting the JDBC password directly in the `SparkConnect` command arguments.
The pod still receives environment variables for the JDBC and S3 credentials so
the runtime and future job code can use the same Secret keys without copying
values into docs or stack config.

A table name looks like:

```text
trino_iceberg.<namespace>.<table>
```

For example:

```text
trino_iceberg.demo.numbers
```

Trino addresses the same tables through its user-facing catalog named
`iceberg`. That means the same table is:

```text
Spark: trino_iceberg.demo.numbers
Trino: iceberg.demo.numbers
```

Do not manually edit the RustFS warehouse objects or the PostgreSQL Iceberg
metadata tables. Iceberg tracks snapshots and metadata references. A file or row
that looks removable by hand can still be part of a table's history.

## Create And Inspect An Iceberg Table

Use SQL first. It is direct and maps cleanly onto Iceberg concepts. This example
creates the table through Spark and leaves it in the shared catalog, so Trino can
read it too:

```bash
cd pulumi/data/analytics/spark

export SPARK_CONNECT_HOST="$(pulumi stack output --stack mx spark_connect_hostname)"
export ICEBERG_CATALOG="$(pulumi stack output --stack mx iceberg_catalog)"

uv run \
  --with pyspark==4.1.1 \
  --with grpcio \
  --with grpcio-status \
  --with pandas \
  --with pyarrow \
  --with zstandard \
  python - <<'PY'
import os

from pyspark.sql import SparkSession

catalog = os.environ["ICEBERG_CATALOG"]

spark = (
    SparkSession.builder
    .remote(f"sc://{os.environ['SPARK_CONNECT_HOST']}:15002")
    .appName("docs-iceberg-smoke")
    .getOrCreate()
)

spark.sql(f"create namespace if not exists {catalog}.demo")

spark.sql(f"""
    create table if not exists {catalog}.demo.numbers (
        id bigint,
        label string
    ) using iceberg
""")

spark.sql(f"""
    insert into {catalog}.demo.numbers values
        (1, 'one'),
        (2, 'two'),
        (3, 'three')
""")

spark.sql(f"select * from {catalog}.demo.numbers order by id").show()
spark.sql(f"select * from {catalog}.demo.numbers.snapshots").show(truncate=False)

spark.stop()
PY
```

The `snapshots` query reads an Iceberg metadata table. It proves you are not
just writing anonymous Parquet files; Iceberg is tracking table metadata.

To prove the shared contract from Trino, read the same table through Trino's
`iceberg` catalog:

```bash
kubectl exec -n trino deploy/trino-coordinator -- \
  trino --server http://localhost:8080 \
  --execute "select * from iceberg.demo.numbers order by id"
```

You can also write through the dataframe API:

```python
from pyspark.sql import Row

rows = [
    Row(id=4, label="four"),
    Row(id=5, label="five"),
]

df = spark.createDataFrame(rows)
df.writeTo("trino_iceberg.demo.numbers").append()
```

For replacement-style development data, be explicit:

```python
spark.sql("""
    create or replace table trino_iceberg.demo.daily_counts
    using iceberg
    as
    select label, count(*) as n
    from trino_iceberg.demo.numbers
    group by label
""")
```

Do not use replacement operations on important tables casually. Iceberg makes
table operations much safer than raw file writes, but table changes are still
data changes.

## Warehouse State And Local Storage

The active warehouse is shared lakehouse storage, not the local PVC:

```text
Catalog:       trino_iceberg
Warehouse:     s3://trino-iceberg/warehouse
Object store:  RustFS
Metadata DB:   PostgreSQL database trino_iceberg
Credential:    spark-iceberg-credentials
```

The old local PVC still exists and remains mounted:

```text
PVC name:       spark-warehouse
Access mode:    ReadWriteOnce
Storage class:  local-path
Size:           20Gi
Mount path:     /var/lib/spark/warehouse
URI:            file:///var/lib/spark/warehouse
```

That PVC is preserved for safety because it may contain tables or files from
the older Spark-only setup. It is exported as `legacy_local_iceberg_warehouse`.
Do not delete it casually during cleanup. It is not the active catalog warehouse
for new shared Spark/Trino Iceberg tables.

When debugging shared Iceberg issues, check all three backing systems: Spark's
catalog config, PostgreSQL metadata, and RustFS object storage. A healthy Spark
pod does not prove the JDBC metadata database or RustFS endpoint is usable.

Useful non-secret checks:

```bash
cd pulumi/data/analytics/spark

pulumi stack output --stack mx iceberg_catalog
pulumi stack output --stack mx iceberg_warehouse
pulumi stack output --stack mx iceberg_s3_endpoint
pulumi stack output --stack mx iceberg_credentials_secret
```

To inspect the preserved PVC and mount:

```bash
cd pulumi/data/analytics/spark

NS="$(pulumi stack output --stack mx namespace)"
CONNECT="$(pulumi stack output --stack mx spark_connect_name)"

kubectl get pvc -n "$NS" spark-warehouse
kubectl describe pvc -n "$NS" spark-warehouse

POD="$(
  kubectl get pod -n "$NS" \
    -l "sparkoperator.k8s.io/connect-name=$CONNECT,spark-role=connect-server" \
    -o jsonpath='{.items[0].metadata.name}'
)"

kubectl exec -n "$NS" "$POD" -- sh -lc 'id; ls -ld /var/lib/spark/warehouse'
```

The pod security context runs as user and group `185`, with privilege escalation
disabled, all Linux capabilities dropped, and `RuntimeDefault` seccomp. If
legacy local path writes fail with permission errors, check the mounted volume
ownership and the runtime user before changing Spark code. If shared Iceberg
writes fail, start with the JDBC/S3 catalog path instead.

## Spark UI

Open the UI from the stack output:

```bash
cd pulumi/data/analytics/spark
pulumi stack output --stack mx spark_ui_hostname
```

Then browse to:

```text
https://<spark_ui_hostname>
```

The UI is served by the Connect server on port `4040`, through the repo-owned
`<connect_name>-ui` service and the Tailscale ingress. It is private to the same
network path as the rest of these homelab services.

Use the UI while the query is running, not only after something fails. The useful
tabs are:

| UI area | What to look for |
| --- | --- |
| Jobs | Which actions ran, whether they succeeded, and how long they took. |
| Stages | Task counts, failed tasks, skew, shuffle read/write, and spill. |
| SQL/DataFrame | Logical and physical query plans for SQL and dataframe actions. |
| Executors | Executor count, memory use, task failures, and whether executors are alive. |
| Storage | Cached dataframes and memory pressure from caching. |
| Environment | Spark configuration, classpath clues, and runtime details. |

If a local client shows a short exception, the UI often has the fuller story.
For example, a Python stack trace may only say that a SQL query failed, while
the UI shows the stage that failed and the executor behavior around it.

If the UI URL returns a backend error, do not start with browser debugging.
Check the UI service endpoints:

```bash
cd pulumi/data/analytics/spark

NS="$(pulumi stack output --stack mx namespace)"
CONNECT="$(pulumi stack output --stack mx spark_connect_name)"

kubectl get ingress -n "$NS" spark-connect-ui
kubectl describe ingress -n "$NS" spark-connect-ui
kubectl get svc -n "$NS" "$CONNECT-ui"
kubectl get endpoints -n "$NS" "$CONNECT-ui"
kubectl get pods -n "$NS" \
  -l "sparkoperator.k8s.io/connect-name=$CONNECT" \
  --show-labels
```

Empty endpoints mean the service selector does not match a ready pod. That is a
Kubernetes routing problem, even if the Spark pod itself is running.

## Common PySpark Patterns

Small in-memory data is fine for examples:

```python
from pyspark.sql import Row

people = spark.createDataFrame(
    [
        Row(name="Ada", team="math", score=10),
        Row(name="Grace", team="systems", score=12),
        Row(name="Katherine", team="math", score=15),
    ]
)

people.groupBy("team").avg("score").show()
```

SQL and dataframe APIs can work together:

```python
people.createOrReplaceTempView("people")

spark.sql("""
    select team, count(*) as n, avg(score) as avg_score
    from people
    group by team
    order by team
""").show()
```

For writes, prefer table APIs when writing Iceberg tables:

```python
people.writeTo("trino_iceberg.demo.people").createOrReplace()
```

For appends:

```python
more_people.writeTo("trino_iceberg.demo.people").append()
```

For partitioned tables, choose partitions based on query patterns, not on
habit:

```python
spark.sql("""
    create table if not exists trino_iceberg.demo.events (
        event_date date,
        kind string,
        payload string
    )
    using iceberg
    partitioned by (event_date)
""")
```

A partition column with very high cardinality can create too many small files. A
partition column that is never used in filters may not help. Use the UI and
Iceberg metadata tables to understand what your writes are doing.

To inspect file output for an Iceberg table:

```python
spark.sql("select * from trino_iceberg.demo.people.files").show(truncate=False)
```

If a write creates far more files than expected, look at which partitions Spark
actually used:

```python
from pyspark.sql import functions as F

(
    df.select(F.spark_partition_id().alias("partition_id"))
    .groupBy("partition_id")
    .count()
    .orderBy("partition_id")
    .show()
)
```

Then adjust deliberately:

```python
df.coalesce(4).writeTo("trino_iceberg.demo.some_table").append()
```

`coalesce` reduces partitions without a full shuffle. `repartition` creates a
shuffle and can increase or rebalance partitions. Neither is always better; use
the one that matches the data shape.

## UDFs And Python Packages

PySpark code can fail because the client has a package but the executors do not.
That difference matters in Spark Connect.

If a package is used only by the client script to prepare small parameters, it
can live in the client environment. If a package is imported inside a UDF or
inside code that runs on executors, it must be available in the Spark runtime
environment.

For quick local experiments:

```bash
uv run \
  --with pyspark==4.1.1 \
  --with grpcio \
  --with grpcio-status \
  --with pandas \
  --with pyarrow \
  --with zstandard \
  python my_experiment.py
```

For repeatable executor-side dependencies, update the Spark image or the job
packaging strategy. Repeated notebook-local installs are not a durable way to
operate Spark jobs.

Be especially cautious with:

- Python UDF dependencies;
- PyArrow and pandas versions;
- JVM connector jars;
- Hadoop filesystem connectors;
- Iceberg runtime jar versions;
- libraries that need native system packages.

When a dependency affects executor code, the validation is not "the import works
on my laptop." The validation is a Spark action that runs on executors.

## Jobs: From Exploration To Something Durable

Spark Connect is excellent for interactive work. It is also a clean way for a
workflow system to ask Spark to do distributed work. The important line is
durability: a job that matters should not exist only as a notebook cell or a
terminal history entry.

There are three practical lanes.

The first lane is an interactive client. This is best for exploration,
debugging, and one-off analysis:

```text
laptop or notebook -> Spark Connect -> driver/executors
```

Use it to learn the data and prove the plan. Keep writes small until you trust
the job.

The second lane is a repo-owned workflow task. Airflow, Dagster, or a Kubernetes
Job can run a Python script that connects to Spark Connect:

```text
workflow task -> Spark Connect -> driver/executors
```

That gives you scheduling, logs, retries, code review, and a clearer owner. The
Spark code still uses the same Connect endpoint, but the client process is now
part of an operational workflow instead of a human's shell.

A script shaped for this lane looks like:

```python
import os
from datetime import date

from pyspark.sql import SparkSession, functions as F


def main() -> None:
    connect_url = os.environ["SPARK_CONNECT_URL"]
    run_date = os.environ.get("RUN_DATE", date.today().isoformat())

    spark = (
        SparkSession.builder
        .remote(connect_url)
        .appName(f"daily-docs-example-{run_date}")
        .getOrCreate()
    )

    try:
        spark.conf.set("spark.sql.shuffle.partitions", "8")

        source = spark.table("trino_iceberg.demo.people")
        result = (
            source
            .groupBy("team")
            .agg(F.count("*").alias("people"), F.avg("score").alias("avg_score"))
            .withColumn("run_date", F.lit(run_date))
        )

        result.writeTo("trino_iceberg.demo.team_score_daily").append()
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
```

The third lane is an operator-native Spark application. The Spark operator can
manage application-style Spark resources, but this repo's generated Spark CRD
package is currently filtered to `sparkconnects.sparkoperator.k8s.io`, and this
stack currently declares `SparkConnect`, not `SparkApplication`. If you want
repo-managed `SparkApplication` resources, treat that as an infrastructure
change:

- decide where the job definition belongs;
- update the CRD generation filter if the generated binding should include
  `SparkApplication`;
- regenerate the Spark CRD bindings with the repo recipe;
- preserve stable Pulumi names and Kubernetes names;
- preview the stack before applying;
- add a smoke test that proves the application actually runs.

For many homelab workflows, the second lane is enough: keep Spark Connect as the
cluster compute endpoint and let Airflow or Dagster own the schedule.

## Designing A Spark Job

Before making a Spark job durable, answer these questions in the job's owning
code or docs:

- What is the input table or data location?
- What is the output table or data location?
- Is the write append-only, overwrite, merge, or create-or-replace?
- Is the job idempotent for a given date or partition?
- How many executors and how much memory does it need?
- What happens if the job is retried after a partial failure?
- Which catalog owns the output?
- Which UI, logs, or metrics will someone inspect if it fails?

Idempotency matters more than clever Spark code. A scheduled job that appends
duplicate rows every time it retries is operationally worse than a slower job
with a clear overwrite-by-partition strategy.

For tiny local Iceberg experiments, `create or replace table` is acceptable.
For repeatable jobs, prefer an output design that can be safely rerun:

```sql
delete from trino_iceberg.demo.daily_output where run_date = date'2026-05-18';
insert into trino_iceberg.demo.daily_output
select ...
```

or write to a run-specific staging table and promote after validation. The exact
pattern depends on the table, but the principle is stable: retries should not
silently corrupt the output.

## Debugging: Start With The Live Objects

The fastest useful cluster snapshot is:

```bash
cd pulumi/data/analytics/spark

NS="$(pulumi stack output --stack mx namespace)"
CONNECT="$(pulumi stack output --stack mx spark_connect_name)"

kubectl get sparkconnects -n "$NS"
kubectl describe sparkconnect -n "$NS" "$CONNECT"

kubectl get pods -n "$NS" \
  -l "sparkoperator.k8s.io/connect-name=$CONNECT" \
  -o wide

kubectl get svc -n "$NS" "$CONNECT" "$CONNECT-endpoint" "$CONNECT-ui"
kubectl get endpoints -n "$NS" "$CONNECT" "$CONNECT-endpoint" "$CONNECT-ui"
kubectl get ingress -n "$NS" spark-connect-ui
kubectl get pvc -n "$NS" spark-warehouse
```

Then read logs:

```bash
kubectl logs -n "$NS" \
  -l "sparkoperator.k8s.io/connect-name=$CONNECT,spark-role=connect-server" \
  --tail=200
```

If there are executor pods, inspect them too:

```bash
kubectl get pods -n "$NS" \
  -l "sparkoperator.k8s.io/connect-name=$CONNECT" \
  --show-labels
```

The Spark operator itself is installed in the Spark namespace by this stack, so
operator logs are also in scope:

```bash
kubectl logs -n "$NS" deploy/spark-operator-controller --tail=200
kubectl logs -n "$NS" deploy/spark-operator-webhook --tail=200
```

If those deployment names change in a chart upgrade, list deployments first:

```bash
kubectl get deploy -n "$NS"
```

Do not paste full logs into docs or commits. Summarize the failure class and
keep secret-bearing output out of the repository.

## Debugging By Symptom

If the client cannot connect at all, check:

- Does `spark_connect_hostname` resolve from the client environment?
- Is the client on the private network path that can reach the Tailscale
  service?
- Does `<connect_name>-endpoint` have endpoints?
- Is port `15002` the port being used?
- Is the PySpark client compatible with the server Spark version?

If the client connects but `select 1` fails, check:

- Connect server pod logs;
- executor pod creation and scheduling;
- Spark operator logs;
- service account or RBAC errors;
- image pull errors;
- executor resource pressure.

If SQL works but Iceberg fails, check:

- the image contains the Iceberg runtime jar, Iceberg AWS bundle, and
  PostgreSQL JDBC jar;
- the Iceberg package versions match the constants in `__main__.py`;
- the `spark-iceberg-credentials` Secret exists in the Spark namespace;
- the Connect server mounts `spark-defaults.conf` from that Secret;
- the `trino_iceberg` catalog config is present in Spark's runtime environment;
- the PostgreSQL Iceberg metadata database is reachable;
- the RustFS S3 endpoint is reachable;
- S3 path-style access and region settings match RustFS behavior.

If the UI URL fails, check:

- ingress `spark-connect-ui`;
- service `<connect_name>-ui`;
- endpoints for `<connect_name>-ui`;
- pod labels against the repo-owned selector;
- Tailscale ingress status.

If a query is slow, check:

- the SQL/DataFrame plan;
- shuffle read and write in the UI;
- number of tasks relative to executor cores;
- executor memory and spill;
- data skew;
- whether the client accidentally called `collect`;
- whether `spark.sql.shuffle.partitions` is too high for the current size.

If a write creates too many files, check:

- dataframe partition count before the write;
- partition spec for the Iceberg table;
- task count in the UI;
- whether you should `coalesce` before writing;
- whether the table design needs a different partition strategy.

If a package import works locally but fails during a Spark action, assume the
executors do not have that package. Put executor-side dependencies in the Spark
runtime image or in a deliberate job packaging mechanism.

## Image And Runtime Packages

The custom image is under:

```text
pulumi/data/analytics/spark/images/spark-iceberg/Dockerfile
```

It is a two-stage build:

1. An Alpine stage downloads the Iceberg Spark runtime jar, Iceberg AWS bundle,
   and PostgreSQL JDBC jar from Maven Central.
2. The final image starts from `docker.io/library/spark:<spark>-java<java>-python3`
   and copies those jars into `/opt/spark/jars`.

The final image runs as user `185`, matching the pod security context.

The project-local `Justfile` builds and pushes the image:

```bash
cd pulumi/data/analytics/spark

just build-image
just push-image
just inspect-image
```

Those recipes currently build `linux/amd64` and tag:

```text
ghcr.io/kzh/spark:4.1.1-iceberg1.10.1-lakehouse-java21
```

When changing the image, keep these files in sync:

| File | Why it matters |
| --- | --- |
| `__main__.py` | Defines Spark, Java, Iceberg constants and the default image tag used by Pulumi. |
| `images/spark-iceberg/Dockerfile` | Defines the actual runtime content. |
| `Justfile` | Defines image build args and tag. |
| `pyproject.toml` and `uv.lock` | Define Pulumi project dependencies, not the Spark runtime packages. |

Do not assume changing `pyproject.toml` adds a package to executors. The Pulumi
project environment is only for deploying infrastructure. Spark runtime packages
belong in the Spark image or the job packaging path.

Be careful with `imagePullPolicy: IfNotPresent`. Reusing a tag after pushing a
different image can leave nodes running the old image. Prefer a new, meaningful
tag when the runtime content changes. If you intentionally reuse a tag during
debugging, verify the live pod image ID, not just the image tag:

```bash
kubectl get pod -n "$NS" "$POD" \
  -o jsonpath='{.status.containerStatuses[*].imageID}{"\n"}'
```

## Version Coupling

Spark, Java, Scala binary version, Iceberg runtime jar, Python client, and the
container image are one compatibility set.

This stack currently uses:

```text
Spark server:          4.1.1
PySpark client:        should match 4.1.1 for ordinary use
Java runtime:          21
Iceberg runtime jar:   iceberg-spark-runtime-4.0_2.13:1.10.1
Iceberg AWS bundle:    iceberg-aws-bundle:1.10.1
PostgreSQL JDBC jar:   org.postgresql:postgresql:42.7.11
Spark operator chart:  2.5.0
```

Changing one member of that set can break another:

- A Spark server bump may require a different PySpark client.
- A Spark server bump may require a different Iceberg runtime artifact.
- A Java bump may require a different base image.
- An Iceberg bump may change SQL behavior, metadata behavior, or required jars.
- A JDBC driver bump can change catalog connectivity behavior.
- Missing S3/AWS support jars can make the catalog register but fail on reads
  or writes.
- A Spark operator chart bump may change CRD schema, pod labels, service
  behavior, or reconciliation behavior.
- A tag-only image rebuild may not roll out if Kubernetes thinks the image is
  already present.

Treat runtime upgrades as migrations, not string edits.

## CRDs And The Generated Binding

The stack imports:

```python
from pulumi_spark_operator_crds.sparkoperator.v1alpha1 import SparkConnect
```

That package is generated under:

```text
pulumi/lib/spark_operator_crds
```

The source CRD YAML lives beside the owning stack:

```text
pulumi/data/analytics/spark/crds/spark-operator-2.5.0.crds.yaml
```

The root `Justfile` currently generates this package from the Spark operator
chart and filters the CRDs to:

```text
sparkconnects.sparkoperator.k8s.io
```

That means the generated package is intentionally focused on `SparkConnect`.
Do not hand-edit generated files under `pulumi/lib/spark_operator_crds`. If a
chart upgrade changes the CRD, use the generator recipe:

```bash
just generate-spark-crds
```

For a new chart version:

```bash
just generate-spark-crds version="<new-chart-version>"
```

After CRD generation or any CRD-backed stack change, run the repository gates
that apply to code changes:

```bash
just check-python
just lint
git diff --check
just preview pulumi/data/analytics/spark stack=mx
```

Do not apply unless the task explicitly calls for it.

## Changing Stack Config

The useful config keys read by `__main__.py` are:

| Config key | Default | Meaning |
| --- | --- | --- |
| `namespace` | required | Kubernetes namespace for this stack. |
| `connect_name` | `spark-connect` | `SparkConnect` name and service name family. |
| `connect_hostname` | `connect_name` | Tailscale hostname for Spark Connect. |
| `ui_hostname` | `spark` | Tailscale ingress hostname for the UI. |
| `image` | generated default tag | Spark runtime image for server and executors. |
| `warehouse_storage_size` | `20Gi` | Requested size for the preserved legacy warehouse PVC. |
| `warehouse_storage_class` | `local-path` | StorageClass for the preserved legacy warehouse PVC. |
| `postgresStack` | `kzh/postgresql/mx` | StackReference for the PostgreSQL service used by the Iceberg JDBC catalog. |
| `rustfsStack` | `kzh/rustfs/mx` | StackReference for the RustFS S3 endpoint and credentials. |
| `trinoStack` | `kzh/trino/mx` | StackReference for the shared Iceberg database, warehouse, and JDBC catalog name. |
| `trinoNamespace` | `trino` | Namespace containing Trino's catalog credentials Secret. |
| `trinoCredentialsSecretName` | `trino-catalog-credentials` | Source Secret for Iceberg JDBC and RustFS credentials copied into Spark. |
| `icebergCatalogName` | `trino_iceberg` | Spark catalog name. Keep this aligned with Trino's Iceberg JDBC catalog name unless migrating metadata deliberately. |
| `monitoringReleaseLabel` | `kube-prometheus-stack` | Label used by the chart-created PodMonitor. |

Treat these as API boundaries:

- Changing `connect_name` changes Kubernetes names clients may use.
- Changing `connect_hostname` changes laptop/client connection strings.
- Changing `ui_hostname` changes the UI URL.
- Changing `image` changes the Spark runtime.
- Changing warehouse storage config affects the preserved legacy local PVC, not
  the active RustFS-backed Iceberg warehouse.
- Changing `icebergCatalogName`, the Trino stack reference, the PostgreSQL
  stack reference, or the RustFS stack reference is a lakehouse metadata/storage
  migration unless you are deliberately creating a separate catalog.
- Changing monitoring labels can disconnect metrics from Grafana/Prometheus.

For config audits, include ESC `environment:` imports and stack config, not only
the Python defaults. Do not paste secret outputs or private config into docs.

## Safe Upgrade Workflow

A safe Spark upgrade starts with classification. Decide which kind of change you
are making:

| Change | Main risk |
| --- | --- |
| Spark image package change | Executors or server no longer have the expected Python/JVM libraries. |
| Spark version change | Client compatibility, pod labels, Iceberg runtime compatibility, query behavior. |
| Iceberg version change | Catalog/table behavior and jar compatibility. |
| Spark operator chart change | CRD schema, controller behavior, labels, webhooks, services. |
| Shared Iceberg catalog change | Spark and Trino may stop seeing the same tables. |
| Legacy warehouse PVC change | Old local data or PVC replacement risk. |
| Hostname/service change | Client and ingress breakage. |
| Resource sizing change | Scheduling failure or unexpected cost. |

For a routine docs-free code change, use the repo workflow:

```bash
git status --short
just sync pulumi/data/analytics/spark
just check-python
just lint
git diff --check
just preview pulumi/data/analytics/spark stack=mx
```

For chart or CRD changes, add:

```bash
just generate-spark-crds version="<chart-version>"
just check-crds
```

For image changes, add:

```bash
cd pulumi/data/analytics/spark
just build-image
just inspect-image
```

Push only when you intend the cluster to consume the image:

```bash
just push-image
```

After an intentional apply, validate behavior, not only reconciliation:

```bash
cd pulumi/data/analytics/spark

NS="$(pulumi stack output --stack mx namespace)"
CONNECT="$(pulumi stack output --stack mx spark_connect_name)"

kubectl get sparkconnects -n "$NS"
kubectl get pods -n "$NS" -l "sparkoperator.k8s.io/connect-name=$CONNECT"
kubectl get endpoints -n "$NS" "$CONNECT-endpoint" "$CONNECT-ui"
```

Then run a PySpark smoke:

```python
spark.sql("select 1 as ok").show()
spark.sql("show catalogs").show()
spark.sql("create namespace if not exists trino_iceberg.upgrade_smoke").show()
```

For catalog changes, also prove Trino sees a Spark-created Iceberg table:

```text
Spark writes: trino_iceberg.<schema>.<table>
Trino reads:  iceberg.<schema>.<table>
```

Then open the UI while the query runs. A green Pulumi update is not the same as
a usable Spark service.

## When To Scale

Scale when the workload asks for it, not because "Spark means many executors."

Signs that scaling may help:

- tasks are queued because there are not enough executor cores;
- stages are CPU-bound and parallelizable;
- executor memory pressure causes spilling or failures;
- the UI shows long-running stages with many independent tasks;
- a job is important enough to justify predictable runtime.

Signs that scaling may not help:

- the driver is failing during planning;
- the client is collecting too much data;
- one skewed partition dominates a stage;
- the input source is the bottleneck;
- the query plan is doing unnecessary shuffles;
- writes are bottlenecked on object storage, small files, or catalog metadata.

For the current stack, resource changes live in the `SparkConnect` spec:

```python
"server": {
    "cores": 1,
    "memory": "1g",
    ...
},
"executor": {
    "instances": 1,
    "cores": 1,
    "memory": "1g",
    ...
},
```

If you add dynamic allocation, read the CRD schema and operator behavior first.
Dynamic allocation is useful, but it changes the executor lifecycle and can make
debugging less obvious. Preview the change and verify executor behavior in the
UI after apply.

## Observability

The stack enables Spark operator Prometheus metrics and creates a
`spark-overview` Grafana dashboard ConfigMap. The dashboard is for operator and
cluster-level signals. The Spark UI is still the best view of a specific query.

Use both:

- Grafana answers "is the Spark operator/service generally healthy?"
- Spark UI answers "what happened inside this Spark job?"
- Kubernetes logs answer "what did the pod or operator report?"
- Pulumi preview answers "what infrastructure change would this code make?"

If metrics disappear after a change, check the chart's PodMonitor labels and the
`monitoringReleaseLabel` config. A Spark service can still work while
observability is disconnected.

## A Practical End-To-End Smoke

This is a compact smoke test that proves Connect, SQL, executors, Iceberg, and
the warehouse:

```bash
cd pulumi/data/analytics/spark

export SPARK_CONNECT_HOST="$(pulumi stack output --stack mx spark_connect_hostname)"
export ICEBERG_CATALOG="$(pulumi stack output --stack mx iceberg_catalog)"

uv run \
  --with pyspark==4.1.1 \
  --with grpcio \
  --with grpcio-status \
  --with pandas \
  --with pyarrow \
  --with zstandard \
  python - <<'PY'
import os
import time

from pyspark.sql import SparkSession, functions as F

catalog = os.environ["ICEBERG_CATALOG"]

spark = (
    SparkSession.builder
    .remote(f"sc://{os.environ['SPARK_CONNECT_HOST']}:15002")
    .appName("spark-stack-end-to-end-smoke")
    .getOrCreate()
)

try:
    spark.conf.set("spark.sql.shuffle.partitions", "4")

    spark.sql("select 1 as ok").show()
    spark.sql(f"create namespace if not exists {catalog}.docs_smoke")

    data = (
        spark.range(0, 100)
        .withColumn("bucket", F.col("id") % 10)
        .groupBy("bucket")
        .count()
        .orderBy("bucket")
    )

    data.writeTo(f"{catalog}.docs_smoke.bucket_counts").createOrReplace()
    spark.table(f"{catalog}.docs_smoke.bucket_counts").show()
    spark.sql(f"select * from {catalog}.docs_smoke.bucket_counts.snapshots").show(
        truncate=False
    )

    print("Open the Spark UI now if you want to inspect the completed jobs.")
    time.sleep(5)
finally:
    spark.stop()
PY
```

The sleep at the end is just a convenience during manual testing so there is
time to open the UI. Remove it from real jobs.

## What Not To Do

Do not treat the legacy local warehouse PVC as the shared lakehouse. The active
shared lakehouse path is the `trino_iceberg` catalog backed by PostgreSQL
metadata and RustFS object storage.

Do not debug a UI backend error without checking service endpoints.

Do not rely on a PySpark package installed only on the client when executor code
needs it.

Do not change Spark, Iceberg, Java, or the image tag independently without
checking the compatibility set.

Do not reuse image tags casually with `IfNotPresent`.

Do not hand-edit generated CRD bindings.

Do not put important jobs only in notebooks. Use notebooks to discover; move
repeatable behavior into repo-owned code and a scheduler or job runner.

Do not run `pulumi up`, `pulumi destroy`, or `just up` from a docs-only pass.
Preview and document the intended change, then apply only when the task asks for
a live infrastructure change.
