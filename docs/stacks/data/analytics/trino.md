# Trino

Source: `pulumi/data/analytics/trino`

Trino is the SQL front door for asking questions across several storage systems at once. It is not the place where most data lives. PostgreSQL remains the transactional database, ClickHouse remains the analytical column store, RustFS remains object storage, and Iceberg remains the table format over object storage. Trino sits above them and gives you a single SQL engine that can plan a query, push parts of that query into source systems where possible, and combine the results.

That distinction matters. If you use Trino as a database replacement, it will feel strange: there is no Trino disk where your PostgreSQL rows get copied, and changing a catalog does not change the source system. If you use it as a federated query engine, the model becomes simpler: each catalog is a doorway into another system, and Trino is the coordinator that decides how to read through those doorways.

The best uses in this repo are exploration, BI, ad hoc joins across systems, smoke tests for lakehouse plumbing, and SQL access to Iceberg tables. The risky uses are source database administration, high-volume production ETL without understanding the plan, and huge cross-system joins that look compact in SQL but force Trino to pull a lot of data over the network.

## What This Stack Deploys

Pulumi installs the Trino Helm chart `1.42.2` with Trino version `480`. The chart runs a small cluster: one coordinator and one worker. That is intentionally sized for a homelab analytics surface and connector checks, not for a large shared warehouse.

Important defaults from the Pulumi program:

```text
Namespace:                  trino
Release name:               trino
Coordinator service:        trino
Tailscale service:          trino-tailscale
Tailscale hostname:         trino
HTTP port:                  8080
Workers:                    1
Cluster query memory:       2GB
Coordinator heap:           2G
Worker heap:                2G
Per-node query memory:      1GB
Catalog credentials secret: trino-catalog-credentials
```

The public shape is deliberately plain: a private Tailscale-exposed HTTP endpoint on port `8080`, plus the in-cluster `ClusterIP` service. The Pulumi program does not configure Trino password authentication. The `--user` value you pass to the CLI is the query identity Trino records; it is not a password check. Treat the private network boundary as part of the access model, and do not expose this endpoint to a public network without adding real authentication.

The stack depends on three other stacks through `StackReference`:

```text
PostgreSQL: kzh/postgresql/mx
ClickHouse: kzh/clickhouse/mx
RustFS:     kzh/rustfs/mx
```

Those references are not incidental. Trino needs PostgreSQL for two separate reasons, ClickHouse for a federated analytical source, and RustFS for object storage behind Iceberg.

## Coordinator And Workers

A Trino client talks to the coordinator. The coordinator parses SQL, checks metadata, plans the query, breaks work into stages and tasks, and schedules that work across workers. Workers do the heavy execution: reading table splits through connectors, filtering, joining, aggregating, sorting, and exchanging intermediate data.

In this stack the Tailscale service selects the coordinator pods:

```text
app.kubernetes.io/component=coordinator
app.kubernetes.io/instance=trino
app.kubernetes.io/name=trino
```

That is why clients connect to `http://trino:8080` or the `trino` service rather than to a worker. The coordinator owns the HTTP API and web UI. Workers are behind the scenes.

With one worker, the cluster still uses the normal Trino architecture, but it has a small execution pool. That is fine for testing catalogs, browsing metadata, writing small Iceberg tables, and running careful analytical queries. If you point a large federated join at it, the limiting factor may be worker memory, source database throughput, network transfer, or the fact that connector pushdown could not do as much as the SQL made you hope.

Two useful ways to remember the boundary:

```text
The coordinator is where the query is accepted and planned.
The workers are where most query work happens.
The catalogs are how Trino reaches external systems.
The external systems still own their data.
```

## Catalogs In This Repo

Trino addresses data as `catalog.schema.table`.

The catalog is the connector instance. The schema is the namespace inside that connector. The table is the table or view. Fully qualified names are worth using in shared examples because they remove guessing:

```sql
select *
from pg_airflow.public.dag
limit 10;
```

This stack renders these catalog families into the Helm chart:

```text
tpch                    Generated benchmark/sample data
tpcds                   Generated benchmark/sample data
memory                  Ephemeral in-memory scratch catalog
clickhouse              ClickHouse through its HTTP interface
iceberg                 Iceberg tables on RustFS with PostgreSQL JDBC metadata
pg_<database_name>      PostgreSQL databases through the PostgreSQL connector
```

The configured PostgreSQL catalogs are:

```text
pg_airflow
pg_app
pg_coder
pg_convexdb
pg_immich
pg_mlflow
pg_n8n
pg_postgres
pg_stitch
pg_temporal
pg_temporal_visibility
```

Those names come from the `trino:postgresDatabases` config list. The Pulumi program creates one catalog file per database and grants a shared PostgreSQL role enough access to connect and read from the `public` schema. If a database exists but a table is invisible, separate the questions: did the catalog render, did the source database exist, did the table exist in `public`, and does the Trino reader role have access to that table?

The sample catalogs are safe first stops. `tpch` and `tpcds` are generated data inside Trino connectors, not production systems. `memory` is useful for temporary experimentation, but it is not durable storage.

## The RustFS, Iceberg, PostgreSQL, ClickHouse Relationship

The most important dependency chain is Iceberg:

```text
Trino SQL
  -> iceberg catalog
  -> PostgreSQL JDBC catalog metadata
  -> RustFS object data under s3://trino-iceberg/warehouse
```

Iceberg tables have two parts. The table data files live in object storage. The table metadata tracks schemas, snapshots, manifests, and where the current version of the table lives. In this stack, RustFS stores the object files and PostgreSQL stores the JDBC catalog metadata.

Pulumi makes that path usable by creating:

```text
Iceberg metadata database: trino_iceberg
Iceberg metadata user:     trino_iceberg
Iceberg JDBC catalog name: trino_iceberg
Iceberg bucket:            trino-iceberg
Iceberg warehouse:         s3://trino-iceberg/warehouse
```

Spark is intentionally wired to this same Iceberg catalog contract. Spark's
catalog is named `trino_iceberg` because Iceberg's Spark JDBC catalog uses the
Spark catalog name as the JDBC `catalog_name`. Trino exposes the same metadata
and objects through the user-facing catalog named `iceberg`. That means the same
table is addressed as:

```text
Spark: trino_iceberg.<schema>.<table>
Trino: iceberg.<schema>.<table>
```

If a Spark-created table does not appear in Trino, check the JDBC catalog name
first. Sharing the same RustFS bucket is not sufficient by itself; Spark and
Trino must also use the same JDBC catalog metadata name.

It also creates two Kubernetes Jobs before the Trino chart starts:

```text
trino-iceberg-catalog-tables
trino-iceberg-bucket
```

The first job creates the JDBC catalog tables in PostgreSQL if they do not exist. The second job creates the RustFS bucket if it does not exist. If Iceberg table creation fails, inspect those jobs and the two backing systems before deciding the Trino coordinator is at fault.

PostgreSQL has a second role too: it is a federated source system. The `pg_*` catalogs read application databases through the PostgreSQL connector using the generated `trino_reader` role. That role is intentionally different from the Iceberg metadata role. One reads source databases; the other owns Iceberg catalog metadata.

ClickHouse is simpler from Trino's perspective. The `clickhouse` catalog connects to the in-cluster ClickHouse HTTP endpoint:

```text
jdbc:clickhouse:http://clickhouse.clickhouse.svc.cluster.local:8123/default?compress=0
```

The connector uses credentials from the ClickHouse stack outputs, injected through the same Kubernetes Secret as the other catalog credentials. Use this path when ClickHouse is part of a broader SQL question. Use native ClickHouse clients when you are designing engines, inspecting `system.*` tables, or diagnosing ClickHouse-specific behavior.

## Connect

Read the non-secret outputs from the stack:

```bash
cd pulumi/data/analytics/trino

pulumi stack output --stack mx namespace
pulumi stack output --stack mx hostname
pulumi stack output --stack mx url
pulumi stack output --stack mx catalogs
pulumi stack output --stack mx postgres_catalogs
pulumi stack output --stack mx iceberg_warehouse
```

From a laptop on the private network, connect with the Trino CLI:

```bash
HOST="$(pulumi stack output --stack mx hostname)"
trino --server "http://$HOST:8080" --user "$USER"
```

The same endpoint also serves the Trino web UI:

```text
http://trino:8080
```

Inside Kubernetes, use the service DNS instead of the Tailscale hostname:

```text
http://trino.trino.svc.cluster.local:8080
```

If Tailscale access is not the path you want for a quick local check, use a port-forward:

```bash
NS="$(pulumi stack output --stack mx namespace)"
kubectl port-forward -n "$NS" svc/trino 8080:8080
```

Then connect to:

```bash
trino --server http://127.0.0.1:8080 --user "$USER"
```

Start with generated data. This proves the client, coordinator, and basic execution path without touching any real backend:

```sql
show catalogs;
select * from tpch.tiny.nation limit 5;
select count(*) from tpcds.tiny.store_sales;
```

Then ask Trino what it sees about itself:

```sql
select node_id, http_uri, node_version, coordinator, state
from system.runtime.nodes;

select catalog_name
from system.metadata.catalogs
order by catalog_name;
```

## Discover Before Querying

The fastest way to get oriented is to walk from catalog to schema to table.

```sql
show catalogs;
show schemas from pg_airflow;
show tables from pg_airflow.public;
describe pg_airflow.public.dag;
```

For a more queryable inventory:

```sql
select table_schema, table_name
from pg_airflow.information_schema.tables
where table_schema = 'public'
order by table_name
limit 50;
```

The same pattern works for ClickHouse and Iceberg:

```sql
show schemas from clickhouse;
show tables from clickhouse.default;

show schemas from iceberg;
show tables from iceberg.lab;
```

When a name fails, keep the three-part model in mind. `Catalog does not exist` is different from `Schema does not exist`, and both are different from a permission failure reading a table.

## Query PostgreSQL Catalogs

The `pg_*` catalogs are read paths into PostgreSQL databases managed elsewhere in the repo. They are useful when you want SQL visibility into application data without opening a direct PostgreSQL session.

Examples:

```sql
show tables from pg_airflow.public;

select table_name
from pg_mlflow.information_schema.tables
where table_schema = 'public'
order by table_name;
```

Use Trino for reads and exploration. Do not use it as the normal administration path for PostgreSQL. If you need to create databases, install extensions, repair grants, inspect replication, or perform PostgreSQL maintenance, use the PostgreSQL stack and native PostgreSQL tools.

A practical habit is to begin with metadata and small limits:

```sql
select count(*) from pg_airflow.public.<table_name>;

select *
from pg_airflow.public.<table_name>
where <indexed_or_selective_column> = <value>
limit 50;
```

The PostgreSQL connector can push filters and projections into PostgreSQL in many cases, but it cannot make every cross-system query cheap. Select the columns you need, filter early, and check the query plan when a query matters.

## Query ClickHouse Through Trino

ClickHouse is exposed as the `clickhouse` catalog:

```sql
show schemas from clickhouse;
show tables from clickhouse.default;
select count(*) from clickhouse.default.<table_name>;
```

This path is useful when ClickHouse is one side of a federated query:

```sql
select
    c.<dimension_column>,
    count(*) as events
from clickhouse.default.<event_table> c
join pg_app.public.<small_dimension_table> d
    on c.<dimension_key> = d.<dimension_key>
where c.<timestamp_column> >= current_timestamp - interval '1' day
group by c.<dimension_column>
order by events desc
limit 20;
```

That example is intentionally shaped with a large event table and a smaller dimension table. Cross-system joins are most useful when one side is small, or when filters reduce the large side before the join. If both sides are large and neither connector can push down enough work, Trino has to move a lot of rows into its own execution engine.

For ClickHouse-specific work, go native. Table engines, `ORDER BY` keys, partitions, merges, `system.query_log`, and storage tuning belong in ClickHouse clients.

## Use Iceberg On RustFS

Iceberg is the catalog to use when you want durable lakehouse tables managed through Trino. In this repo, table data lands in RustFS under `s3://trino-iceberg/warehouse`, and catalog metadata lands in PostgreSQL.

Spark uses the same warehouse and metadata contract for distributed writes.
Prefer this shared path for tables that should be usable from both Spark and
Trino. Use Trino's `iceberg` catalog name in Trino SQL and Spark's
`trino_iceberg` catalog name in Spark SQL.

A small table test:

```sql
create schema if not exists iceberg.lab;

create table if not exists iceberg.lab.docs_smoke (
    id bigint,
    note varchar
);

insert into iceberg.lab.docs_smoke values
    (1, 'hello from trino'),
    (2, 'stored through iceberg');

select *
from iceberg.lab.docs_smoke
order by id;
```

A slightly more realistic CTAS smoke test:

```sql
create table iceberg.lab.nations_from_tpch as
select
    nationkey,
    name,
    regionkey
from tpch.tiny.nation;

select count(*) from iceberg.lab.nations_from_tpch;
```

That single `create table as select` proves a lot:

```text
Trino can read from a source catalog.
Trino can write Iceberg metadata into PostgreSQL.
Trino can write object files into RustFS.
Trino can read the resulting Iceberg table back.
```

Do not manually edit the RustFS warehouse objects or the PostgreSQL Iceberg metadata tables. Iceberg tracks snapshots and metadata references. Deleting a file that looks unused, changing a metadata row by hand, or moving objects under the warehouse can make the table unreadable even if the bucket still exists.

## Use The Memory Catalog For Scratch Work

The `memory` catalog is useful for temporary tables during a session or a short-lived experiment:

```sql
create schema if not exists memory.default;

create table memory.default.sample_nations as
select *
from tpch.tiny.nation;

select count(*) from memory.default.sample_nations;
```

Keep expectations low. The stack config caps the memory connector at `128MB` per node, and the data is not durable. If a result matters, write it to Iceberg or to the appropriate source system through that system's normal path.

## Federated SQL: Useful, But Not Free

Federation is the reason this stack exists. You can write a query that reads from PostgreSQL, ClickHouse, and Iceberg through one endpoint:

```sql
with app_rows as (
    select <id>, <category>
    from pg_app.public.<table_name>
    where <category> is not null
),
recent_events as (
    select <id>, count(*) as event_count
    from clickhouse.default.<event_table>
    where <timestamp_column> >= current_timestamp - interval '7' day
    group by <id>
)
select
    a.<category>,
    sum(e.event_count) as events
from app_rows a
join recent_events e on a.<id> = e.<id>
group by a.<category>
order by events desc;
```

That is the productive shape: let each source do what it can, keep intermediate results as small as possible, and join the reduced sets. The unproductive shape is `select *` from two large systems and joining them without filters.

Use these habits for queries you expect to keep:

```text
Start with metadata and limits.
Use fully qualified names.
Select only the columns needed.
Filter before joining.
Prefer small dimension joins over large fact-to-fact joins.
Use EXPLAIN before running expensive queries.
Use EXPLAIN ANALYZE only when you are prepared to execute the query.
Materialize durable results in Iceberg, ClickHouse, or workflow-managed tables.
```

Example plan check:

```sql
explain
select count(*)
from pg_airflow.public.<table_name>
where <column_name> is not null;
```

For a query that becomes important, do not stop at "Trino can run it." Decide whether Trino should run it repeatedly, whether it should become an Iceberg table, whether ClickHouse is the better serving layer, or whether Spark/Flink/Airflow/Dagster should own a repeatable transformation.

## Python Clients

Python can use Trino through the `trino` package. From inside the cluster, point at the Kubernetes service:

```python
import trino

conn = trino.dbapi.connect(
    host="trino.trino.svc.cluster.local",
    port=8080,
    user="kevin",
    catalog="tpch",
    schema="tiny",
)

cur = conn.cursor()
cur.execute("select nationkey, name from nation order by nationkey limit 5")
print(cur.fetchall())
```

From a laptop, use the Tailscale hostname:

```python
import trino

conn = trino.dbapi.connect(
    host="trino",
    port=8080,
    user="kevin",
    catalog="iceberg",
    schema="lab",
)
```

If a client library supports HTTPS or authentication settings by default, keep the stack's actual endpoint in mind: this deployment serves plain HTTP on a private network unless you add a different access layer.

## What To Inspect When Something Fails

Start by splitting failures into layers. A Trino query touches the client, network path, coordinator, worker, catalog config, source credentials, source service, and source data. Checking those separately is faster than treating every error as a Trino outage.

Basic cluster view:

```bash
cd pulumi/data/analytics/trino
NS="$(pulumi stack output --stack mx namespace)"

kubectl get pods,svc,endpoints,jobs,secrets -n "$NS"
kubectl get endpoints -n "$NS" trino
kubectl get endpoints -n "$NS" "$(pulumi stack output --stack mx tailscale_service_name)"
```

Coordinator logs:

```bash
kubectl logs -n "$NS" -l app.kubernetes.io/component=coordinator --tail=200
```

Worker logs:

```bash
kubectl logs -n "$NS" -l app.kubernetes.io/component=worker --tail=200
```

If the web UI and `tpch` queries work, the coordinator and basic execution path are alive. Move to the failing connector.

Trino-side runtime checks:

```sql
select *
from system.runtime.nodes;

select query_id, state, user, query
from system.runtime.queries
order by created desc
limit 10;
```

Connector-focused checks:

```sql
show catalogs;
show schemas from <catalog>;
show tables from <catalog>.<schema>;
select * from <catalog>.<schema>.<table> limit 5;
```

PostgreSQL failures usually fall into one of these buckets:

```text
The database name is missing from trino:postgresDatabases.
The database exists but the public schema or table grants are missing.
The table lives outside public.
The PostgreSQL stack output changed.
The source database is unavailable.
```

ClickHouse failures usually point to:

```text
ClickHouse pod or service readiness.
The in-cluster HTTP endpoint.
Connector credentials.
ClickHouse-side table/schema behavior.
```

Iceberg failures usually point to:

```text
The RustFS bucket job.
The RustFS S3 endpoint.
S3 credentials or path-style access.
The PostgreSQL Iceberg database.
The JDBC catalog bootstrap tables.
The Spark and Trino JDBC catalog names no longer matching.
Warehouse objects or Iceberg metadata changed outside Iceberg.
```

Slow queries are a different class of problem. A slow query can mean Trino is unhealthy, but it more often means the query is moving too much data, missing useful filters, joining large sources, spilling, or not getting the connector pushdown you expected. Use `EXPLAIN`, reduce the query, and compare source-native performance before changing infrastructure.

## Common Errors And What They Usually Mean

`Catalog does not exist`

The catalog file did not render, the pod has not picked it up, you are connected to a different Trino endpoint, or the catalog name is not what you think. Check `pulumi stack output --stack mx catalogs`, then `show catalogs`.

`Schema does not exist`

The catalog is present, but the namespace inside the source is not. For PostgreSQL, the common schema is `public`. For ClickHouse, schemas map to ClickHouse databases. For Iceberg, create a namespace with `create schema`.

`Table does not exist`

The schema is present, but the table name is wrong, the table is in another schema, the connector cannot see it, or the reader role lacks access.

`Access denied`

The connector reached the source, but credentials or grants are not enough. For PostgreSQL, inspect the `trino_reader` role and grants. For ClickHouse and RustFS-backed Iceberg, inspect the source stack outputs and the Kubernetes Secret wiring without printing secret values into docs or tickets.

Iceberg create or insert fails

Check both halves of Iceberg: PostgreSQL metadata and RustFS object storage. A working PostgreSQL connection does not prove the bucket exists. A working bucket does not prove the JDBC catalog tables exist.

The CLI cannot connect

Check whether the hostname is reachable from where the client is running. From inside the cluster, use service DNS. From a laptop, use the Tailscale hostname. Confirm the URL is HTTP on port `8080`.

## Change Catalogs Safely

Catalog changes are cross-stack integration changes. A preview can look small while the blast radius is large: a renamed catalog breaks saved SQL, a changed credential breaks BI tools, and a changed Iceberg warehouse can separate metadata from data.

Keep these rules:

```text
Edit the Pulumi program, not rendered Kubernetes ConfigMaps.
Keep secret values in Pulumi outputs, Pulumi secrets, or Kubernetes Secrets.
Use StackReference outputs instead of copying hostnames and passwords.
Prefer adding a new catalog before removing or renaming an existing one.
Treat catalog names as user-facing API.
Test the exact query shape that motivated the catalog change.
```

For a new PostgreSQL database in the existing PostgreSQL cluster, the normal path is to add the database name to `trino:postgresDatabases`. That creates a catalog named `pg_<database_name>` and grants the `trino_reader` role connect, schema usage, table select, and sequence access for the configured public schema. If the database uses other schemas, or if future tables need automatic access, handle that deliberately instead of assuming the existing grant shape covers it.

For a new non-PostgreSQL system, decide these things before editing:

```text
What connector name does Trino use?
Does the connector ship with Trino, or does it need extra jars?
What stable catalog name will clients use?
Where do credentials come from?
How does a Trino pod reach the service from inside Kubernetes?
What source-side permissions should the connector have?
What small query proves the catalog works?
What real query proves the catalog was worth adding?
```

For Iceberg changes, be extra conservative. The warehouse path, bucket name, JDBC catalog name, metadata database, Spark catalog name, and catalog bootstrap tables are a single contract. Renaming one part without a migration plan can leave existing tables orphaned from their metadata or files, or make Spark and Trino look at different metadata while both appear healthy.

Use the repo gates:

```bash
just sync pulumi/data/analytics/trino
just check-python
just lint
git diff --check
just preview pulumi/data/analytics/trino stack=mx
```

Do not run `pulumi up`, `pulumi destroy`, or `just up` unless the person requesting the work explicitly asks for an apply or destructive action.

After an apply, test across the catalog families:

```sql
select * from tpch.tiny.nation limit 5;
show tables from pg_airflow.public;
show schemas from clickhouse;
```

Then run the Iceberg create/insert/read smoke test from this page. For shared lakehouse changes, also write one tiny table through Spark's `trino_iceberg` catalog and read it through Trino's `iceberg` catalog. The stack is healthy only when the coordinator works and the catalogs that people rely on still work. For Trino, "the pod is running" is just the first layer of the check.
