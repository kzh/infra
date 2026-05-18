# Analytics And Compute

The analytics area is the part of the repo where data becomes work: someone explores it, queries it, transforms it, trains against it, schedules compute around it, records what happened, and presents the result. The stacks here are intentionally not one giant interchangeable platform. They are a set of tools with different contracts.

The useful split is this:

- [Spark](/stacks/data/analytics/spark) runs distributed dataframe, SQL, and table-format workloads.
- [Trino](/stacks/data/analytics/trino) provides federated SQL across multiple storage systems.
- [ClickHouse](/stacks/data/analytics/clickhouse) stores and queries analytical tables in a columnar database.
- [JupyterHub](/stacks/data/analytics/jupyterhub) gives notebooks inside the cluster network.
- [MLflow](/stacks/data/analytics/mlflow) records experiment runs, metrics, parameters, and artifacts.
- [Slurm](/stacks/data/analytics/slurm) schedules batch jobs through a traditional queue interface.
- [Superset](/stacks/data/analytics/superset) turns SQL datasets into charts and dashboards.

Keeping those boundaries clear makes the system easier to use and much easier to debug. Spark being slow is not the same problem as Trino missing a catalog. A Superset chart failing is not automatically a Superset problem. A notebook import failure is not a Spark outage. A Slurm job pending in the queue is not necessarily a Kubernetes scheduling failure. Start from the tool that owns the failed contract, then move outward.

## How The Pieces Fit

A common analytics path starts in [JupyterHub](/stacks/data/analytics/jupyterhub). A notebook pod runs inside Kubernetes, so it can reach internal service DNS, private databases, Spark Connect, Trino, MLflow, and object storage without turning the laptop into the runtime environment. That makes notebooks a good place to explore a dataset, test a SQL query, run a tiny Spark session, or log a first MLflow run.

When exploration becomes distributed transformation, move to [Spark](/stacks/data/analytics/spark). This repo exposes Spark through Spark Connect, so a notebook or local Python client can create a Spark session while the driver and executors run in the cluster. Spark is the right tool when the work is more than a single SQL statement: parse files, clean records, join and reshape data, create derived tables, run feature engineering, or use Spark libraries. The active Spark Iceberg catalog is now the shared `trino_iceberg` catalog backed by PostgreSQL metadata and RustFS object storage, so Spark-created lakehouse tables can be read from Trino's `iceberg` catalog.

When the question is SQL across systems, use [Trino](/stacks/data/analytics/trino). Trino is the federation layer. It can query PostgreSQL catalogs, ClickHouse, generated example data, and Iceberg tables backed by RustFS and a PostgreSQL JDBC catalog. It is especially useful when the value is in joining or comparing data that lives in different places. It is not a replacement for source database administration, and it does not make large cross-system joins free. Treat it as a query coordinator with connectors, not as a storage engine.

When the data belongs in an analytical database, use [ClickHouse](/stacks/data/analytics/clickhouse). ClickHouse is built for column scans, aggregations, time-series and event-style tables, and repeated dashboard queries over large datasets. It is often the right destination for derived analytics data that needs to be queried quickly. Use native ClickHouse clients for table design, ingestion behavior, system tables, and database-specific debugging. Use Trino's ClickHouse catalog when ClickHouse is one participant in a broader SQL query.

When the work is an experiment, record it in [MLflow](/stacks/data/analytics/mlflow). MLflow does not run the compute; it records the history of compute that ran somewhere else. A notebook, Spark driver, Slurm job, Airflow task, or local script can all log runs. In this repo, MLflow metadata goes to PostgreSQL and artifacts go to RustFS through the tracking server. That gives a run a durable place to keep parameters, metrics, plots, config files, model files, and other artifacts. Do not log secrets as params, tags, or artifacts.

When the work should be submitted to a queue, use [Slurm](/stacks/data/analytics/slurm). This repo runs a small CPU-only Slinky Slurm environment on Kubernetes. The user-facing model is still Slurm: log in, run `sinfo`, submit with `sbatch`, inspect with `squeue`, and explain a job with `scontrol show job`. That is useful for HPC-shaped batch work where a script, resource request, queue, and job output file are the natural interface. It is not currently wired for GPU, Pyxis, Enroot, or a container-heavy HPC workflow.

When the output is meant to be read repeatedly by people, use [Superset](/stacks/data/analytics/superset). Superset is the BI layer. It stores charts, dashboards, saved queries, datasets, users, and permissions, while data stays in Trino, ClickHouse, PostgreSQL, or another SQL backend. In this repo, Trino is often the cleanest first datasource because Superset can connect once and let Trino handle the backend catalogs. Durable dashboards usually need stable datasets or source views; raw application tables tend to make fragile dashboards.

## Choosing The Right Tool

If you are asking "what is in this data?", start with Trino, ClickHouse, or a notebook. Trino is best when the data spans systems. ClickHouse is best when the data is already in ClickHouse and the question is analytical. JupyterHub is best when you need an interactive Python workspace around the question.

If you are asking "how do I turn this dataset into another dataset?", start with Spark unless the transformation is simple SQL. Spark gives you a real distributed execution engine and a dataframe API. Trino can create tables and views too, but it is primarily a query engine. If the transformation becomes a repeated workflow, put the runnable code in Git and let a workflow tool or job submission path own the schedule.

If you are asking "where should this analytical data live?", use ClickHouse for fast analytical tables, PostgreSQL for relational application state, RustFS/S3 for object artifacts and lakehouse data, and MLflow for experiment metadata plus artifacts. Do not use notebooks, pod filesystems, or dashboard definitions as the source of record.

If you are asking "how do I track this model or training run?", use MLflow. Spark, Slurm, and notebooks can all do the compute, but MLflow is the place to compare runs later. A good MLflow run records the code or version, the dataset reference, parameters, key metrics, artifacts, and enough tags to find the run again.

If you are asking "how do I run this batch script with resource requests and queue history?", use Slurm. If the work is Kubernetes-native and container-shaped, a Kubernetes Job, SparkApplication, Airflow task, or Dagster asset may be a better fit. Slurm is strongest when the operator experience should be a scheduler queue rather than a service API.

If you are asking "how do I publish this result?", use Superset once the underlying SQL is stable. Build and test the query first, save a dataset with a clear shape, build one chart, then assemble the dashboard. A dashboard should not be the first place where a query becomes understandable.

## Good End-To-End Paths

For exploration to dashboard, use JupyterHub to inspect the data, Trino to query across systems, ClickHouse or a stable SQL view to hold a dashboard-friendly shape, and Superset to publish the result. This keeps exploratory code out of the dashboard and gives the chart a durable dataset.

For data transformation, use JupyterHub for the first few cells, Spark for the distributed job, object storage or ClickHouse for outputs, and MLflow if the work has experiment metadata worth preserving. Once the transformation matters, move the job out of the notebook and into a repo-owned execution path.

For model or evaluation work, use JupyterHub or Slurm for development, Spark when the data processing is distributed, MLflow for run history, and RustFS/S3 or MLflow artifacts for output files. The important split is that compute can be temporary, but run history and artifacts should survive the process that produced them.

For scheduled analytics, keep the schedule outside this directory unless the scheduler is the subject of the stack. Airflow, Dagster, Temporal, or another workflow layer should decide when the work happens. The analytics services here should provide the execution engine, query layer, storage, experiment tracking, or presentation layer.

## Debug The Contract That Failed

Start with the user-facing symptom, then map it to the owning layer.

A Spark client failure has three different shapes. If the client cannot connect to Spark Connect, inspect the hostname, Tailscale exposure, service endpoints, and pod labels. If the client connects but SQL fails, inspect the Spark pod logs, runtime image, Spark/Iceberg versions, Secret-backed catalog defaults, RustFS endpoint, PostgreSQL Iceberg metadata database, and executor resources. If the Spark UI is unavailable but the pod is running, inspect the UI service endpoints and selectors before changing ingress. This stack deliberately owns Spark Connect and UI services because operator-owned selectors have drifted before.

A Trino query failure is usually connector-specific. `show catalogs` proves the coordinator and client path. If one catalog fails, read the coordinator logs and debug that connector: PostgreSQL grants, ClickHouse reachability, RustFS/S3 credentials, Iceberg JDBC catalog tables, or the catalog file rendered by Helm. A failed ClickHouse query through Trino should still be tested natively in ClickHouse before treating Trino as the only suspect.

A ClickHouse failure splits between Kubernetes reachability and database behavior. If clients cannot connect, inspect the service, endpoints, Tailscale exposure, and pod readiness. If clients connect but queries fail or run poorly, use ClickHouse system tables, table engines, ordering keys, parts, merges, query logs, and credentials. Database-level failures should usually be understood with ClickHouse tools first.

A JupyterHub failure splits between the Hub, the spawned notebook pod, and the service the notebook is trying to reach. If the page does not load, inspect ingress, proxy service, Hub pod, and PVCs. If spawning fails, inspect Hub logs, the single-user pod, image pulls, scheduling, and user PVCs. If a notebook starts but cannot reach Spark, Trino, or MLflow, test from inside the notebook pod so the DNS and network path match the real runtime.

An MLflow failure splits between browser access, metadata storage, and artifact storage. If the UI loads but runs do not appear, confirm the client is using the intended tracking URI and inspect PostgreSQL connectivity. If runs appear but artifacts fail, inspect the bucket bootstrap job, RustFS endpoint, S3 credential secret references, and MLflow server environment. Avoid printing secret values while debugging; existence and key names are usually enough.

A Slurm failure should start with Slurm's own state after SSH works. `sinfo`, `squeue`, and `scontrol show job <jobid>` often explain pending jobs, missing nodes, partitions, resource requests, and controller state. Drop into Kubernetes when the login service has no endpoints, pods are unhealthy, the controller logs show infrastructure errors, or Slurm state and pod state disagree.

A Superset failure usually belongs to one of four layers: web/login, metadata database/cache, datasource driver, or source query. If SQL Lab cannot run `select 1` against the datasource, the dashboard is not the problem. If SQL Lab works and a chart fails, inspect the dataset definition, chart query, filters, cache behavior, and source view. Missing Python drivers should be fixed in the chart bootstrap or image, not by changing a running pod.

## Useful Commands

For a broad, read-only first look:

```bash
kubectl get pods,svc,endpoints,ingress,pvc,jobs -A | rg 'spark|trino|clickhouse|jupyter|jhub|mlflow|slurm|slinky|superset'
```

For service-specific stack outputs, read the owning project:

```bash
cd pulumi/data/analytics/spark
pulumi stack output --stack mx

cd ../trino
pulumi stack output --stack mx
```

For repo checks before changing infrastructure code:

```bash
just sync pulumi/data/analytics/<service>
just check-python
just lint
git diff --check
just preview pulumi/data/analytics/<service> stack=mx
```

Replace `<service>` with `spark`, `trino`, `clickhouse`, `jupyterhub`, `mlflow`, `slurm`, or `superset`. Do not apply from a docs pass. If a preview matters to a code change, run the targeted preview and report whether any blocker is a code regression, missing config, live drift, or an external dependency.

Useful service smoke tests:

```text
Spark:       run a Spark Connect `select 1`, write a tiny `trino_iceberg` table, and open the Spark UI
Trino:       run `show catalogs`, query `tpch.tiny.nation`, and read the Spark-created Iceberg table when testing lakehouse wiring
ClickHouse:  run `select 1`, create a tiny MergeTree table, and aggregate it
JupyterHub:  open the UI, spawn a server, write a file, and import expected packages
MLflow:      log one run with a metric, parameter, and artifact
Slurm:       SSH to the login host, run `sinfo`, `srun hostname`, and a tiny `sbatch`
Superset:    open SQL Lab, query a real datasource, and load a dashboard
```

## Change Discipline

Analytics stacks touch data, credentials, private hostnames, dashboards, and user workflows. A green pod is not a finished validation. Prove the thing the service exists to do.

Before changing a service, identify what state it owns. ClickHouse owns database files on persistent storage. MLflow owns PostgreSQL metadata and RustFS artifacts. Superset owns dashboard metadata in its chart-managed database. JupyterHub owns user PVCs. Spark owns the Spark Connect runtime and a preserved legacy warehouse PVC, while shared Spark/Trino Iceberg table state lives in PostgreSQL metadata plus RustFS objects. Trino owns catalog configuration and cross-stack access assumptions. Slurm owns scheduler behavior, login access, and job output expectations.

Before renaming resources or outputs, find consumers. Trino reads PostgreSQL, ClickHouse, and RustFS contracts. Superset often reads Trino. Jupyter notebooks may connect to Spark, Trino, and MLflow. MLflow depends on PostgreSQL and RustFS. A change that looks local in one stack may break a client path in another.

After changing code, test at the level people use: a Spark query, a Trino query, a ClickHouse query, a notebook spawn, an MLflow run with artifact, a Slurm job, or a Superset datasource and dashboard. The service pages linked above contain the deeper service-specific runbooks; this page is the map for choosing the right one and debugging the right layer first.
