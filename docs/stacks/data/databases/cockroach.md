# CockroachDB

Source: `pulumi/data/databases/cockroach`

CockroachDB is a distributed SQL database. The useful way to approach it is to
separate the SQL surface from the storage architecture underneath it.

At the top, CockroachDB speaks a PostgreSQL-compatible wire protocol and offers
familiar SQL primitives: databases, tables, indexes, transactions, users, and
grants. That makes it approachable for applications that already know how to
talk to a relational database.

Underneath that surface, CockroachDB is not PostgreSQL. Cockroach stores data in
ranges, moves those ranges around a cluster, and uses consensus between replicas
to keep committed data correct when nodes come and go. The design goal is not
"PostgreSQL, but with a different logo." The design goal is serializable SQL on
top of a distributed key-value storage layer.

That distinction matters in this repo because the current Pulumi stack is a
single-node CockroachDB deployment. It is useful for Cockroach-specific SQL
behavior, client compatibility testing, and experiments that may later grow into
a Cockroach-shaped service. It is not currently proof that a workload can
survive CockroachDB node failures, range rebalancing, quorum loss, geographic
placement, or a rolling multi-node upgrade.

## Use CockroachDB Deliberately

Use the shared [PostgreSQL](/stacks/data/databases/postgres) stack first for
ordinary application metadata in this repo. PostgreSQL is the default relational
platform here. Other stacks already consume its outputs, create their app
databases and roles around it, and rely on its CloudNativePG operating model.

Reach for this CockroachDB stack when the workload specifically needs one of
these things:

- CockroachDB compatibility testing.
- A SQL service that should eventually follow CockroachDB's distributed model.
- Experiments with CockroachDB transaction behavior, SQL dialect differences,
  or PostgreSQL-wire clients against Cockroach.
- A place to inspect CockroachDB's admin UI and basic runtime behavior without
  claiming multi-node resilience.

Prefer PostgreSQL when the app needs the repo's existing shared database
contract, PostgreSQL extensions, exact PostgreSQL behavior, or the simplest
stateful backing store for normal app metadata. A service that merely needs
tables, migrations, and a durable row store is usually better served by
PostgreSQL in this checkout.

The practical rule is: use CockroachDB because you mean CockroachDB. Do not add
it just because "distributed SQL" sounds more capable on paper. In the current
repo shape, choosing CockroachDB adds a separate operational model without
adding multi-node durability.

## What This Stack Actually Wires

The Pulumi project is small and explicit. `Pulumi.yaml` defines a Python project
named `cockroachdb` that uses the `uv` toolchain. The program in `__main__.py`
creates three top-level resources:

- A Kubernetes `Namespace` named from Pulumi config.
- A Helm v4 `Chart` release named `cockroachdb`.
- A Kubernetes `Ingress` named `cockroachdb` for the admin UI.

The project expects a config key named `namespace` in the CockroachDB stack
configuration. Because the Pulumi project name is `cockroachdb`, that appears in
stack config as `cockroachdb:namespace`. Read it from the project directory
rather than assuming it:

```bash
cd pulumi/data/databases/cockroach
pulumi config get namespace --stack mx
```

The Helm chart is the CockroachDB chart from the official CockroachDB chart
repository:

```text
Chart repository: https://charts.cockroachdb.com
Chart name:       cockroachdb
Chart version:    20.0.5
Image:            cockroachdb/cockroach:v26.1.4
Pulumi release:   cockroachdb
```

The chart values set by this repo are intentionally narrow:

```text
conf.single-node:                 true
conf.max-sql-memory:              6G
conf.cache:                       6G
statefulset.replicas:             1
tls.enabled:                      false
storage.persistentVolume.size:    100Gi
service.public tailscale expose:  true
service.public tailscale host:    cockroachdb-public
```

The admin UI ingress is also explicit:

```text
Ingress name:       cockroachdb
Ingress class:      tailscale
Path:               /
Path type:          Prefix
Backend service:    cockroachdb-public
Backend port:       8080
TLS host entry:     cockroach
```

There are a few important absences:

- The program does not call `pulumi.export(...)`, so this stack currently does
  not publish a host, port, password, database name, or UI URL as a Pulumi output
  contract for other projects.
- The program does not create app-specific databases, roles, grants, or
  Kubernetes Secrets for consumers.
- The program does not configure a CockroachDB backup schedule, backup target,
  restore job, or object storage destination.
- The program does not configure CockroachDB TLS certificates. It sets
  `tls.enabled` to `false`.
- The program sets CockroachDB cache and SQL memory settings, but it does not
  define custom Kubernetes CPU or memory requests and limits in this file. Check
  the rendered chart or live pod before making resource-capacity claims.

That means the current contract is mostly Kubernetes identity and network path:
the namespace, the Helm release, the StatefulSet and PVCs rendered by the chart,
the `cockroachdb-public` Service, the Tailscale service exposure annotation, and
the admin UI ingress.

## Mental Model: Distributed SQL, Single Node Here

CockroachDB's distributed architecture exists to let a logical SQL database keep
working across multiple storage nodes. In a real multi-node cluster, the rows in
a table are split into ranges. Each range has replicas. One replica is the
leaseholder for serving many operations on that range. Writes are committed with
consensus, so CockroachDB can survive some node failures when enough replicas
remain available.

That is the reason people pick CockroachDB for workloads that need horizontal
growth, strong consistency, and node-level fault tolerance without moving away
from SQL.

This repo's stack collapses that architecture to one Kubernetes replica:

```text
Cockroach SQL surface:       yes
Cockroach storage engine:    yes
Kubernetes StatefulSet:      yes
Persistent volume:           yes
Multiple Cockroach nodes:    no
Multi-node quorum behavior:  no
Node-failure tolerance:      no
Geo-distribution testing:    no
TLS identity model:          no
```

A single-node CockroachDB deployment can still be useful. It can reveal whether
a client library connects correctly, whether a migration uses SQL CockroachDB
accepts, whether an application accidentally relies on PostgreSQL-specific
extensions, and whether the admin UI exposes the runtime information you expect.

It cannot validate the operational reasons CockroachDB is famous. If the pod is
unavailable, the SQL service is unavailable. If the underlying volume is lost and
there is no backup, the data is lost. If a future change moves this to multiple
replicas, that is a real database migration, not a tiny scaling tweak.

## Network And Service Wiring

The chart's public Service is the center of the current access story. The repo
annotates that public Service for Tailscale exposure:

```text
tailscale.com/expose:    true
tailscale.com/hostname:  cockroachdb-public
```

That exposure is intended for private tailnet access to the service. The same
Service is also the backend for the admin UI ingress on port `8080`.

Think of the paths separately:

```text
SQL path:
client -> Tailscale-exposed cockroachdb-public Service -> Cockroach SQL port

Admin UI path:
browser -> Tailscale Ingress named cockroachdb -> cockroachdb-public:8080

In-cluster path:
pod -> cockroachdb-public.<namespace>.svc -> CockroachDB
```

The source code only spells out the admin UI backend port, `8080`. For SQL, use
the live Service as the source of truth before wiring a client:

```bash
cd pulumi/data/databases/cockroach
NS="$(pulumi config get namespace --stack mx)"

kubectl get svc -n "$NS" cockroachdb-public -o wide
kubectl describe svc -n "$NS" cockroachdb-public
kubectl get endpoints -n "$NS" cockroachdb-public
kubectl get endpointslices.discovery.k8s.io \
  -n "$NS" \
  -l kubernetes.io/service-name=cockroachdb-public
```

If the Service has no endpoints, do not start by editing Tailscale or the
Ingress. A Service with no endpoints usually means the selected pod is not Ready
or the Service selector does not match the pod labels. Fix the backend before
the exposure layer.

## SQL Access

The repo disables CockroachDB TLS, so Cockroach CLI sessions should use
`--insecure`, and PostgreSQL-wire clients should use an SSL-disabled connection
mode. That is a statement about this stack's current Pulumi values, not a
general recommendation for production CockroachDB.

For a local operator session, port-forward the public Service:

```bash
cd pulumi/data/databases/cockroach
NS="$(pulumi config get namespace --stack mx)"

kubectl -n "$NS" port-forward svc/cockroachdb-public 26257:26257 8080:8080
```

Then connect with the Cockroach CLI from another terminal:

```bash
cockroach sql --insecure --host=localhost:26257
```

If you are using a PostgreSQL-wire client such as `psql`, make the disabled TLS
mode explicit:

```bash
psql "postgresql://root@localhost:26257/defaultdb?sslmode=disable"
```

For an in-cluster throwaway client, use the same Cockroach image as the server:

```bash
cd pulumi/data/databases/cockroach
NS="$(pulumi config get namespace --stack mx)"

kubectl run crdb-client \
  -n "$NS" \
  --rm \
  -it \
  --restart=Never \
  --image=cockroachdb/cockroach:v26.1.4 \
  -- sql --insecure --host=cockroachdb-public:26257
```

Those commands are for inspection and smoke testing. They are not a consumer
contract. If an application is going to depend on CockroachDB, the repo should
grow an explicit contract for that application: database name, role, grants,
Secret shape, service host, TLS decision, and ownership boundaries.

## A Small SQL Smoke Test

Use a tiny table to prove that the SQL endpoint accepts writes and that data
survives at least across the immediate connection path:

```sql
select version();
select current_database(), current_user;

create database if not exists docs_lab;

create table if not exists docs_lab.public.connection_smoke (
  id int primary key,
  note string not null,
  updated_at timestamptz not null default now()
);

upsert into docs_lab.public.connection_smoke (id, note)
values (1, 'cockroachdb sql path is writable');

select id, note, updated_at
from docs_lab.public.connection_smoke
order by id;
```

This proves only a narrow path: client to SQL service to CockroachDB storage. It
does not prove backup coverage, multi-node resilience, a future app migration,
or compatibility with every PostgreSQL feature an application might use.

When testing an actual application, run the application's own migrations and
read/write paths. CockroachDB compatibility is workload-specific. A generic
`select version()` can pass while a real migration fails on an unsupported
extension, a lock assumption, a sequence behavior difference, or SQL syntax that
PostgreSQL accepts but CockroachDB does not.

## Admin UI

The admin UI is exposed through a Tailscale ingress:

```bash
cd pulumi/data/databases/cockroach
NS="$(pulumi config get namespace --stack mx)"

kubectl get ingress -n "$NS" cockroachdb
kubectl describe ingress -n "$NS" cockroachdb
```

The ingress sends `/` to `cockroachdb-public` on port `8080`. If the UI is not
reachable, keep the layers separate:

```text
Ingress exists?             kubectl get ingress -n "$NS" cockroachdb
Ingress class correct?      ingressClassName should be tailscale
Backend Service exists?     kubectl get svc -n "$NS" cockroachdb-public
Backend has endpoints?      kubectl get endpoints -n "$NS" cockroachdb-public
Cockroach pod Ready?        kubectl get pods -n "$NS" -o wide
```

If SQL works through port-forward but the UI does not work through the private
URL, the database process is probably fine and the problem is in the ingress or
Tailscale path. If neither SQL nor UI works, start with the pod, StatefulSet,
and PVC.

## Storage, Replicas, And Failure Boundaries

The repo sets `storage.persistentVolume.size` to `100Gi`. The chart renders the
actual PVCs and volume claim templates. Inspect the live objects before changing
storage:

```bash
cd pulumi/data/databases/cockroach
NS="$(pulumi config get namespace --stack mx)"

kubectl get statefulset,pods,pvc -n "$NS"
kubectl describe statefulset -n "$NS" cockroachdb
kubectl describe pvc -n "$NS"
```

There is one Kubernetes replica. That is the most important durability fact in
this page. CockroachDB's distributed storage model only becomes operationally
meaningful when there are multiple Cockroach nodes with enough healthy replicas
to satisfy quorum. This stack does not currently provide that.

For this deployment, the main state risk is the persistent volume attached to
the single CockroachDB pod. Kubernetes can reschedule a pod. It cannot recreate
lost database contents unless you have a backup or snapshot that can be restored.

Be especially careful with changes to:

- Chart version.
- CockroachDB image tag.
- Namespace.
- Helm release name.
- StatefulSet name or labels.
- `statefulset.replicas`.
- `conf.single-node`.
- `tls.enabled`.
- Service names and Tailscale hostnames.
- Persistent volume size, storage class, or volume claim templates.

Some of those changes can force replacement. Some can leave the Kubernetes
objects alive while changing how clients must connect. Some can be valid only as
part of a planned database migration. Preview the change, read the diff, and
decide whether the data needs a backup before any apply.

## Debugging From The Bottom Up

Start with the stack config, then Kubernetes identity, then CockroachDB itself.

```bash
cd pulumi/data/databases/cockroach
pulumi config get namespace --stack mx
```

Use that namespace for every command. Guessing the namespace creates noisy
failures:

```bash
NS="$(pulumi config get namespace --stack mx)"

kubectl get pods -n "$NS" -o wide
kubectl get statefulset -n "$NS"
kubectl get svc -n "$NS"
kubectl get pvc -n "$NS"
kubectl get ingress -n "$NS"
kubectl get events -n "$NS" --sort-by=.lastTimestamp
```

For pod and process failures:

```bash
kubectl describe statefulset -n "$NS" cockroachdb
kubectl describe pod -n "$NS" cockroachdb-0
kubectl logs -n "$NS" statefulset/cockroachdb --tail=200
```

For SQL service failures:

```bash
kubectl describe svc -n "$NS" cockroachdb-public
kubectl get endpoints -n "$NS" cockroachdb-public
kubectl get endpointslices.discovery.k8s.io \
  -n "$NS" \
  -l kubernetes.io/service-name=cockroachdb-public
```

For admin UI failures:

```bash
kubectl describe ingress -n "$NS" cockroachdb
kubectl describe svc -n "$NS" cockroachdb-public
```

For storage failures:

```bash
kubectl get pvc -n "$NS"
kubectl describe pvc -n "$NS"
kubectl get events -n "$NS" --sort-by=.lastTimestamp
```

Read failures by layer:

```text
No pod:                 chart, namespace, scheduling, image pull, or StatefulSet
Pod not Ready:           Cockroach process, probes, storage, resources, startup
Service has no endpoints: pod readiness or selector mismatch
SQL cannot connect:      service path, port, TLS mode, credentials, client config
UI cannot load:          ingress, Tailscale path, backend service, port 8080
PVC pending:             storage class, capacity, volume binding, node placement
Migration fails:         Cockroach/PostgreSQL compatibility, not just networking
```

When a client says "PostgreSQL connection failed," do not assume PostgreSQL is
involved. CockroachDB can use PostgreSQL-compatible clients, so error text may
come from the client library rather than the database product.

## Backups And Restore Thinking

This repo does not currently wire a CockroachDB backup system. There is no
Pulumi-managed backup schedule, no configured object-store destination, and no
restore automation in `pulumi/data/databases/cockroach`.

That does not mean backups are optional. It means the backup decision is outside
the current stack and must be made before the data matters.

For disposable experiments, it may be acceptable to recreate the database. For
anything important, decide how the data would come back before changing chart
versions, storage settings, TLS mode, replica shape, or service identity.

The restore plan should answer these questions:

- What data needs to be restorable: all databases, one database, or a small set
  of tables?
- What artifact exists: a CockroachDB backup, a logical SQL export, a volume
  snapshot, or nothing yet?
- Where is the artifact stored, and who can read it?
- Which secrets or storage credentials are required to restore it?
- Would restore happen into this same cluster, a replacement namespace, or a new
  CockroachDB deployment?
- What clients must be stopped, repointed, or revalidated after restore?

Keep backup destinations, access keys, and private URLs out of docs and chat.
Use placeholders in notes, and keep real values in the appropriate secret system
or local terminal session.

For a small manual export, a SQL-level dump may be enough. For larger or more
important data, use CockroachDB's own backup and restore capabilities or a
storage-level snapshot process that you have tested. A volume snapshot without a
tested restore is only an optimistic artifact.

The key operating rule is simple: a green pod is not a backup. A successful
Pulumi preview is not a backup. A Git revert is not a database restore.

## Adding A Consumer

Because this stack exports no Pulumi outputs today, wiring a real consumer
should be treated as new contract design, not a one-off connection string.

Before adding a consumer, decide:

- Which stack owns the CockroachDB database and role?
- Will the consumer connect through in-cluster DNS or through the Tailscale
  service hostname?
- What Service name, port, database name, username, and Secret key names will be
  considered stable?
- Does the app support CockroachDB, or only PostgreSQL?
- Does the app require PostgreSQL extensions that CockroachDB will not provide?
- Does the app require TLS, and if so, will this stack grow TLS support first?
- How will credentials rotate?
- How will backup and restore be tested before the data matters?

For experiments, a manual SQL session is fine. For a durable service, put the
contract into Pulumi. That usually means creating the database, role, grants,
and Kubernetes Secret in a project that clearly owns the consumer, or expanding
this CockroachDB project to export a deliberate platform contract. Do not hide
important connection material in a local note or a chart value copied from an
interactive session.

## Safe Changes

For docs-only edits, do not apply infrastructure. For code or config changes to
this stack, use the repo's normal gates and preview before any apply decision:

```bash
just sync pulumi/data/databases/cockroach
just check-python
just lint
git diff --check
just preview pulumi/data/databases/cockroach stack=mx
```

The `just preview` command runs `pulumi preview` for the project. It does not
apply changes. Do not run `pulumi up`, `pulumi destroy`, or `just up` unless the
user explicitly asks for an apply or destructive action.

When reading a preview, slow down around any replacement of:

- The Namespace.
- The Helm chart release.
- The StatefulSet.
- The PVC or volume claim template.
- The `cockroachdb-public` Service.
- The Tailscale annotations or hostnames.
- The admin UI Ingress.

Also slow down around changes that look harmless but alter database identity:

- Turning TLS on or off.
- Moving from single-node to multi-node.
- Changing the CockroachDB image tag across major versions.
- Changing the chart version.
- Changing the namespace config.
- Changing service ports or hostnames.
- Adding app credentials manually instead of declaring them in Pulumi.

If a change affects data, decide the backup and restore path first. If a change
affects clients, decide the service and Secret contract first. If a change
affects the chart, inspect the rendered Kubernetes diff instead of assuming a
version bump is only a pod restart.

CockroachDB can be the right tool, but in this repo it should be handled as a
specific database product with a currently small, single-node deployment. Keep
that boundary clear and the stack stays useful.
