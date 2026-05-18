# ClickHouse

Source: `pulumi/data/analytics/clickhouse`

ClickHouse is the analytical database in this repo. Use it when data should be
stored in tables and queried quickly with scans, filters, group-bys, and
aggregations. It is a good fit for event streams, metrics-like records, logs,
facts, counters, derived dashboard tables, and other data where the common
question is "summarize a lot of rows and return a small answer."

It is not PostgreSQL with a faster engine. PostgreSQL stores rows together
because it is built around transactions: insert one order, update one account,
check one user's current state, enforce constraints, and commit. ClickHouse
stores columns together because it is built around analytical reads: touch one
or five columns across millions or billions of rows, skip chunks that cannot
match the filter, aggregate the result, and avoid reading the rest.

That storage choice is the beginning of almost every ClickHouse design
decision. The system can feel very forgiving because it accepts SQL and ingests
data quickly, but it rewards tables that are designed around read patterns,
batch-oriented writes, explicit types, and a clear answer to "how will this data
be rebuilt or backed up?"

In this repo, ClickHouse is also exposed to Trino as a catalog. Use native
ClickHouse clients when the task is table design, ingestion, tuning,
credentials, system-table inspection, or database-specific debugging. Use Trino
when ClickHouse is one participant in a broader SQL question across systems.

## The Columnar Mental Model

A row store keeps one row's fields near each other. If an application asks for
one order by primary key, the database can fetch that row and all of its fields
together. That is great for transactional state. It is less ideal for a query
like this:

```sql
select
    event,
    count() as events,
    quantile(0.95)(latency_ms) as p95_latency
from app.events
where ts >= now() - interval 1 day
group by event
order by events desc;
```

That query does not need whole rows. It needs only `event`, `latency_ms`, and
`ts`. In ClickHouse those columns are stored separately, compressed separately,
and read separately. If the table is ordered well, ClickHouse can also skip
large ranges of data that fall outside the timestamp or dimension filters.

ClickHouse tables are made of immutable data parts. Inserts create parts.
Background merges combine smaller parts into larger parts. The `MergeTree`
family of engines is the usual starting point because it gives durable storage,
sorting, sparse indexes, partitions, and background merges. When you run an
analytical query, ClickHouse reads the relevant marks and granules from those
parts rather than walking row-by-row through a heap of records.

The most important phrase for a new table is `ORDER BY`. In a `MergeTree`
table, `ORDER BY` is not a uniqueness constraint. It is the storage order and
primary sparse index. It decides which rows sit near each other on disk and
which chunks can be skipped. If most queries filter by tenant and time,
`ORDER BY (tenant, ts)` gives ClickHouse a useful shape. If most queries filter
by event name and then time, `ORDER BY (event, ts)` may be better. If nobody
knows the query shape yet, create a small table, measure real queries, and
adjust before the data becomes expensive to move.

Partitions are different from ordering. A partition is a coarse lifecycle
boundary, often by month for event data:

```sql
partition by toYYYYMM(ts)
```

Partitions help ClickHouse drop or prune large chunks. They are not a substitute
for a good `ORDER BY`. Too many partitions create too many parts and make the
server work harder. A daily partition can be reasonable for high-volume data
with daily retention operations, but monthly partitions are a calmer default
for many homelab analytics tables.

## What This Stack Owns

Pulumi creates the Kubernetes and operator-level pieces. It does not create
application databases or analytical tables for you.

The stack currently wires these resources:

```text
Namespace:                 configurable, defaulting to clickhouse
Operator chart:            Altinity clickhouse operator, chart 0.27.0 by default
Helm release name:         chop
ClickHouseInstallation:    clickhouse by default
Cluster name:              default by default
Layout:                    1 shard, 1 replica
Server image:              clickhouse/clickhouse-server:26.4.2.10 by default
Storage:                   one ReadWriteOnce PVC, 100Gi on local-path by default
Native protocol:           9000
HTTP protocol:             8123
Pulumi service:            ClusterIP named clickhouse
Private exposure:          Tailscale service annotations on that ClusterIP service
Metrics:                   operator metrics, ServiceMonitor, and chart dashboards
Admin user:                generated password, all grants, default username admin
```

The stack exports:

```text
clickhouseHost
clickhousePort
clickhouseAdminUsername
clickhouseAdminPassword   secret output
```

Do not copy the secret output into docs, commit messages, tickets, dashboard
descriptions, or chat. Treat hostnames and tailnet details as environment
details too; use the output names and commands in written docs instead of
pasting private values.

## Operator And Cluster Wiring

There are two controllers in play: Pulumi and the Altinity ClickHouse operator.
Pulumi owns the desired infrastructure objects in this project. The Altinity
operator watches the `ClickHouseInstallation` custom resource and turns that
database-level declaration into the StatefulSets, pods, services, config, and
PVC behavior ClickHouse needs.

The project imports a repo-local generated CRD package:

```python
from pulumi_clickhouse_operator_crds.clickhouse.v1 import ClickHouseInstallation
```

That package is generated from the operator CRD YAML under this project and
published locally under `pulumi/lib/clickhouse_operator_crds`. Do not hand-edit
generated SDK files. If the operator CRD changes, regenerate the bindings with
the repo target and then update stack code against the generated type.

The `ClickHouseInstallation` in this stack defines one cluster with one shard
and one replica. Its pod template pins the ClickHouse image. Its volume claim
template requests the data PVC. Its user config creates the admin user, reads
the password from a Kubernetes Secret, and grants that user broad permissions.

The generated password is created by Pulumi with `pulumi-random`, stored in a
Kubernetes Secret, and exported as a Pulumi secret output. The stack also derives
a short task id from the password so the operator has a non-secret value that
changes when credentials rotate. That keeps credential changes declarative
without putting the password itself into the custom resource.

The service named `clickhouse` is intentionally Pulumi-owned. It is a
`ClusterIP` service with Tailscale exposure annotations and two ports:

```text
tcp    9000 -> native ClickHouse protocol
http   8123 -> ClickHouse HTTP protocol
```

Its selector targets the operator-managed ClickHouse pods only when the operator
has marked them ready:

```text
clickhouse.altinity.com/app=<operator release>
clickhouse.altinity.com/chi=<installation name>
clickhouse.altinity.com/namespace=<namespace>
clickhouse.altinity.com/ready=yes
```

That readiness selector is useful for clients, but it also gives you a clear
debugging signal. If the pod exists and the service has no endpoints, the next
question is "why is the ClickHouse pod not matching the ready selector?" rather
than "should I edit the service by hand?"

## Connect To It

Start with Pulumi outputs. They are the stable contract for laptop access.

```bash
PROJECT=pulumi/data/analytics/clickhouse
STACK=mx

pulumi -C "$PROJECT" stack output --stack "$STACK" clickhouseHost
pulumi -C "$PROJECT" stack output --stack "$STACK" clickhousePort
pulumi -C "$PROJECT" stack output --stack "$STACK" clickhouseAdminUsername
pulumi -C "$PROJECT" stack output --stack "$STACK" --show-secrets clickhouseAdminPassword
```

Use the native protocol for the ClickHouse CLI:

```bash
HOST="$(pulumi -C "$PROJECT" stack output --stack "$STACK" clickhouseHost)"
PORT="$(pulumi -C "$PROJECT" stack output --stack "$STACK" clickhousePort)"
USER="$(pulumi -C "$PROJECT" stack output --stack "$STACK" clickhouseAdminUsername)"

clickhouse client \
  --host "$HOST" \
  --port "$PORT" \
  --user "$USER" \
  --password
```

Passing `--password` without a value lets the client prompt for it. That keeps
the password out of the shell command itself. If a script needs credentials,
load them from Pulumi, a local secret store, or a Kubernetes Secret at runtime
and avoid logging the command with expanded values.

Use the HTTP port for tools that speak ClickHouse over HTTP. It is also useful
for a small reachability check, but native clients are usually nicer for
interactive work:

```bash
curl --data-binary 'select 1' "http://$HOST:8123/"
```

That unauthenticated example only proves that something answered HTTP. Real
HTTP clients should send credentials through their client configuration, not
through a URL pasted into docs.

From inside Kubernetes, use service DNS instead of the Tailscale hostname:

```text
clickhouse.<namespace>.svc.cluster.local:9000
clickhouse.<namespace>.svc.cluster.local:8123
```

The right hostname depends on where the client runs. A laptop should use the
exported Tailscale host. A pod in the cluster should use Kubernetes DNS. Trino
uses its own catalog configuration and should be debugged from the Trino side
when the failure is specifically "Trino cannot query ClickHouse."

## First Database And Table

This stack does not create databases or tables. A good first smoke table should
prove authentication, persistent storage, inserts, scans, and aggregation
without becoming part of an application contract.

```sql
create database if not exists lab;

create table if not exists lab.events
(
    ts DateTime64(3),
    source LowCardinality(String),
    event LowCardinality(String),
    value Float64
)
engine = MergeTree
partition by toYYYYMM(ts)
order by (source, event, ts);

insert into lab.events values
    (now64(3), 'docs', 'page_view', 1),
    (now64(3), 'docs', 'page_view', 2),
    (now64(3), 'docs', 'deploy', 1),
    (now64(3), 'other', 'page_view', 10);

select
    source,
    event,
    count() as rows,
    avg(value) as avg_value
from lab.events
group by source, event
order by source, event;
```

The important choices are small but real:

`DateTime64(3)` preserves millisecond event time.

`LowCardinality(String)` is useful for repeated dimensions such as sources,
event names, states, regions, or categories.

`MergeTree` gives the normal durable analytical table behavior.

`partition by toYYYYMM(ts)` gives a coarse monthly lifecycle boundary.

`order by (source, event, ts)` stores rows in a way that helps queries that
filter by source, event, and time.

If your first real table does not have a clear `ORDER BY`, pause and write down
the queries the table needs to serve. ClickHouse can scan a lot of data, but a
table ordered for the common filters will stay easier to operate.

## Model Tables Around Queries

ClickHouse schema design starts from read paths. For an event table, the
questions are usually:

What time range do queries scan?

Which dimensions appear in `where` clauses?

Which dimensions appear in `group by` clauses?

Which columns are needed for most dashboards?

How long should the data live?

Can the table be rebuilt from an upstream source?

A practical event table might look like this:

```sql
create database if not exists app;

create table if not exists app.events
(
    ts DateTime64(3),
    tenant LowCardinality(String),
    event LowCardinality(String),
    user_id String,
    request_id String,
    duration_ms UInt32,
    status UInt16,
    properties_json String
)
engine = MergeTree
partition by toYYYYMM(ts)
order by (tenant, event, ts, user_id);
```

That design assumes many queries start with a tenant, event name, and time
window. If most queries start with only time, put time earlier. If most queries
start with service or environment, include those dimensions earlier. The order
key is a storage layout. It should match how the table is read.

A few modeling habits help:

Use explicit numeric and timestamp types. A string that contains a number is
slower and harder to aggregate than a number.

Use `LowCardinality(String)` for repeated strings with moderate cardinality.
Do not use it automatically for unique ids.

Keep high-cardinality ids available when needed, but avoid leading the
`ORDER BY` with a nearly unique id unless queries normally filter by that id.

Prefer append-friendly records. ClickHouse can run mutations, updates, and
deletes, but they are heavier than inserting the right derived record.

Treat `Nullable` as a real choice. Nulls are useful, but they add complexity and
can make expressions less direct. If a default value has clear meaning, it may
be better.

Keep raw payloads only when you need them. A `String` column with JSON can be a
useful escape hatch, but dashboards and repeated queries should usually operate
on typed columns.

Do not assume the `ORDER BY` key enforces uniqueness. If you need deduplication,
design for it deliberately with ingestion ids, replacement engines, materialized
views, or a rebuild path.

For derived analytics, it is often useful to keep two layers:

```text
raw append table      receives source-shaped events in batches
serving table         stores dashboard-friendly facts or rollups
```

That lets ingestion stay simple while queries hit a table shaped for readers.

## Ingest Data

ClickHouse likes batches. Single-row inserts work for a smoke test, but a
production path should batch records by size or time. Many tiny inserts create
many tiny parts, which forces the server to spend more work merging and can
make queries less predictable.

For local files, the native client can load structured formats:

```bash
clickhouse client \
  --host "$HOST" \
  --port "$PORT" \
  --user "$USER" \
  --password \
  --query "insert into app.events format JSONEachRow" \
  < events.jsonl
```

For CSV:

```bash
clickhouse client \
  --host "$HOST" \
  --port "$PORT" \
  --user "$USER" \
  --password \
  --query "insert into app.events format CSVWithNames" \
  < events.csv
```

For applications, use a real ClickHouse client library and make batching a
first-class behavior. A good ingestion client has:

Reasonable batch sizes.

Retries that do not silently duplicate data unless the table is designed for
dedupe.

A clear source offset, file name, event id, or run id for tracing.

Metrics for rows written, bytes written, failures, and latency.

A plan for malformed records.

For pipelines, Kafka, Flink, Spark, Airflow, Dagster, or a small application
service can all be valid producers. The important part is not the tool name; it
is that the producer writes in batches, has an ownership boundary, and can be
rerun or repaired when a batch fails.

Use system tables to see whether ingestion is producing a healthy storage
shape:

```sql
select
    database,
    table,
    count() as active_parts,
    sum(rows) as rows,
    formatReadableSize(sum(bytes_on_disk)) as bytes_on_disk
from system.parts
where active
group by database, table
order by sum(bytes_on_disk) desc;
```

If a table has a large number of tiny active parts, fix the ingest path before
assuming the database needs more CPU. Batching is often the first and best
change.

## Query Well

ClickHouse will happily run SQL that asks for too much. The quickest way to
make it feel slower is to select columns you do not need, filter in a way that
cannot use the storage order, or turn every dashboard into a fresh scan of raw
events.

Good first habits:

Select only the columns needed by the answer.

Filter by the leading `ORDER BY` columns when the question allows it.

Filter by time ranges explicitly.

Aggregate in ClickHouse rather than pulling raw rows into a client.

Use materialized views or serving tables for repeated expensive rollups.

Avoid `select *` in dashboards and scheduled jobs.

Prefer this shape:

```sql
select
    toStartOfHour(ts) as hour,
    event,
    count() as events,
    quantile(0.95)(duration_ms) as p95_ms
from app.events
where tenant = 'example'
  and event in ('page_view', 'deploy')
  and ts >= now() - interval 7 day
group by hour, event
order by hour, event;
```

Over this shape:

```sql
select *
from app.events
where toDate(ts) >= today() - 7;
```

The second query reads every column and applies a function to the timestamp in
the filter. It may still run, but it gives ClickHouse less to work with.

For repeated rollups, create a serving table or materialized view instead of
recomputing from raw events every time. For example, a dashboard that always
shows hourly counts can read from an hourly table populated by an ingest job or
materialized view.

When a query surprises you, inspect what ClickHouse did:

```sql
explain indexes = 1
select
    event,
    count()
from app.events
where tenant = 'example'
  and ts >= now() - interval 1 day
group by event;
```

Then check recent query behavior:

```sql
select
    event_time,
    query_duration_ms,
    read_rows,
    read_bytes,
    result_rows,
    query
from system.query_log
where type = 'QueryFinish'
order by event_time desc
limit 20;
```

If `read_rows` is far larger than expected, revisit table order, filters,
partitions, and whether the query belongs on a rollup table.

## Query Through Trino

Trino exposes ClickHouse as a catalog from the Trino stack. That path is useful
when the question spans systems:

```sql
show schemas from clickhouse;
show tables from clickhouse.default;

select count(*)
from clickhouse.default.<table_name>;
```

Use Trino for federation, BI access, and cross-system exploration. Do not use
Trino as the first tool for ClickHouse administration. If a ClickHouse query
fails through Trino, run the equivalent native ClickHouse query before changing
the Trino stack. That tells you whether the problem is ClickHouse itself,
Trino's connector configuration, credentials, or the query translation path.

Superset will often reach ClickHouse through Trino because Trino gives one SQL
surface across multiple systems. That is convenient for dashboards, but a
dashboard still needs a stable table or view underneath it. Do not let a BI
chart become the only place where a ClickHouse data model is defined.

## Storage And Durability

The current stack is a small single-replica deployment. It has persistent
storage, but it is not a highly available ClickHouse cluster and it does not
define a backup system.

The default PVC is `ReadWriteOnce`, sized to `100Gi`, and uses the configured
storage class, `local-path` by default. With local-path storage, the disk is
node-local. That is fine for a homelab analytical store and quick iteration,
but it is not the same as replicated storage or tested disaster recovery.

Keep these distinctions clear:

A PVC helps a pod restart without losing its data directory.

A PVC does not protect against accidental `drop table`, bad migrations,
corrupted source data, node loss, or a storage class replacement.

One ClickHouse replica means there is no second ClickHouse copy to fail over to.

Increasing storage is usually a one-way operational change. Shrinking storage is
not a normal safe operation.

Changing the storage class or volume claim template can imply replacement or a
data migration.

Adding replicas or shards is a cluster design change, not just a count bump.
Replicated ClickHouse tables need coordination through Keeper or ZooKeeper, and
this stack does not currently define that layer.

Before putting important data here, decide which recovery model applies:

The data is derived and can be rebuilt from upstream sources.

The data is primary analytical data and needs a real backup and restore path.

The data is temporary and can be deleted.

For derived data, the most reliable backup may be the upstream source plus a
documented ingestion job. For primary analytical data, use a backup tool or
storage snapshot strategy that is actually tested by restoring into a clean
database. A backup policy that has never restored anything is only a hope.

## Database Changes

ClickHouse makes table creation easy, but schema and storage changes should
still be treated as migrations.

Low-risk changes:

Adding a nullable or defaulted column to an existing table.

Creating a new table or view.

Creating a new database for an isolated use case.

Adding a rollup table fed from an existing source.

Higher-risk changes:

Changing `ORDER BY`, partitioning, or table engine.

Changing data types for existing columns.

Changing a table from raw events to deduplicated/replacing behavior.

Changing the ClickHouse image version.

Changing storage class, storage size, shard count, or replica count.

Renaming the `ClickHouseInstallation`, cluster, service, PVC template, or
metadata names.

For risky table changes, prefer a new table plus backfill:

```sql
create table app.events_v2
(
    ts DateTime64(3),
    tenant LowCardinality(String),
    event LowCardinality(String),
    user_id String,
    duration_ms UInt32
)
engine = MergeTree
partition by toYYYYMM(ts)
order by (tenant, event, ts, user_id);

insert into app.events_v2
select
    ts,
    tenant,
    event,
    user_id,
    duration_ms
from app.events;

select count() from app.events;
select count() from app.events_v2;
```

After validation, switch readers deliberately. A final table rename can be
quick, but the plan should include rollback and client coordination.

For user and permission changes, decide whether the user is experimental or
durable. Temporary users can be created by SQL during exploration. Durable
application users should be managed declaratively through Pulumi and the
operator config, with passwords coming from secrets rather than literals in
code.

## Debug The Kubernetes Layer

Start from the namespace and custom resource:

```bash
PROJECT=pulumi/data/analytics/clickhouse
STACK=mx
NS="$(pulumi -C "$PROJECT" config get namespace --stack "$STACK" 2>/dev/null || printf clickhouse)"

kubectl get chi -n "$NS"
kubectl describe chi -n "$NS" clickhouse
kubectl get pods,svc,endpoints,pvc,events -n "$NS"
```

If the Tailscale hostname does not connect, inspect the Pulumi-owned service:

```bash
kubectl get svc -n "$NS" clickhouse -o wide
kubectl describe svc -n "$NS" clickhouse
kubectl get endpoints -n "$NS" clickhouse
kubectl get pods -n "$NS" --show-labels
```

No endpoints usually means the selector does not match a ready pod. Because the
selector includes the operator readiness label, this often points to pod
readiness, ClickHouse startup, or operator reconciliation rather than a broken
Tailscale path.

Inspect ClickHouse pod logs:

```bash
kubectl logs -n "$NS" -l clickhouse.altinity.com/chi=clickhouse --tail=200
```

Inspect the operator if the `ClickHouseInstallation` is not reconciling:

```bash
kubectl get deploy,pods -n "$NS" -l app.kubernetes.io/instance=chop
kubectl logs -n "$NS" -l app.kubernetes.io/instance=chop --tail=200
```

If the pod is running, test from inside the cluster:

```bash
POD="$(kubectl get pod -n "$NS" -l clickhouse.altinity.com/chi=clickhouse -o jsonpath='{.items[0].metadata.name}')"

kubectl exec -n "$NS" -it "$POD" -- clickhouse-client --query "select version()"
```

That separates database startup from laptop networking. If this works inside
the pod but the laptop cannot connect, look at the ClusterIP service, Tailscale
exposure, local tailnet state, and the hostname you are using. If this fails
inside the pod, debug ClickHouse itself first.

## Debug The Database Layer

Once a client can connect, use ClickHouse system tables. They are often more
useful than Kubernetes logs for query and storage questions.

Recent queries:

```sql
select
    event_time,
    query_duration_ms,
    read_rows,
    read_bytes,
    result_rows,
    memory_usage,
    query
from system.query_log
where type = 'QueryFinish'
order by event_time desc
limit 20;
```

Active parts and table sizes:

```sql
select
    database,
    table,
    count() as active_parts,
    sum(rows) as rows,
    formatReadableSize(sum(bytes_on_disk)) as bytes_on_disk
from system.parts
where active
group by database, table
order by sum(bytes_on_disk) desc;
```

Background merges:

```sql
select
    database,
    table,
    elapsed,
    progress,
    num_parts
from system.merges;
```

Pending mutations:

```sql
select
    database,
    table,
    mutation_id,
    command,
    create_time,
    is_done,
    latest_fail_reason
from system.mutations
order by create_time desc;
```

Columns and types:

```sql
select
    database,
    table,
    name,
    type,
    default_kind,
    default_expression
from system.columns
where database = 'app'
order by table, position;
```

These queries help separate the failure classes:

Cannot connect at all: service, endpoints, Tailscale, pod readiness, or
credentials.

Can connect but authentication fails: admin password, ClickHouse user config,
or stale client credentials.

Can authenticate but SQL fails: schema, permissions, engine behavior, or query
syntax.

Query works but is slow: table order, partitioning, selected columns, parts,
merges, or query shape.

Data is missing: ingest path, source offsets, target table, materialized view,
or retention behavior.

## Safe Repo Changes

This repo treats ClickHouse as live infrastructure. Make changes from the
Pulumi project and verify with the repo's own commands.

For code changes, run the cheap gates first:

```bash
just sync pulumi/data/analytics/clickhouse
just check-python
just lint
git diff --check
```

Then run a targeted preview:

```bash
just preview pulumi/data/analytics/clickhouse stack=mx
```

Do not run `pulumi up`, `pulumi destroy`, or `just up` from a docs pass or
without an explicit apply request.

Read preview output as a migration plan. If a preview shows replacement of the
`ClickHouseInstallation`, PVC, service, namespace, or generated secret, stop and
decide whether that replacement is intended. Renames in Kubernetes metadata can
be destructive even when the Python diff looks small.

A few change categories deserve extra care:

Operator chart upgrades affect CRDs and reconciliation behavior. Check the
chart change, regenerate ClickHouse CRD bindings when needed, run repo checks,
and preview before applying.

ClickHouse image upgrades affect on-disk data format, server behavior, SQL
features, and client compatibility. Treat them as database upgrades, not normal
container refreshes.

Storage changes affect data survival. Increasing size is different from
changing storage class or volume template names. Never assume a PVC replacement
is harmless.

Admin network changes affect who can log in at the ClickHouse layer. Remember
that Tailscale controls the service exposure path, while ClickHouse user
networks control database authentication rules.

Service selector or hostname changes affect every client that uses the exported
host, Trino catalog assumptions, notebooks, scripts, and dashboards.

Shard and replica changes affect table engines, Keeper/ZooKeeper requirements,
data placement, query routing, and recovery. Design that migration before
touching counts.

Generated CRD SDK changes belong under `pulumi/lib/clickhouse_operator_crds`.
Use the generator target; do not edit generated files by hand.

## CRD Regeneration

The checked-in CRD YAML for this stack comes from the Altinity operator chart,
and the generated Python package is consumed by the Pulumi project. The durable
path is:

```bash
just generate-clickhouse-crds
just sync pulumi/data/analytics/clickhouse
just check-python
just lint
git diff --check
just preview pulumi/data/analytics/clickhouse stack=mx
```

The generator is intentionally narrower than "feed every chart CRD into
application code and hope for the best." It should produce the repo-local
bindings used by the stack, while semantic changes to the ClickHouse program
are reviewed in the stack itself.

If CRD generation changes the typed surface, update `__main__.py` deliberately.
Do not mix an operator version bump, a cluster topology change, and a storage
migration into one unexplained diff.

## A Useful Operating Loop

When using ClickHouse day to day, keep the loop small and observable:

Connect with the native client.

Create or inspect the database and table.

Insert a small batch.

Run the query you actually care about.

Check `system.parts` and `system.query_log`.

If the query is repeated, shape a serving table or materialized view.

If the data matters, document how to rebuild or restore it.

If another service reads it, test that service too.

For an end-to-end smoke after infrastructure changes:

```sql
select 1;
select version();

create database if not exists lab;

create table if not exists lab.clickhouse_smoke
(
    ts DateTime64(3),
    label LowCardinality(String),
    value UInt64
)
engine = MergeTree
partition by toYYYYMM(ts)
order by (label, ts);

insert into lab.clickhouse_smoke values
    (now64(3), 'infra', 1),
    (now64(3), 'infra', 2);

select
    label,
    count() as rows,
    sum(value) as total
from lab.clickhouse_smoke
group by label;
```

If Trino is part of the change, also run:

```sql
show schemas from clickhouse;
show tables from clickhouse.lab;
select count(*) from clickhouse.lab.clickhouse_smoke;
```

The goal is not just a green pod. The goal is a database that can be reached,
authenticated to, written to, queried natively, and read through downstream
paths that depend on it.
