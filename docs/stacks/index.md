# Stack Docs Map

The stack guides are the service map for this repository. They are not meant to
replace the Pulumi programs, the stack config, Pulumi state, or the live
cluster. They give you the mental model you need before those facts make sense:
what the service is for, where the important state lives, how requests or jobs
reach it, which other stacks it depends on, and what to inspect when something
does not behave the way the user-facing surface suggests it should.

A stack in this repo is a Pulumi project deployed to a named Pulumi stack,
usually `mx` for this checkout. The project directory under `pulumi/` contains
the Python program, dependency files, chart values, local assets, dashboards,
custom images, and any service-owned helper code. The Pulumi stack adds config,
secrets, imported ESC environment values, outputs, and state history. Kubernetes
then adds another layer: live resources, controller reconciliation, events,
pod logs, PVCs, generated Secrets, and whatever drift exists between desired and
actual state.

That layering is the whole reason these docs exist. "The pod is ready" is not
the same as "the service works." "Pulumi preview is clean" is not the same as
"the workflow can run." "Grafana opens" is not the same as "the service has
metrics." A useful guide teaches you which claim you are trying to prove.

The guide pages are grouped the same way the repository is grouped:

```text
pulumi/core/   shared cluster machinery
pulumi/data/   durable state, analytics, streaming, and workflow systems
pulumi/apps/   user-facing or integration applications
pulumi/ops/    operational visibility
```

Start with the page closest to the thing you are trying to use or repair. Then
walk outward to the pages that explain its dependencies. The code still wins
over prose for exact values. A guide should tell you how to think; `__main__.py`,
`Pulumi.yaml`, stack config, stack outputs, and the live cluster tell you what is
true right now.

## Choose A Guide

If you know the service name, use the sidebar or the map below and open that
service page first. Most pages start from first principles and then narrow into
this repo's implementation, access path, inspection commands, common failure
patterns, and safer change workflow.

If you know only the symptom, pick the guide by the first failing boundary.

For a broken private URL, read the service page and the
[Tailscale Operator](/stacks/core/networking/tailscale) page. The important
path is client, tailnet name, Tailscale proxy or ingress object, Kubernetes
Service, EndpointSlice, pod, and application process. If the Service has no
endpoints, fix selector, readiness, or pod health before changing the exposure
layer.

For a broken public URL or webhook, read the service page and
[Cloudflare Tunnel](/stacks/core/networking/cf-tunnel). The public edge can be
fine while the backend Service is empty, and the app can be fine while the route
points at the wrong port. Prove each hop before editing route declarations.

For a database-backed app, read the app page, the database page, and sometimes
the operator page. [PostgreSQL](/stacks/data/databases/postgres) is the shared
relational contract for many stacks. [CloudNativePG](/stacks/core/operators/cnpg)
is the controller behind PostgreSQL clusters. [MySQL Operator](/stacks/core/operators/mysql)
matters for MySQL-backed apps such as MediaWiki and WordPress. The app's
database Secret, generated credentials, migrations, router services, PVCs, and
StackReference outputs are different pieces of the same path.

For an analytics or data workflow issue, decide whether the failing thing is
storage, compute, orchestration, or a human-facing UI. Storage failures send you
to [RustFS](/stacks/data/storage/rustfs), [PostgreSQL](/stacks/data/databases/postgres),
[ClickHouse](/stacks/data/analytics/clickhouse), or [Kafka](/stacks/data/streaming/kafka).
Compute failures send you to [Spark](/stacks/data/analytics/spark),
[Trino](/stacks/data/analytics/trino), [Flink](/stacks/data/streaming/flink),
or [Slurm](/stacks/data/analytics/slurm). Orchestration failures send you to
[Airflow](/stacks/data/workflow/airflow), [Dagster](/stacks/data/workflow/dagster),
[Temporal](/stacks/data/workflow/temporal), or [n8n](/stacks/data/workflow/n8n).

For missing metrics or dashboards, read [Monitoring](/stacks/ops/monitoring)
and the service page that owns the dashboard. Monitoring provides Prometheus,
Grafana, CRDs, and dashboard discovery. Service stacks own service-specific
dashboards and monitors. A dashboard can be absent because Grafana discovery is
broken, because the service stack did not create the ConfigMap, because a label
does not match, or because Prometheus has no target to scrape.

For an operator or CRD issue, start in [Core Operators](/stacks/core/operators/).
Operator-backed systems have at least three layers:

```text
Pulumi program -> custom resource -> operator-created resources
```

Pulumi can create a custom resource successfully while the operator later fails
to reconcile it. The operator can be healthy while one custom resource is
invalid. A custom resource can be ready while the consuming application points at
the wrong hostname or credential. The operator pages help you keep those layers
separate.

## How The Areas Differ

Core stacks are platform machinery. They answer questions like "how does traffic
reach services?", "who reconciles this custom resource?", "who issues
certificates?", and "where does sensitive material come from?" They are usually
not the product a person is trying to use directly, but they explain failures
that cut across multiple products. A core change can affect every consumer of
private routing, public routing, TLS, database reconciliation, Ray clusters, or
secret handling, so preview the core stack and then inspect representative
consumers.

Data stacks are where the truth often lives. The truth might be a relational
row, an object in a bucket, a Kafka topic, an Iceberg table, a workflow history,
a model artifact, a notebook file, or a dashboard definition. Before changing a
data stack, identify the durable state, the credentials, the service contract,
the backup or recovery path, and the downstream consumers. Kubernetes can
recreate pods. It cannot infer the schema, restore a deleted bucket, or guess
which credential a client expected.

Application stacks are where infrastructure becomes visible to a person or an
external system. They should be debugged from the outside in: can the expected
user action happen, does the URL or callback reach the right route, does the
Service have endpoints, are pods ready, can the app read its database and
storage, and are external tokens or provider settings valid? Deployments,
Services, and probes are replaceable. Uploaded media, workspace files, runtime
state, generated keys, database contents, and migration history deserve more
care.

Ops stacks provide feedback. In this repo that currently means monitoring:
Prometheus-compatible scraping, Grafana, dashboard discovery, and observability
CRDs. Ops is not a dumping ground for every dashboard. The monitoring stack owns
the platform; service stacks own the dashboards and monitors that explain their
own behavior. If a panel is empty, follow the scrape path instead of assuming the
visualization is wrong.

## Core Guides

The [Core](/stacks/core/) overview explains the shared cluster machinery and why
platform health and consumer health need to be inspected separately.

[Networking](/stacks/core/networking/) is the first stop when a request cannot
reach a service. It frames every request as a chain from client name resolution
to proxy or edge, Kubernetes Service, endpoints, selected pod, and application
process.

[Tailscale Operator](/stacks/core/networking/tailscale) is the private-access
path for internal tools, admin surfaces, notebooks, dashboards, databases, and
services intended for trusted tailnet clients. Read it when a private hostname
does not resolve, a Tailscale-exposed Service has no endpoints, a tailnet
identity changed, or you are deciding whether a new service should be private by
default.

[Cloudflare Tunnel](/stacks/core/networking/cf-tunnel) is the public-edge path.
Read it for public DNS, external webhook reachability, Cloudflare policy, tunnel
connector health, and public routes into Kubernetes Services. It is the right
guide when an internet-facing URL returns a backend error, but it will also keep
you honest about whether the application Service is healthy before the public
route gets blamed.

[Operators](/stacks/core/operators/) explains the controller pattern. Read this
overview when Pulumi creates a higher-level custom resource and another
controller is responsible for the pods, services, status, and repair loop that
follow.

[CloudNativePG](/stacks/core/operators/cnpg) is the PostgreSQL operator layer.
It matters because PostgreSQL is a shared dependency for many stacks. Read it
when the operator pod is unhealthy, a PostgreSQL `Cluster` custom resource is
not reconciling, or a consuming stack might be confusing database health with
application credential drift.

[MySQL Operator](/stacks/core/operators/mysql) is the controller behind
MySQL-backed application databases. Read it for MediaWiki or WordPress database
issues, router services, `InnoDBCluster` status, generated CRD bindings, and the
line between changing the shared operator and changing an app-owned MySQL
cluster.

[KubeRay](/stacks/core/operators/kuberay) installs the Ray operator and a
development Ray cluster. Read it when Ray workloads, Ray cluster custom
resources, dashboard access, packaging, or operator-created pods are the thing
being inspected.

[Security Services](/stacks/core/security/) is the map for trust and secret
management. It is intentionally conservative: show commands, describe purpose,
and avoid writing secret values into docs, chat, commits, or PR text.

[cert-manager](/stacks/core/security/cert-manager) automates certificate
resources and TLS Secret lifecycle. Read it when a `Certificate`, `Issuer`, or
`ClusterIssuer` is not ready, when a webhook problem blocks reconciliation, or
when a consumer mounts a TLS Secret but the request path still fails.

[Vault](/stacks/core/security/vault) is the in-cluster secrets service. Read it
for initialization, seal state, TLS, policies, auth methods, storage, and
consumer secret paths. Treat it as stateful security infrastructure, not as a
throwaway key-value pod.

## Data Guides

The [Data, Compute, And Workflow](/stacks/data/) overview is the best starting
point when a service stores data, transforms data, schedules work, or gives a
human an interface to inspect results. It asks the most useful first question:
where is the truth?

[Databases](/stacks/data/databases/) explains the database contract model:
service names, credentials, schemas, storage, backups, and consumers.

[PostgreSQL](/stacks/data/databases/postgres) is the default relational platform
for the repo and one of the most important StackReference producers. Read it
before changing exported outputs, database roles, extensions, service names,
credential material, storage settings, or any shared database contract consumed
by Coder, MLflow, Trino, Airflow, Dagster, n8n, Temporal, ConvexDB, or other
services.

[CockroachDB](/stacks/data/databases/cockroach) is a Cockroach-specific SQL
environment. Read it when you want CockroachDB behavior, compatibility testing,
its admin UI, or its single-node deployment details. It is not the default
database home for ordinary app metadata in this repo.

[ConvexDB](/stacks/data/databases/convexdb) is a self-hosted Convex backend and
dashboard. It feels application-shaped because clients use an API and humans use
a dashboard, but it belongs in data because it exposes a data platform with
PostgreSQL-backed state and PVC-backed backend state. Read it when the backend,
dashboard, admin-key path, API ingress, or PostgreSQL connection is the issue.

[Storage](/stacks/data/storage/) explains why object storage changes require
consumer testing, not only pod readiness checks.

[RustFS](/stacks/data/storage/rustfs) is the S3-compatible object store used by
systems such as MLflow artifacts and Trino or Iceberg-style warehouse data. Read
it for buckets, endpoints, credentials, path conventions, client behavior, and
real write/read validation through S3-compatible clients.

[Streaming](/stacks/data/streaming/) covers data in motion. It separates event
logs from stream processing and reminds you that offsets, partitions, schemas,
checkpoints, and consumer groups are operational contracts.

[Kafka](/stacks/data/streaming/kafka) is the durable event log managed by
Strimzi. Read it for topics, partitions, KRaft broker behavior, listener
advertisement, tailnet bootstrap access, smoke produce/consume checks, and the
reason topics should be declared rather than auto-created.

[Flink](/stacks/data/streaming/flink) is the stream processing engine. Read it
for session-cluster behavior, jobs, TaskManagers, JobManager UI, checkpoint and
savepoint thinking, Kafka integration, and the difference between an operator
being healthy and a Flink job actually running.

[Analytics And Compute](/stacks/data/analytics/) maps the tools used to ask
questions, transform data, run experiments, schedule batch work, and show
results.

[Spark](/stacks/data/analytics/spark) is the distributed dataframe, SQL, and
Iceberg-oriented compute stack. Read it for Spark Connect, the Iceberg-capable
runtime image, the shared `trino_iceberg` catalog backed by RustFS, Spark UI,
small query smoke tests, notebook usage, resource sizing, and the relationship
between Spark, Trino, ClickHouse, and object-backed tables.

[Trino](/stacks/data/analytics/trino) is federated SQL. Read it when the task is
to query across systems such as PostgreSQL, ClickHouse, Iceberg, example
catalogs, or future connectors. It coordinates queries; it does not replace the
source systems or their own administration paths.

[ClickHouse](/stacks/data/analytics/clickhouse) is the columnar analytical
database. Read it for event-style tables, analytical scans, native connections,
Trino access, ingestion paths, ClickHouse-specific debugging queries, operator
behavior, backups, and the distinction between analytical storage and
transactional PostgreSQL.

[Marimo](/stacks/data/analytics/marimo) is the reactive notebook workspace.
Read it for token access, the persistent workspace PVC, cluster service
environment variables, and quick checks against Trino, Spark Connect, MLflow,
Kafka, RustFS, ClickHouse, and PostgreSQL.

[MLflow](/stacks/data/analytics/mlflow) records experiment runs, metrics,
parameters, artifacts, and model lineage. Read it when tracking API calls,
artifact uploads, PostgreSQL metadata, RustFS-backed storage, experiment
organization, or client behavior from notebooks and batch jobs need to be
understood together.

[Slurm](/stacks/data/analytics/slurm) is the CPU-oriented batch scheduling stack
through Slinky. Read it when the mental model is login node, `sbatch`, `srun`,
`squeue`, resource requests, controller behavior, job output files, and queue
inspection rather than a Kubernetes Job or a web API.

[Superset](/stacks/data/analytics/superset) is the BI and dashboarding
application. Read it for SQL Lab, dataset definitions, chart and dashboard
metadata, permissions, source connections, and the difference between Superset
metadata and the actual data living in Trino, PostgreSQL, ClickHouse, or other
SQL backends.

[Workflow](/stacks/data/workflow/) explains the differences between schedulers,
asset systems, durable application workflows, and visual automation.

[Airflow](/stacks/data/workflow/airflow) schedules DAGs. Read it for recurring
batch workflows, task dependencies, retries, worker execution, metadata state,
UI access, and the smoke DAG path that proves scheduling and execution instead
of only proving that the webserver is alive.

[Dagster](/stacks/data/workflow/dagster) models data assets and materializations.
Read it when the important question is what data asset exists, what produced it,
which resources it used, and whether user code, run history, or asset metadata
matches the repo-backed definitions.

[Temporal](/stacks/data/workflow/temporal) is durable application workflow
execution. Read it for workflow histories, activities, task queues, namespaces,
worker connectivity, replay semantics, and the difference between a scheduler
and a code-defined durable state machine.

[n8n](/stacks/data/workflow/n8n) is visual automation and webhook-driven glue.
Read it for lightweight integrations, workflow definitions, credentials, webhook
paths, manual executions, and the state that makes automation recoverable across
pod restarts.

## Application Guides

The [Application Services](/stacks/apps/) overview explains why app guides are
written from the outside in. Start with the user action or external integration,
then walk down through routing, Services, pods, storage, databases, external
APIs, and finally the Pulumi program.

[Coder](/stacks/apps/coder) is the development workspace control plane. Read it
when the UI, template path, workspace namespace, database state, workspace PVCs,
agent connection, image pulls, or user create/resume/stop workflow is the thing
to prove.

[Hermes](/stacks/apps/hermes) is a persistent agent runtime. Read it when the
gateway, dashboard, optional browser sidecar, exported runtime commands,
persistent home directory, Codex home, image pinning, PVC ownership, or runtime
state needs to be understood before changing the deployment.

[Langfuse](/stacks/apps/langfuse) is the LLM observability surface. Read it for
trace storage, generated application secrets, private Tailscale access, and the
shared PostgreSQL, shared ClickHouse, RustFS object storage, and
Langfuse-owned Valkey state.

[Immich](/stacks/apps/immich) is the photo and video library. Read it for media
PVC preservation, PostgreSQL metadata, Valkey persistence, disabled
machine-learning behavior, uploads, imports, background processing, and the
backup mindset needed before any storage or chart change.

[MediaWiki](/stacks/apps/mediawiki) is the wiki stack. Read it for page and
revision state, MySQL Operator-managed database storage, generated
`LocalSettings.php`, install and update Jobs, images PVC, admin access handling,
compatibility jobs, dashboard behavior, and migrations.

[WordPress](/stacks/apps/wordpress) is the CMS stack. Read it for the split
between database state and filesystem state, MySQL `InnoDBCluster` behavior,
themes, plugins, uploads, generated passwords, site URL settings, and cautious
chart or image upgrades.

[golink](/stacks/apps/golink) is the private short-link service over Tailscale
tsnet. Read it for tailnet identity, reusable auth keys, SQLite link storage,
PVC-backed tsnet state, link hygiene, key rotation, and the difference between a
Kubernetes pod problem and a tailnet identity problem.

[Stitch](/stacks/apps/stitch) is an external-integration application. Read it
for Twitch, Discord, webhooks, PostgreSQL configuration, Cloudflare webhook
routing, private Tailscale access, and cases where the pod is healthy but a
token, callback, channel, or provider-side setting makes the app unusable.

## Ops Guide

The [Operations](/stacks/ops/) overview is currently centered on monitoring. It
explains the ownership split between the monitoring platform and service-owned
observability assets.

[Monitoring](/stacks/ops/monitoring) is the observability foundation:
Prometheus, Grafana, Prometheus Operator CRDs, metrics-server helpers, dashboard
discovery, retention, and the scrape path from service metrics to Grafana panel.
Read it when dashboards are missing, panels are empty, targets are missing or
down, metrics-server is confused with Prometheus, or you are adding a monitor
that should be useful during a real incident.

## Use A Stack Guide

For normal usage, read the service page before reaching for commands. Most
guides tell you which Pulumi outputs matter. Outputs are often better than
memory because they encode the current deployed contract: URL, namespace, host,
service name, generated command, or non-secret identifier. Retrieve secret
outputs only when you actually need to connect, and keep the values out of docs,
commit messages, PR text, and chat.

The common non-mutating command shape is:

```bash
just projects
just sync <project>
just preview <project> stack=mx
```

For a changed Python entrypoint or docs-adjacent Pulumi change, the usual cheap
checks are:

```bash
just check-python
just lint
git diff --check
```

The guide pages often include more specific checks: open a URL, run a first SQL
query, produce and consume a Kafka message, trigger a smoke DAG, materialize a
Dagster asset, log an MLflow run, submit a Slurm job, start a notebook server,
upload a small Immich asset, or load a Grafana dashboard. Those checks matter
because they prove the service's actual purpose,
not just the Kubernetes resource shape.

Treat an apply as a separate live-infrastructure action. A preview explains what
Pulumi intends to change. It does not prove a migration is safe, a database is
backed up, or a consumer will still accept the output contract. Before applying
risky changes, know what state is durable, what is replaceable, and how you will
verify the service after the change.

## Debug A Stack

Good debugging starts by naming the boundary. A browser error, workflow failure,
dashboard gap, database connection error, or provider failure can all lead to
different stacks.

For request-serving systems, follow the request:

```text
client -> name -> exposure layer -> Kubernetes Service -> endpoints -> pod -> app
```

If the name does not resolve, inspect the exposure layer. If the Service has no
endpoints, inspect selectors, labels, readiness, and pods. If endpoints exist,
inspect pod logs, app health, mounted volumes, database connectivity, TLS
expectations, and external dependencies. Do not solve an empty Service by adding
another route; fix the selected backend.

For operator-backed systems, follow reconciliation:

```text
Pulumi resources -> custom resource status -> operator logs -> generated pods/services/PVCs
```

If the custom resource is not ready, inspect its conditions and events before
rewriting the app. If the operator is healthy and the custom resource is ready,
move up to the consumer: service DNS, credentials, output names, Secret keys,
database names, and app config.

For data systems, follow the durable truth:

```text
contract -> credentials -> storage -> schema or layout -> consumer behavior
```

Ask where the data lives, who writes it, who reads it, how it is backed up, and
what a successful restore or consumer check would look like. A database can be
ready and empty. A bucket can exist with the wrong path convention. A Kafka
topic can exist with a partition count that changes ordering behavior. A
workflow UI can load while workers cannot run the code.

For observability, follow the signal:

```text
app emits metrics
  -> Service exposes metrics
  -> monitor selects Service
  -> Prometheus scrapes
  -> Grafana queries
```

Each arrow can fail independently. Empty panels are not automatically Grafana
problems, and healthy Grafana does not prove Prometheus can scrape the service.

When the likely fix belongs in Pulumi, make it in the owning stack. Do not patch
the running resource as the durable solution. A live patch can prove a theory,
but the repo should be the source of truth for the next restart, refresh, or
preview. Keep changes narrow, preview the changed stack, and preview consumers
when an output, credential, service name, storage path, chart default, CRD field,
or selector contract changes.
