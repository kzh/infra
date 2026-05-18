# Databases

The database stacks are the part of the repo where Kubernetes object health is only the outer shell. A Deployment can be replaced, a pod can restart, and a Service can be recreated without much drama. A database change also carries data files, credentials, schemas, extensions, restore procedures, and downstream assumptions that may live in other Pulumi projects.

Treat each database stack as a contract, not as a collection of pods. The contract says which service name clients use, which credentials are valid, where durable bytes live, which schema or extension surface exists, which stack outputs other projects read, and how a restore would be performed if the change goes badly.

This directory has three database pages:

- [PostgreSQL](/stacks/data/databases/postgres) is the shared relational platform. It is the default place for ordinary app metadata and one of the most important `StackReference` producers in the repo.
- [CockroachDB](/stacks/data/databases/cockroach) is a Cockroach-specific SQL environment. In the current repo shape it is single-node, chart-driven, persistent, and useful when the workload specifically needs Cockroach behavior or compatibility testing.
- [ConvexDB](/stacks/data/databases/convexdb) is a self-hosted Convex backend and dashboard. It looks application-shaped, but it owns database state through both PostgreSQL and a PVC, so it belongs in the database operating model.

## What Database Ownership Means

A database stack owns more than the process accepting TCP connections. It owns a name that consumers remember, storage that must survive restarts, secrets that must stay consistent across rotations, and a schema that may be advanced by migrations. The safe mental model is:

```text
service contract + credentials + durable storage + schema + restore path
```

If any part changes, the database may be "up" while the product using it is still broken. A green pod does not prove that the expected database exists, that the role has the right grants, that a consumer Secret has the current password, that an extension is installed in the right database, or that the data on disk is the data the consumer expects.

That distinction matters most for shared PostgreSQL. Many stacks read its exported outputs, then create their own database, role, grants, and Kubernetes Secret. A PostgreSQL output name is therefore an internal API. Renaming it, changing its semantics, changing the service hostname it points at, or rotating the source credential can affect stacks that do not live under `pulumi/data/databases/postgres`.

CockroachDB and ConvexDB have narrower contracts, but the same rule applies. CockroachDB's public SQL service and admin UI ingress define how clients reach it. ConvexDB's public API URL, dashboard URL, backend service, PostgreSQL database, generated role password, CA Secret, and PVC together define the deployment. Looking only at the current pod list misses most of the risk.

## Current Stack Shape

PostgreSQL lives in `pulumi/data/databases/postgres`. CloudNativePG runs the cluster in the `postgresql` namespace, the cluster name defaults to `postgresql-cluster`, and the read-write service is exported as `rw_service_fqdn`. The stack also exposes a Tailscale hostname for operator-side provider access, secret-derived connection material, the CA Secret name, and optional app database/extension configuration. The `mx` config currently creates shared app databases for `immich` and `n8n` and enables `vector`, `cube`, `earthdistance`, and `vchord`.

CockroachDB lives in `pulumi/data/databases/cockroach`. It creates a namespace from config, installs the CockroachDB Helm chart, runs `cockroachdb/cockroach` in single-node mode, disables TLS, provisions a persistent volume, exposes the public service through Tailscale annotations, and creates a private ingress for the admin UI. Because this stack is not currently a multi-node Cockroach deployment, do not treat it as proof that a workload is ready for Cockroach's distributed failure modes.

ConvexDB lives in `pulumi/data/databases/convexdb`. It reads the shared PostgreSQL stack, creates a PostgreSQL role and database for Convex, stores the PostgreSQL URL and CA in Kubernetes Secrets, provisions a PVC for backend local data, runs separate backend and dashboard Deployments, and exposes separate private Tailscale ingresses for the API and dashboard. The API is the client contract; the dashboard is an operator/developer surface; the internal PostgreSQL database is backing state and should not be treated as a general client interface.

## Service Contracts

For in-cluster PostgreSQL consumers, the normal host is the exported `rw_service_fqdn`. For Pulumi providers that run from the operator machine during preview or apply, stacks often use the PostgreSQL Tailscale hostname instead. Those paths are intentionally different. In-cluster pods should usually use Kubernetes DNS; local Pulumi database provisioning needs a route that works from the machine running Pulumi.

Read the PostgreSQL contract from the stack outputs:

```bash
cd pulumi/data/databases/postgres

pulumi stack output --stack mx k8s_namespace
pulumi stack output --stack mx cnpg_cluster_name
pulumi stack output --stack mx rw_service_name
pulumi stack output --stack mx rw_service_fqdn
pulumi stack output --stack mx ts_hostname
pulumi stack output --stack mx ca_secret_name
```

Do not change these output names casually. Consumer projects call `require_output(...)`, so an output rename is a breaking change even when the underlying Kubernetes object still exists. If an output really must change, update and preview the consumers in the same change window.

The current codebase has PostgreSQL consumers in multiple areas, including Coder, Immich, LiteLLM, MLflow, Trino, Airflow, Dagster, n8n, Temporal, and ConvexDB. Some of those consumers create app-specific databases and roles through the PostgreSQL provider. Others pass PostgreSQL connection values into Helm chart values or Kubernetes Secrets. Before changing the shared PostgreSQL stack, search for the exact output or stack reference being changed:

```bash
rg -n 'kzh/postgresql/mx|rw_service_fqdn|require_output\("(username|password|host|port|ts_hostname|ca_)' pulumi -g '!pulumi/lib/**'
```

CockroachDB currently has a thinner exported contract. Inspect the live service and ingress names before wiring clients to it:

```bash
cd pulumi/data/databases/cockroach
NS="$(pulumi config get namespace)"

kubectl get pods,svc,statefulsets,pvc,ingress -n "$NS"
kubectl get endpoints -n "$NS" cockroachdb-public
```

ConvexDB exports its client and dashboard URLs, plus operational identifiers:

```bash
cd pulumi/data/databases/convexdb

pulumi stack output --stack mx api_url
pulumi stack output --stack mx dashboard_url
pulumi stack output --stack mx backend_service
pulumi stack output --stack mx dashboard_service
pulumi stack output --stack mx pvc
pulumi stack output --stack mx postgres_db_name
```

Use the API URL for clients. Use the dashboard URL for inspection. Do not send clients directly to ConvexDB's PostgreSQL database unless you are deliberately debugging backing state.

## Credentials

Database credentials in this repo move through three main paths:

- Pulumi stack outputs, including secret outputs from the PostgreSQL stack.
- Kubernetes Secrets written by the consumer stack.
- ESC imports or Pulumi config values that feed stack configuration.

Keep those paths distinct. A password in a Pulumi output is not automatically the same thing as the password in a consumer namespace Secret. A provider may successfully connect during preview while the application pod still has an old Secret, a wrong database name, or a missing CA file.

Only reveal secret outputs in a trusted terminal when the value is needed for a connection or rotation:

```bash
cd pulumi/data/databases/postgres

pulumi stack output --stack mx username
pulumi stack output --stack mx --show-secrets password
```

Do not paste secret values into this docs site, issues, chat, commit messages, PR bodies, or shell history. Prefer commands that keep values in process environment for the one command that needs them.

For app-specific databases, the durable pattern is to create the database, role, password, grants, and app Secret in the owning Pulumi project. That gives the consumer stack a complete record of what it needs and lets previews show credential rotations. Manual `psql` changes are appropriate for emergency diagnosis, but important roles and grants should be brought back into Pulumi afterward.

ConvexDB is a useful example of this pattern: it reads PostgreSQL admin connection material from the shared stack, creates a Convex-specific role and database, generates a role password, writes a `POSTGRES_URL` Secret in the `convexdb` namespace, and mounts CA material separately. The generated admin key command is also sensitive; run it locally only when a client or operator actually needs the key.

## Durable State

PostgreSQL state is owned by CloudNativePG and its persistent volumes. The app-level state inside PostgreSQL also includes roles, databases, grants, extensions, and migration history. A replacement that preserves a PVC may still be unsafe if it changes bootstrap behavior, extension support, major version, service identity, or credential semantics.

CockroachDB state is the StatefulSet plus its persistent storage. The current single-node mode means that storage health and restoreability matter even more: there is no second Cockroach node to make the data path resilient. Treat chart upgrades, storage size changes, TLS mode changes, and service identity changes as database migrations.

ConvexDB state is split. PostgreSQL holds backing database state, and the `convexdb-storage` PVC holds backend local data. A restore plan for Convex must account for both sides. Restoring only PostgreSQL or only the PVC can leave the backend in an inconsistent state even if each Kubernetes object looks healthy on its own.

When debugging, inventory state before editing code:

```bash
kubectl get pods,svc,pvc,secrets,ingress -n postgresql
kubectl get pods,svc,pvc,secrets,ingress -n cockroachdb
kubectl get pods,svc,pvc,secrets,ingress -n convexdb
```

If a namespace or service name differs from the defaults, get it from stack config or outputs first instead of guessing.

## Backups And Restores

Do not use "pod is ready" as a backup signal. Readiness means the current process passed its probes. It does not mean the data can be restored, that the database contains the expected rows, or that the last migration can be rolled back.

Before a risky database change, identify the restore artifact and the command or operator flow that would use it. For PostgreSQL, that usually means deciding whether you are relying on a CNPG backup mechanism, a volume snapshot, a logical dump, or a freshly taken ad hoc dump. For CockroachDB, it means knowing whether the data matters enough to export or snapshot before a chart/storage change. For ConvexDB, it means planning for both the PostgreSQL database and the backend PVC.

The repo pages should not contain backup secrets or private object-store URLs. They should contain the operating expectation: what must be restorable, which stack owns the restore procedure, and which consumers must be paused or revalidated.

For small checks, a logical smoke test is often more useful than a large dump:

```sql
select current_database(), current_user;
select version();
```

For real migrations, take or verify a backup first, then write down the rollback boundary. "Revert the commit" is not a database rollback if the change already modified persistent state.

## Consumers And Failure Diagnosis

When a database-backed service fails, split the problem into layers:

```text
database process       pod readiness, logs, operator status
durable storage        PVC bound state, volume events, restore status
network path           Service, endpoints, ingress, Tailscale path, DNS
credentials            role, password, Secret key names, CA material
database/schema        database exists, grants, extensions, migrations
consumer config        host choice, database name, env vars, chart values
```

Start at the producer contract, then move to the consumer. For PostgreSQL, inspect the CNPG cluster and read-write service before editing an app. For ConvexDB, inspect the backend before the dashboard because the dashboard can be up while backend API calls fail. For CockroachDB, separate the SQL service from the admin UI ingress.

Useful inspection commands:

```bash
kubectl get events -n <namespace> --sort-by=.lastTimestamp
kubectl describe svc -n <namespace> <service>
kubectl get endpoints -n <namespace> <service>
kubectl logs -n <namespace> <pod-or-workload> --tail=200
```

If a consumer suddenly cannot connect after a database change, check for contract drift before assuming the consumer regressed. The common drift points are output names, service hostnames, Secret key names, password rotation, database names, missing extensions, and TLS/CA behavior.

## Safe Changes

Use the narrowest stack command that proves the change. Do not apply from this docs guide; preview first and understand replacements before any apply decision.

For a database stack change:

```bash
just sync pulumi/data/databases/<stack>
just check-python
just lint
git diff --check
just preview pulumi/data/databases/<stack> stack=mx
```

Then preview affected consumers when the change touches an output, service name, database name, role, password, Secret shape, CA material, extension, image, chart version, storage setting, ingress hostname, or anything that can change migration behavior.

For shared PostgreSQL changes, assume downstream previews are part of the work. At minimum, search for consumers and preview the ones whose contract changed. For ConvexDB changes, preview PostgreSQL-related behavior and the Convex stack together when database name, user, CA, service host, or image versions move. For CockroachDB changes, read the Helm chart diff carefully because StatefulSet and storage fields can turn a version bump into a replacement.

Some changes need migration notes before code moves:

- Resource renames need Pulumi aliases or an explicit state migration plan.
- Storage class and PVC changes need a data movement or recreate decision.
- Major database version changes need extension and client compatibility checks.
- Credential rotations need a plan for updating consumer Secrets and restarting or reconciling consumers.
- Service hostname changes need both in-cluster and Tailscale-path verification.
- Schema migrations need a rollback boundary that is not just a Git revert.

When in doubt, make the contract visible before changing it:

```bash
pulumi stack output --stack mx
kubectl get pods,svc,pvc,secrets,ingress -n <namespace>
rg -n '<output-or-service-name>' pulumi docs/stacks -g '!pulumi/lib/**'
```

The database pages under this directory hold the service-specific details. This index is the operating rule: protect state first, preserve contracts deliberately, keep credentials secret, prove restoreability for risky changes, and preview the consumers that depend on the database layer.
