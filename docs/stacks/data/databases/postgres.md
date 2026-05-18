# PostgreSQL

Source: `pulumi/data/databases/postgres`

PostgreSQL is the shared relational database platform for this repo. When an
app needs durable structured state, this is usually the first database surface
to consider: users, jobs, workflow runs, auth records, model metadata,
application settings, event indexes, catalog tables, and other data where
correctness and transactions matter more than raw scan throughput.

The important thing to understand is that this stack is a platform contract,
not just a pod that listens on port `5432`. It defines the namespace, the
CloudNativePG cluster name, the read-write service name, the private Tailscale
path used by Pulumi providers, the source superuser secret, the CA material,
and the stack outputs other projects read with `StackReference`.

If one of those exported names changes, the rest of the repo can break even
when the database pod is healthy. If a password changes, a consumer Secret can
be stale even when a Pulumi provider still connects. If a database exists but
the needed extension, role, grant, or schema migration is missing, the app can
fail while Kubernetes reports every object as ready. Operate PostgreSQL from
that first-principles model:

```text
cluster process + storage + service contract + credentials + databases + roles + extensions + consumers
```

## What This Stack Owns

The PostgreSQL project is a Python 3.12 Pulumi project using `uv`,
`pulumi-kubernetes`, `pulumi-postgresql`, and the repo-local monitoring CRD
package. It creates a namespace, deploys the CloudNativePG cluster chart,
exports connection material, and optionally creates shared application
databases with a standard extension set.

Current defaults from `pulumi/data/databases/postgres/__main__.py` and
`Pulumi.mx.yaml`:

```text
Pulumi project name:       postgresql
Default stack:             mx
Namespace:                 postgresql
CNPG cluster name:         postgresql-cluster
PostgreSQL major version:  18
Cluster chart:             cluster
Cluster chart version:     0.6.1
PostgreSQL image:          tensorchord/cloudnative-vectorchord:18.3-1.1.1
Read-write service:        postgresql-cluster-rw
Tailscale service:         postgresql-cluster-rw-ext
Default Tailscale host:    postgresql
CA Secret:                 postgresql-cluster-ca
Superuser Secret:          postgresql-cluster-superuser
Current app_databases:     immich, n8n
Current extensions:        vector, cube, earthdistance, vchord
Monitoring label:          kube-prometheus-stack
```

The stack disables the default CloudNativePG read-only services, keeps the
read-write service as the in-cluster runtime endpoint, and adds a separate
`ClusterIP` service annotated for Tailscale exposure. That gives the repo two
different connection paths:

```text
application pods inside Kubernetes -> postgresql-cluster-rw.postgresql.svc.cluster.local
Pulumi providers and local psql    -> Tailscale hostname exported as ts_hostname
```

Keep those paths separate. In-cluster clients should usually use Kubernetes
DNS. Pulumi PostgreSQL providers run from the machine executing `pulumi
preview` or `pulumi up`, so they need a host reachable from that machine.

## CloudNativePG Relationship

CloudNativePG is the PostgreSQL operator. The operator itself lives in
`pulumi/core/operators/cnpg`; that project installs the upstream
`cloudnative-pg` chart and its monitoring resources. This PostgreSQL stack is
the database cluster that the operator reconciles.

The PostgreSQL stack deploys the CloudNativePG `cluster` chart. That chart
renders a `postgresql.cnpg.io/v1` `Cluster` resource, Services, Secrets, and
the database pods owned by CloudNativePG. The repo code also adds a Pulumi
`waitFor` annotation to the `Cluster` resource because generic Kubernetes
awaiting does not fully capture CNPG readiness. A Pulumi preview can tell you
what resource graph will change; CNPG status tells you whether the operator
has actually reconciled the database into a healthy state.

Useful mental split:

```text
pulumi/core/operators/cnpg        installs the operator and CRDs
pulumi/data/databases/postgres    creates the PostgreSQL cluster instance
consumer stacks                   create app roles/databases/secrets or read outputs
```

If the cluster object exists but does not become healthy, inspect CNPG before
rewriting application stacks. The failure may be image pull, storage, operator
reconciliation, bootstrap SQL, or a chart value issue.

## Stack Outputs Are The API

The outputs from `kzh/postgresql/mx` are consumed throughout the repo. Treat
their names, types, and secrecy as an API.

Plain service and identity outputs:

```text
k8s_namespace
cnpg_cluster_name
monitoring_release_label
rw_service_name
rw_service_fqdn
ts_hostname
ca_secret_name
```

Secret-derived connection outputs from the CNPG superuser Secret:

```text
dbname
jdbc_uri
port
uri
user
host
pgpass
username
password
ca_cert_pem
```

Some of those values, such as `port`, are not inherently sensitive, but the
code exports the fields through the same secret-preserving path because they
come from Kubernetes Secrets. Do not "simplify" that away casually. If a value
comes from a Secret, keeping the Pulumi graph secret is the safer default.

Read the contract without revealing secret values:

```bash
cd pulumi/data/databases/postgres

pulumi stack output --stack mx k8s_namespace
pulumi stack output --stack mx cnpg_cluster_name
pulumi stack output --stack mx rw_service_name
pulumi stack output --stack mx rw_service_fqdn
pulumi stack output --stack mx ts_hostname
pulumi stack output --stack mx ca_secret_name
```

Only reveal secret outputs when you actually need them for a local connection
or a credential rotation:

```bash
cd pulumi/data/databases/postgres

pulumi stack output --stack mx --show-secrets username
pulumi stack output --stack mx --show-secrets password
```

Do not paste decoded values into this docs site, issues, commit messages, PR
text, screenshots, chat, or shared logs.

## Databases, Roles, And Grants

There are two patterns in the repo.

The first pattern is shared setup in this PostgreSQL stack. The
`app_databases` config creates named databases and applies the configured
extensions inside each one. In the current `mx` stack file, that list is
`immich` and `n8n`. This is useful for simple shared platform setup, especially
where a consumer currently expects the shared superuser output.

The second pattern is app-owned database provisioning in the consumer stack.
That stack reads the PostgreSQL admin connection outputs, creates its own role,
generates its own password, creates a database owned by that role, writes a
Kubernetes Secret in the app namespace, and passes only the app-specific
credentials to the runtime. Airflow, Dagster, LiteLLM, MLflow, ConvexDB, and
parts of Trino follow this pattern.

Prefer the second pattern for new services. It has better ownership:

```text
shared PostgreSQL stack     owns the cluster and admin contract
consumer stack              owns its database, role, password, grants, and Secret
consumer application        owns its schema migrations
```

That separation matters during rotation and incident response. Rotating an
app-owned password should only affect one app. Rotating the CNPG superuser
password affects every stack that still reads the shared `username` and
`password` outputs directly.

The PostgreSQL stack's extension loop applies only to databases listed in
`app_databases`. It does not magically install extensions in every database
created by consumer stacks. If a consumer-owned database needs `vector`,
`vchord`, or another extension, add that requirement deliberately in the
owning stack or app migration path.

Extension order matters here. The code preserves config order, removes
duplicates, and when both `vector` and `vchord` are present it creates
`vchord` immediately after `vector`. Keep that dependency in mind when adding
extensions with bootstrap, superuser, schema, or library requirements.

## Adding A New Consumer

Start by deciding whether the new service only needs a database or whether it
needs a platform feature. A service database, role, password, grant, and
Kubernetes Secret usually belong in the service's own Pulumi project. A
cluster-level capability, such as the PostgreSQL image, service exposure,
monitoring label, or a shared extension policy, belongs in this PostgreSQL
project.

The common consumer shape looks like this:

```python
import pulumi_kubernetes as k8s
import pulumi_postgresql as pg
import pulumi_random as random

import pulumi

config = pulumi.Config()

postgres_stack_ref = config.get("postgresStack") or "kzh/postgresql/mx"
postgres_stack = pulumi.StackReference(postgres_stack_ref)

postgres_service_host = postgres_stack.require_output("rw_service_fqdn")
postgres_provider_host = postgres_stack.require_output("ts_hostname")

database_password = random.RandomPassword(
    "example-database-password",
    length=32,
    special=False,
)

admin_provider = pg.Provider(
    "example-pg-admin",
    host=postgres_provider_host,
    port=5432,
    username=postgres_stack.require_output("username"),
    password=postgres_stack.require_output("password"),
    database="postgres",
    sslmode="disable",
)

role = pg.Role(
    "example-role",
    name="example",
    login=True,
    password=database_password.result,
    opts=pulumi.ResourceOptions(provider=admin_provider),
)

database = pg.Database(
    "example-database",
    name="example",
    owner=role.name,
    opts=pulumi.ResourceOptions(provider=admin_provider, depends_on=[role]),
)

db_secret = k8s.core.v1.Secret(
    "example-db-credentials",
    string_data={
        "username": role.name,
        "password": database_password.result,
    },
    type="Opaque",
    opts=pulumi.ResourceOptions(depends_on=[database]),
)
```

That example intentionally uses two different hosts. The provider host is the
Tailscale name because the Pulumi PostgreSQL provider connects from the
operator machine. The runtime host for the app should be
`postgres_service_host`, which is the in-cluster read-write service. Keeping
those separate prevents a local-network fix from accidentally becoming a
runtime dependency inside Kubernetes.

If the app wants a single connection URL instead of separate fields, build it
from Pulumi outputs and store the result as a secret. URL-encode usernames,
passwords, and database names if the password policy allows special
characters. Several current stacks set `special=False` on generated database
passwords, but that is a local implementation choice, not a rule PostgreSQL
itself guarantees.

For a new consumer, preview both sides when needed:

```bash
just preview pulumi/data/databases/postgres stack=mx
just preview pulumi/<area>/<service> stack=mx
```

The PostgreSQL preview is only needed when the shared platform changes. A
normal app-owned database and Secret change should preview in the consumer
stack.

## Current Consumers

Before changing an output, service name, port, credential, CA behavior, or
database default, search for consumers:

```bash
rg -n 'kzh/postgresql/mx|rw_service_fqdn|require_output\("(username|password|host|port|ts_hostname|ca_)' pulumi -g '!pulumi/lib/**'
```

At the time this page was written, the main PostgreSQL consumers are:

```text
pulumi/apps/coder
  Reads the PostgreSQL stack, optionally creates the coder database, builds
  CODER_PG_CONNECTION_URL, and stores it in the coder-db-url Secret.

pulumi/apps/litellm
  Creates a litellm role and database, stores app credentials in
  litellm-db-credentials, and points the LiteLLM chart at the in-cluster
  read-write service.

pulumi/apps/immich
  Reads PostgreSQL outputs for runtime connection settings. The shared
  postgres stack currently creates the immich database and shared extensions.

pulumi/apps/stitch
  Uses the PostgreSQL provider and config-derived PostgreSQL host/namespace
  values rather than the newer StackReference shape. Treat it as a legacy
  consumer when auditing database changes.

pulumi/data/analytics/mlflow
  Creates an mlflow role and database, stores credentials in a Kubernetes
  Secret, and uses PostgreSQL for the MLflow backend store.

pulumi/data/analytics/trino
  Creates a trino_reader role with grants across configured PostgreSQL
  databases, creates a trino_iceberg database and role, and writes catalog
  credentials for Trino.

pulumi/data/databases/convexdb
  Creates a Convex-specific PostgreSQL role and database, writes POSTGRES_URL
  into a Secret, and manages CA material for the backend.

pulumi/data/workflow/airflow
  Creates an airflow role and database, then passes metadata database
  connection settings into the Airflow chart.

pulumi/data/workflow/dagster
  Creates a dagster role and database, writes the chart's PostgreSQL password
  Secret, and configures Dagster to use the shared PostgreSQL service.

pulumi/data/workflow/n8n
  Uses the shared n8n database from app_databases and passes PostgreSQL
  connection values into the n8n Deployment.

pulumi/data/workflow/temporal
  Uses shared PostgreSQL outputs in the Temporal chart. The chart is currently
  configured to create the temporal and temporal_visibility databases and
  manage their SQL schemas.
```

That list is not a replacement for `rg`. It is an operator map. The code is
the source of truth, and other workers may be adding new consumers.

## Connection Paths

For in-cluster runtime clients, start with the exported service FQDN:

```bash
cd pulumi/data/databases/postgres

pulumi stack output --stack mx rw_service_fqdn
```

That FQDN has the shape:

```text
<rw_service_name>.<namespace>.svc.cluster.local
```

For local `psql` or a Pulumi PostgreSQL provider, use the exported Tailscale
hostname. Keep the secret in process environment for the command that needs it:

```bash
cd pulumi/data/databases/postgres

export PGHOST="$(pulumi stack output --stack mx ts_hostname)"
export PGPORT="$(pulumi stack output --stack mx --show-secrets port)"
export PGUSER="$(pulumi stack output --stack mx --show-secrets username)"
export PGPASSWORD="$(pulumi stack output --stack mx --show-secrets password)"

psql --dbname postgres --command 'select current_database(), current_user, version();'
```

Unset the password when you are done:

```bash
unset PGPASSWORD
```

If local `psql` cannot connect but in-cluster apps are healthy, investigate
the Tailscale service path, local network path, and provider host choice. If
apps cannot connect but local `psql` works, investigate Kubernetes DNS, the
read-write Service endpoints, the app Secret, the database name, and grants.

## Backups And Restore Boundaries

This stack currently does not define a repo-owned CNPG backup object,
`ScheduledBackup`, Barman object-store configuration, or restore workflow in
`pulumi/data/databases/postgres`. That means a ready PostgreSQL pod is not
evidence that a recent restore point exists.

Before a risky database change, decide what restore artifact you are relying
on. For small app-level changes, that may be a fresh logical dump of the
affected database. For platform-level changes, it may need to be a
CloudNativePG backup mechanism, a volume snapshot, or a deliberately added
backup configuration in the stack. Do not assume "revert the commit" is enough
after a migration has modified persistent state.

Example logical backup shape for one database:

```bash
cd pulumi/data/databases/postgres

export PGHOST="$(pulumi stack output --stack mx ts_hostname)"
export PGPORT="$(pulumi stack output --stack mx --show-secrets port)"
export PGUSER="$(pulumi stack output --stack mx --show-secrets username)"
export PGPASSWORD="$(pulumi stack output --stack mx --show-secrets password)"

pg_dump --format=custom --dbname app_database --file "/tmp/app_database.$(date +%Y%m%d%H%M%S).dump"

unset PGPASSWORD
```

That example is not a full backup policy. It is a safe local shape for an
operator-held dump when you are about to make a narrow change. A real backup
policy should be owned in code, include restore testing, and avoid storing
private object-store URLs or credentials in docs.

For restores, write down the boundary before changing anything:

```text
which database or cluster is being restored?
which apps need to be paused?
which schemas/migrations ran after the restore point?
which Secrets or generated passwords must match the restored data?
which consumer stacks must be previewed or re-applied afterward?
```

## Debug The Producer First

When a PostgreSQL-backed app fails, start with the producer contract. It is
easy to lose time inside an app chart when the actual issue is that the
read-write service has no endpoints, the CNPG cluster is not healthy, or the
consumer Secret points at a database that no longer exists.

Basic inventory:

```bash
cd pulumi/data/databases/postgres

NS="$(pulumi stack output --stack mx k8s_namespace)"
CLUSTER="$(pulumi stack output --stack mx cnpg_cluster_name)"
RW_SERVICE="$(pulumi stack output --stack mx rw_service_name)"

kubectl get clusters.postgresql.cnpg.io -n "$NS"
kubectl describe clusters.postgresql.cnpg.io -n "$NS" "$CLUSTER"
kubectl get pods,svc,pvc,secrets -n "$NS"
kubectl get endpoints -n "$NS" "$RW_SERVICE"
```

Read the status as an operator-owned database object, not just as a pod list:

```bash
kubectl get clusters.postgresql.cnpg.io -n "$NS" "$CLUSTER" -o yaml
kubectl get pods -n "$NS" -l "cnpg.io/cluster=$CLUSTER" -o wide
kubectl logs -n "$NS" -l "cnpg.io/cluster=$CLUSTER" --tail=200
```

The useful questions are practical:

```text
did CNPG accept the Cluster spec?
did bootstrap SQL finish?
is there a primary pod?
is the read-write Service selecting that primary?
are PVCs bound and mounted by the expected pod?
did the Tailscale-exposed Service get reconciled?
```

Look at recent namespace events when readiness is confusing:

```bash
kubectl get events -n "$NS" --sort-by=.lastTimestamp
```

Check the service paths separately:

```bash
kubectl get svc -n "$NS" postgresql-cluster-rw postgresql-cluster-rw-ext
kubectl describe svc -n "$NS" postgresql-cluster-rw
kubectl describe svc -n "$NS" postgresql-cluster-rw-ext
```

Then test the database itself:

```bash
export PGHOST="$(pulumi stack output --stack mx ts_hostname)"
export PGPORT="$(pulumi stack output --stack mx --show-secrets port)"
export PGUSER="$(pulumi stack output --stack mx --show-secrets username)"
export PGPASSWORD="$(pulumi stack output --stack mx --show-secrets password)"

psql --dbname postgres --command 'select current_database(), current_user;'
psql --dbname postgres --command '\l'

unset PGPASSWORD
```

Do not decode or paste Kubernetes Secret values unless the credential itself
is the thing you are intentionally verifying.

## Debug Consumers By Layer

After the producer looks healthy, split the consumer problem into layers:

```text
network path       runtime pod uses rw_service_fqdn, provider uses ts_hostname
credentials        correct role, password, Secret name, and Secret key
database           database exists and is owned or accessible by the role
extensions         required extensions exist in that specific database
schema             app migrations ran and match the app version
chart values       app receives the host, port, database, and Secret keys it expects
rollout            pods restarted after Secret or config changes
```

Common examples:

```text
Pulumi provider fails during preview
  Check local reachability to ts_hostname, the CNPG superuser output, and the
  provider's admin database setting.

App pod fails to connect
  Check the Kubernetes Secret in the app namespace, the env vars or chart
  values that reference it, and endpoints for rw_service_fqdn.

Database exists but app still fails
  Check role ownership, grants, extension availability, and app migrations.

Trino cannot read a new database
  Add the database to Trino's postgresDatabases config and think about grants
  for both existing and future tables.

Temporal startup changes database state
  Remember the Temporal chart is configured to create and manage its own SQL
  schema against the shared PostgreSQL service.
```

Useful consumer checks:

```bash
kubectl get pods,svc,secrets -n <app-namespace>
kubectl describe pod -n <app-namespace> <pod-name>
kubectl logs -n <app-namespace> <pod-name> --tail=200
kubectl get secret -n <app-namespace> <secret-name>
```

For a Secret check, confirming the Secret exists and has the expected keys is
often enough. Dumping decoded values should be a last step, done locally and
kept out of shared artifacts.

When a chart stores only the password in a Secret, also inspect the non-secret
chart values or environment variables that provide host, port, username, and
database name. A Secret can be correct while the pod still points at the wrong
database, or the chart can use the right host while reading a password key that
does not exist.

## Safe Schema Changes

Application schema belongs to the application. The PostgreSQL platform stack
should not become a pile of unrelated app DDL just because it has admin
credentials.

Use this rule:

```text
cluster/service/extension platform concern -> PostgreSQL stack
app role/database/password/grants          -> consumer stack
tables/indexes/migrations                  -> application migration path
```

For an app-owned migration:

1. Identify the owning stack and database.
2. Confirm there is a backup or restore point for the affected database.
3. Run or preview the migration through the app's supported mechanism.
4. Verify the app version and schema version together.
5. Keep the PostgreSQL stack unchanged unless the migration needs a platform
   feature such as a new extension.

For a shared extension change:

1. Confirm which databases need the extension.
2. Check whether the extension exists in the VectorChord image.
3. Check ordering requirements, especially around `vector` and `vchord`.
4. Add the extension to the owning code path.
5. Preview the PostgreSQL stack and any consumers that depend on the extension.

Do not run manual DDL in `psql` and leave it undocumented. Manual operations
can be useful during incident response, but durable state should move back into
Pulumi or the application's migration system.

## Safe Credential Changes

There are three credential categories:

```text
CNPG superuser credential      source output from the PostgreSQL stack
app-owned role credential      generated and stored by a consumer stack
external app/config secret     Pulumi config or ESC value consumed by a stack
```

For new apps, create an app-owned role and password in the consumer stack. Do
not pass the shared superuser password into a new workload unless there is a
very specific reason and a plan to remove it.

For app-owned password rotation:

1. Rotate the `random.RandomPassword` or secret source in the consumer stack.
2. Preview that consumer stack.
3. Confirm the Kubernetes Secret key names stay compatible with the chart or
   Deployment.
4. Apply during an app maintenance window.
5. Verify the app restarted or reloaded the Secret.

For CNPG superuser rotation, widen the blast-radius analysis. Consumers that
read `username` and `password` directly may need their Secrets rewritten and
pods restarted. Consumer stacks that only use the superuser for Pulumi
provider-time provisioning may need a preview/apply to refresh provider
connections, but their runtime apps may be unaffected if they use app-owned
roles.

Never convert a secret output to plaintext for convenience. Use
`config.require_secret`, provider-generated secret outputs, or
`pulumi.Output.secret(...)` to preserve secrecy through the graph.

## Safe Output Changes

Output changes are API changes. A consumer using
`postgres_stack.require_output("rw_service_fqdn")` fails if that output
disappears, even if the same string is available under a nicer name.

Use additive migrations:

1. Add the new output while keeping the old one.
2. Preview the PostgreSQL stack.
3. Search for consumers of the old output.
4. Migrate consumers one stack at a time.
5. Preview each affected consumer.
6. Remove the old output only after there are no references left.

For a meaning change, not just a rename, write the change like a migration. For
example, changing `rw_service_fqdn` from in-cluster DNS to a Tailscale hostname
would be a semantic break even though both are strings. Create a new output
instead, with a name that tells consumers which path it represents.

Keep output names boring and stable. The value behind an output may come from
a chart, a Secret, or an operator, but the output is what other stacks depend
on.

## Safe Service And Storage Changes

Service names, selectors, and PVC-backed storage are high-risk database
surfaces.

Changing `cnpg_cluster_name` changes derived names such as:

```text
postgresql-cluster-rw
postgresql-cluster-rw-ext
postgresql-cluster-ca
postgresql-cluster-superuser
```

That is not cosmetic. Consumers, outputs, Secrets, Tailscale exposure, and
debug commands all assume those names. If a rename is unavoidable, use a
planned migration with aliases or a compatibility period where old outputs
remain available.

Changing the PostgreSQL image, major version, bootstrap SQL, storage class, or
cluster instance count should be treated as a database migration. Read the CNPG
and image release notes, inspect the preview for replacements, identify a
backup, and verify consumers after the cluster is healthy.

The current stack runs one PostgreSQL instance. That is fine for a homelab
platform, but it changes the operational story: a single ready pod can still
be a single point of failure, and a storage problem is a platform incident.

## Validation Commands

For a docs-only edit to this page, do not apply infrastructure. For code or
config changes to the PostgreSQL stack, start with the cheap repo checks:

```bash
just sync pulumi/data/databases/postgres
just check-python
just lint
git diff --check
```

Then preview the PostgreSQL stack:

```bash
just preview pulumi/data/databases/postgres stack=mx
```

If the change affects an exported output, service name, credential, CA
material, extension, app database, or default connection path, also preview the
known consumers that rely on that contract. Do not use a green PostgreSQL
preview as proof that Coder, LiteLLM, Immich, MLflow, Trino, Airflow, Dagster,
n8n, Temporal, ConvexDB, or Stitch still works.

Do not run `pulumi up`, `pulumi destroy`, or `just up` unless the user has
explicitly asked for an apply or destructive operation.

## Practical Change Checklist

Before changing PostgreSQL behavior, answer these questions:

```text
What exact contract is changing: output, service, credential, DB, role, grant, extension, schema, storage, or image?
Which consumers read that contract today?
Is there a backup or restore point for the persistent state being touched?
Will the change require app pods to restart or rerun migrations?
Does the Pulumi provider path still work from the operator machine?
Does the runtime path still work from inside Kubernetes?
Are any secret values being exposed in docs, logs, shell history, or PR text?
```

That is the operating standard for this repo: keep the shared platform stable,
make app ownership explicit, preserve stack outputs deliberately, and verify
the path that will actually be used by the consumer.
