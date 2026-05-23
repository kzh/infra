# Data, Compute, Storage, Streaming, And Workflow

The data area is where this repo stops being a set of Kubernetes Deployments and starts being a platform with memory. Pods are usually replaceable. Database contents, object keys, topic offsets, workflow history, user notebooks, chart metadata, bucket names, service hostnames, and Pulumi output names are not.

Use this page as the map before changing an individual stack page. The service pages explain how to operate one system. This page explains how the systems fit together, where the source of truth lives, and which contracts need extra care.

## The Operating Model

There are four kinds of truth in this area.

Infrastructure truth lives in the Pulumi project that owns the resource: `pulumi/data/<area>/<service>/__main__.py`, its `Pulumi.yaml`, stack config, ESC imports, local `images/` assets, and service-owned dashboards. If a role, database, bucket, topic, service, or ingress matters, prefer declaring it in the owning project over creating it manually.

Runtime truth lives inside the stateful systems. PostgreSQL rows, RustFS objects, Kafka offsets, ClickHouse tables, Temporal history, Airflow run state, Dagster materializations, n8n workflows, Superset dashboards, Marimo workspace files, and Spark warehouse files are the things users actually care about. Kubernetes readiness only says the control plane sees healthy objects; it does not prove the right data still exists.

Contract truth lives in Pulumi outputs, Kubernetes Secrets, service DNS names, Tailscale hostnames, bucket/topic/table names, and catalog names. A harmless-looking output rename can break a consumer stack. A bucket rename can make MLflow or Trino boot successfully while losing the path to its data. A Kafka advertised-listener change can let bootstrap succeed and still break produce/consume.

Workflow truth lives in versioned code when the repo owns it, and in the application metadata store when humans create it through a UI. Airflow DAGs, Dagster user-code images, Temporal workers, n8n flows, Superset datasets, and notebooks all need an explicit ownership story. UI-only work can be useful, but it is operational state; do not confuse it with reproducible infrastructure.

## The Dependency Spine

The shared relational foundation is [PostgreSQL](/stacks/data/databases/postgres). It exports the namespace, cluster name, read-write service name/FQDN, Tailscale hostname, CA material, and superuser connection fields. Those output names are an internal API. Data stacks that read it include [Airflow](/stacks/data/workflow/airflow), [Dagster](/stacks/data/workflow/dagster), [MLflow](/stacks/data/analytics/mlflow), [n8n](/stacks/data/workflow/n8n), [Temporal](/stacks/data/workflow/temporal), [ConvexDB](/stacks/data/databases/convexdb), and [Trino](/stacks/data/analytics/trino). App stacks outside `pulumi/data` also depend on it, so PostgreSQL changes are rarely local.

[RustFS](/stacks/data/storage/rustfs) is the S3-compatible object store. It exports the RustFS namespace, console hostname, S3 hostname, and access credentials. [MLflow](/stacks/data/analytics/mlflow) consumes those outputs for artifact storage and creates its own artifact bucket. [Trino](/stacks/data/analytics/trino) consumes them for the Iceberg warehouse and creates the `trino-iceberg` bucket. [Spark](/stacks/data/analytics/spark) now consumes the same RustFS-backed Iceberg warehouse through Trino's JDBC catalog metadata contract. RustFS owns the storage service; consumers own the buckets and prefixes they need.

[ClickHouse](/stacks/data/analytics/clickhouse) is both a database and an analytics engine. It owns the Altinity operator, `ClickHouseInstallation`, admin credential Secret, persistent volume, and Tailscale-exposed native/HTTP service. [Trino](/stacks/data/analytics/trino) consumes its exported admin username/password to build the `clickhouse` catalog. That makes ClickHouse credential rotation and host/service changes a Trino change too.

[Trino](/stacks/data/analytics/trino) is the federated SQL layer. It reads PostgreSQL, ClickHouse, and RustFS outputs, then creates a reader role across configured PostgreSQL databases, an Iceberg JDBC catalog database in PostgreSQL, a RustFS bucket for the Iceberg warehouse, catalog credentials, and the Trino coordinator service. A Trino preview can be green while one connector is wrong, so connector smoke tests matter after catalog changes.

[Spark](/stacks/data/analytics/spark) is distributed compute through Spark Connect. Its active Iceberg catalog is `trino_iceberg`, backed by the same PostgreSQL JDBC catalog metadata and RustFS warehouse that Trino uses. Spark addresses shared tables as `trino_iceberg.<schema>.<table>`; Trino addresses the same tables as `iceberg.<schema>.<table>`. The old `spark-warehouse` PVC is still preserved and mounted as legacy local storage, but it is no longer the active shared Iceberg warehouse.

[Kafka](/stacks/data/streaming/kafka) is the durable event log. It owns the Strimzi operator, single-node KRaft broker/controller, node pool, listeners, storage, and declared topics. [Flink](/stacks/data/streaming/flink) is the stream processor that would compute over topics, but the current Flink stack is a session-cluster foundation rather than a catalog of production jobs.

[Airflow](/stacks/data/workflow/airflow), [Dagster](/stacks/data/workflow/dagster), [Temporal](/stacks/data/workflow/temporal), and [n8n](/stacks/data/workflow/n8n) are workflow systems, not interchangeable schedulers. Airflow stores DAG/run metadata in PostgreSQL and proves the stack with a ConfigMap smoke DAG. Dagster stores asset/run metadata in PostgreSQL and proves user-code loading with a smoke deployment. Temporal stores workflow history in PostgreSQL and archives to a PVC; application workers live outside the server chart. n8n stores workflow state through PostgreSQL plus a local PVC and can accumulate important UI-authored automation.

The human-facing tools sit on top. [Marimo](/stacks/data/analytics/marimo) gives a reactive notebook workspace wired to the cluster data services. [MLflow](/stacks/data/analytics/mlflow) records experiments in PostgreSQL and artifacts in RustFS. [Superset](/stacks/data/analytics/superset) stores chart/dashboard metadata in shared PostgreSQL, persists chart-managed Redis for cache/result backend state, and queries external data through configured datasources, often Trino. [Slurm](/stacks/data/analytics/slurm) exposes an HPC-style batch interface through Slinky; by default this repo keeps controller persistence disabled and exposes the login service privately.

## Service Map

The durable storage layer is [PostgreSQL](/stacks/data/databases/postgres), [RustFS](/stacks/data/storage/rustfs), [ClickHouse](/stacks/data/analytics/clickhouse), [Kafka](/stacks/data/streaming/kafka), and the service-specific PVCs behind systems like Marimo, Temporal, n8n, ConvexDB, CockroachDB, Superset, and Spark's preserved legacy warehouse. These are the stacks where storage class, PVC identity, database name, bucket name, topic name, extension set, and credential rotation need the most caution. For shared Spark/Trino Iceberg tables, the durable state spans PostgreSQL metadata and RustFS objects.

The compute layer is [Spark](/stacks/data/analytics/spark), [Trino](/stacks/data/analytics/trino), [Flink](/stacks/data/streaming/flink), [Slurm](/stacks/data/analytics/slurm), and [ClickHouse](/stacks/data/analytics/clickhouse). Spark transforms data with executors. Trino coordinates SQL across systems. Flink runs continuous dataflows. Slurm schedules batch work. ClickHouse both stores and computes analytical queries. When debugging compute, separate the engine from its inputs and outputs: a query engine can be healthy while a catalog, bucket, table, topic, checkpoint, or client path is broken.

The streaming layer is [Kafka](/stacks/data/streaming/kafka) and [Flink](/stacks/data/streaming/flink). Kafka truth is topics, partitions, records, offsets, consumer groups, storage, and advertised listeners. Flink truth is jobs, state, checkpoints, savepoints, and the external systems it reads/writes.

The workflow layer is [Airflow](/stacks/data/workflow/airflow), [Dagster](/stacks/data/workflow/dagster), [Temporal](/stacks/data/workflow/temporal), and [n8n](/stacks/data/workflow/n8n). Airflow is for scheduled DAGs and task history. Dagster is for assets and materializations. Temporal is for durable application workflows and task queues. n8n is for visual glue, webhooks, and lightweight integration flows. For all four, a reachable UI is only the first check; trigger or inspect actual work before calling the stack good.

The exploration and presentation layer is [Marimo](/stacks/data/analytics/marimo), [MLflow](/stacks/data/analytics/mlflow), and [Superset](/stacks/data/analytics/superset). Notebooks are a discovery surface, not a hidden production scheduler. MLflow is experiment memory, not the model artifact store by itself; artifacts live in RustFS. Superset is BI metadata plus datasource connections; the source facts remain in Trino, PostgreSQL, ClickHouse, or another backend.

[CockroachDB](/stacks/data/databases/cockroach) and [ConvexDB](/stacks/data/databases/convexdb) are special cases. CockroachDB is a single-node Cockroach environment with persistent storage and private access; use it when you specifically need Cockroach behavior. ConvexDB is application-shaped, with backend and dashboard surfaces, but it depends on PostgreSQL, a CA Secret, and a PVC, so treat it as a stateful data platform component.

## Moving Safely

Start by asking what kind of change you are making.

A state change touches rows, objects, tables, topics, workflow histories, run metadata, PVCs, checkpoint locations, or bucket prefixes. Take extra care with backup/restore and post-change data checks.

An output-contract change touches `pulumi.export(...)`, `StackReference`, Secret names/keys, database names, bucket names, catalog names, service names, ingress hostnames, Tailscale hostnames, or Kafka advertised listeners. Find consumers before editing.

A runtime change touches chart versions, images, Spark/Iceberg versions, Trino connectors, Flink versions, Kafka versions, Temporal schema behavior, Airflow executor behavior, or Superset/MLflow package bootstraps. Preview is necessary, but compatibility testing is the real proof.

A UI-only change touches notebooks, Superset dashboards, n8n workflows, manually configured datasources, or ad hoc workflow definitions. Decide whether it must be exported, documented, or moved into repo-backed code before it becomes hard to reproduce.

Useful read-only checks:

```bash
just projects
rg -n "StackReference|require_output|pulumi.export" pulumi/data
rg -n "kzh/postgresql/mx|kzh/rustfs/mx|kzh/clickhouse/mx" pulumi
kubectl get pods,svc,pvc,ingress -A | rg 'postgres|rustfs|clickhouse|trino|spark|kafka|flink|airflow|dagster|temporal|n8n|marimo|mlflow|superset'
```

For a code change, use the repo gates and a targeted preview:

```bash
just sync pulumi/data/<area>/<service>
just check-python
just lint
git diff --check
just preview pulumi/data/<area>/<service> stack=mx
```

If an output producer changes, preview the consumers too. A PostgreSQL output change can affect Airflow, Dagster, MLflow, n8n, Temporal, ConvexDB, Trino, Spark, and app stacks. A RustFS output or credential change can affect MLflow, Trino, and Spark. A ClickHouse credential or service change can affect Trino. A Kafka listener change can affect every producer and consumer, even if no Pulumi `StackReference` points at Kafka yet.

After an apply, test the work path, not just the resource status. For PostgreSQL, connect through the intended path and query the expected database. For RustFS, write and read an object. For Kafka, produce and consume through the listener clients use. For Trino, query `tpch`, one `pg_*` catalog, `clickhouse`, and `iceberg` if those connectors matter. For Spark, connect through Spark Connect and run a tiny query while the UI is active. For shared Iceberg changes, write a tiny table through Spark's `trino_iceberg` catalog and read it back through Trino's `iceberg` catalog. For Airflow, trigger `homelab_smoke`. For Dagster, materialize the smoke asset. For MLflow, log an artifact. For Superset, run SQL Lab against a real datasource. For n8n, run a workflow. For Temporal, verify server components and at least one real worker/client path when available.

The safest habit is to move from producer to consumer. Identify where truth lives, identify who reads it, preview the owner, preview the readers, then run one live workflow that proves the contract still works.
