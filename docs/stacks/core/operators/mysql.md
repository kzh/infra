# MySQL Operator

Source: `pulumi/core/operators/mysql`

The MySQL Operator stack is the shared Kubernetes controller layer for
MySQL-backed applications in this repo. It does not create the MediaWiki
database. It does not create the WordPress database. It installs the controller,
CRDs, RBAC, and Helm-managed operator resources that make MySQL custom resources
work anywhere in the cluster.

That boundary is the most important thing to understand before touching it:

```text
operator stack -> MySQL CRDs and controller
app stack      -> namespace, Secrets, InnoDBCluster, Jobs, app pods, app data
operator loop  -> MySQL pods, routers, Services, status, repair work
```

Pulumi declares the operator Helm release. Application stacks declare
`InnoDBCluster` objects. The MySQL Operator watches those objects and reconciles
the lower-level database implementation. When something is broken, the right fix
depends on which layer owns the broken state.

## What This Stack Owns

The Pulumi program is intentionally small. It reads a few stack config values,
creates the `mysql-operator` namespace, and installs the upstream
`mysql-operator` Helm chart from `https://mysql.github.io/mysql-operator/`.

Current stack defaults are:

```text
Namespace:              mysql-operator
Helm release name:      mysql-operator
Helm chart:             mysql-operator
Chart repository:       https://mysql.github.io/mysql-operator/
Chart version:          2.2.8
Chart app version:      9.7.0-2.2.8
Kubernetes DNS domain:  cluster.local
Pulumi package:         pulumi-kubernetes 4.x
```

The stack passes only one Helm value today:

```text
envs.k8sClusterDomain = cluster.local
```

That value matters because the operator builds DNS names for Services and pods.
If the cluster DNS domain ever changes, the operator and the app connection
strings need to agree.

The Helm release uses `delete_before_replace=True`. That is a deliberate
Pulumi replacement behavior for the release itself, not permission to casually
reset databases. The application `InnoDBCluster` resources and their PVCs live
in the app stacks and app namespaces.

The stack exports:

```text
namespace
releaseName
chartVersion
k8sClusterDomain
status
```

Those outputs describe the operator installation. They are not app database
credentials.

## The Operator Model

An operator is a controller that extends Kubernetes with a higher-level API. In
this case the API group is `mysql.oracle.com`, and the important resource for
this repo is `InnoDBCluster`.

From first principles, there are three different objects in play:

```text
desired API object    InnoDBCluster.spec
observed API object   InnoDBCluster.status
implementation        pods, PVCs, Services, routers, endpoints
```

Pulumi writes the desired API object. Kubernetes stores it. The MySQL Operator
observes it and continuously tries to make the implementation match. The
operator also writes status back onto the custom resource so humans and tools
can see whether reconciliation worked.

A Pulumi preview can tell you what object spec will change. It cannot prove the
operator will later produce healthy MySQL pods. A Pulumi update can finish
before every router, instance, or app initialization Job is healthy. That is why
operator-backed stacks need both Pulumi validation and Kubernetes status checks.

The chart installed by this stack includes more than the single database CRD.
The generated CRD file currently includes:

```text
innodbclusters.mysql.oracle.com
mysqlbackups.mysql.oracle.com
mysqlclustersetfailovers.mysql.oracle.com
clusterkopfpeerings.zalando.org
kopfpeerings.zalando.org
```

The Kopf peering resources are part of the controller coordination machinery.
They are not app databases. The MySQL resources are the API surface app stacks
use when they want MySQL.

By default this chart is a global operator: it runs in `mysql-operator` and can
watch `InnoDBCluster` resources in application namespaces. The chart values make
operator topology sticky after install. Changing deployment identity,
standalone mode, or watched namespaces is not a harmless cleanup; treat it as an
operator migration.

## CRD Bindings

This repo does not hand-write raw YAML for MySQL custom resources. It generates
typed Pulumi Python bindings under:

```text
pulumi/lib/mysql_operator_crds
```

Application stacks import those bindings as:

```python
from pulumi_mysql_operator_crds.mysql.v2 import (
    InnoDBCluster,
    InnoDBClusterSpecArgs,
    InnoDBClusterSpecRouterArgs,
)
```

The generator lives beside this operator stack:

```text
pulumi/core/operators/mysql/scripts/generate_crds.sh
```

The script pulls the configured Helm chart version, writes the chart CRDs into
`pulumi/core/operators/mysql/crds/`, runs `crd2pulumi`, and normalizes the
generated Python package metadata. If the chart CRDs change, regenerate the
bindings. Do not edit generated files in `pulumi/lib/mysql_operator_crds` by
hand.

Use the repo recipes:

```bash
just generate-mysql-crds
just check-mysql-crds
```

For a specific chart version:

```bash
just generate-mysql-crds 2.2.8
just check-mysql-crds 2.2.8
```

After a regeneration, run the cheap repo checks and preview the operator plus
important consumers:

```bash
just check-python
just lint
git diff --check

just preview pulumi/core/operators/mysql mx
just preview pulumi/apps/mediawiki mx
just preview pulumi/apps/wordpress mx
```

A generated package can compile while a consuming app preview still reveals a
schema, defaulting, or behavior change. Check both.

## What An App-Owned MySQL Cluster Looks Like

MediaWiki and WordPress both follow the same database pattern:

```text
app namespace
root credential Secret
app database credential Secret
InnoDBCluster
database init Job
application Deployment
application Service and Ingress
```

The operator stack provides the controller and CRDs. The app stack owns the
database cluster because the data belongs to the app.

The current app stacks create `InnoDBCluster` resources with the same core
shape:

```text
secretName:                 app-owned root credential Secret
version:                    app-configured MySQL version, currently defaulting to 9.7.0
instances:                  app-configured MySQL instance count, currently defaulting to 1
router.instances:           app-configured router count, currently defaulting to 1
tlsUseSelfSigned:           true
datadirVolumeClaimTemplate: app-configured storage, currently defaulting to 20Gi
```

They also annotate the `InnoDBCluster` with:

```text
pulumi.com/skipAwait: "true"
```

That annotation keeps Pulumi from relying on Kubernetes await behavior for a
custom resource whose real readiness is reported by the operator over time. It
does not mean the cluster is ready. It means you must read `InnoDBCluster`
status and child resources when validating the live database.

Avoid renaming these without a migration plan:

```text
namespace
InnoDBCluster metadata.name
root credential Secret name
PVC template identity
database name and user
application Service host used by the app
```

Those names form the identity of durable data. A casual rename can create a new
cluster, orphan old PVCs, break app credentials, or point the app at an empty
database.

## Routers And Services

The MySQL Operator does not expect PHP apps to connect directly to a random
database pod. It creates an application-facing Service for the `InnoDBCluster`.
In this repo the app stacks build the connection host like this:

```text
<cluster-name>.<namespace>.svc.cluster.local
```

For the default app stacks that becomes:

```text
mediawiki-mysql.mediawiki.svc.cluster.local
wordpress-mysql.wordpress.svc.cluster.local
```

The CRD has two related service concepts:

```text
service          the application-facing Service for clients
instanceService  the internal Service for MySQL group members
```

The application-facing Service defaults to `ClusterIP`. Its default port `3306`
targets `mysql-rw`, meaning read-write application traffic goes to the writable
side of the cluster. The CRD also exposes `mysql-ro` and `mysql-rw-split` as
possible service targets, but the current app stacks do not customize that
field.

The router count is controlled separately:

```text
spec.router.instances
```

A cluster can have an `InnoDBCluster` object, a MySQL pod, and still not be
usable by an app if the router path or Service endpoints are missing. For app
connection failures, inspect Services and endpoints before editing PHP config.

Useful service checks:

```bash
NS=mediawiki
CLUSTER=mediawiki-mysql

kubectl get svc,endpoints,endpointslices -n "$NS" | rg "$CLUSTER|mysql|router"
kubectl describe svc -n "$NS" "$CLUSTER"
kubectl get pods -n "$NS" | rg "$CLUSTER|router|mysql"
```

MediaWiki also has one compatibility Job that talks directly to the first MySQL
instance instead of the application Service:

```text
mediawiki-mysql-0.mediawiki-mysql-instances.mediawiki.svc.cluster.local
```

That is intentional. It adjusts MediaWiki tables for MySQL Group Replication
compatibility after install/update. Most app traffic should still go through the
application-facing MySQL Service.

## MediaWiki

The MediaWiki stack lives at:

```text
pulumi/apps/mediawiki
```

It creates:

```text
namespace:                 mediawiki
InnoDBCluster:             mediawiki-mysql
MySQL application host:    mediawiki-mysql.mediawiki.svc.cluster.local
database init Job:         mediawiki-db-init
install Job:               mediawiki-install
update Job:                mediawiki-update
compatibility Job:         mediawiki-db-compat
generated config Secret:   mediawiki-local-settings
images PVC:                mediawiki-images
```

The database init Job waits for the MySQL Service on port `3306`, creates the
MediaWiki database and database user, updates the user's password, grants
privileges, and flushes privileges. It reads credentials from Kubernetes
Secrets. Do not paste those values into docs, tickets, or chat.

The generated `LocalSettings.php` points MediaWiki at the MySQL Service host,
not at an instance pod. The install Job uses `mysqli` to wait for a working
database connection and then runs MediaWiki's installer if the schema is not
already present. The update Job runs MediaWiki's maintenance update. The
compatibility Job then handles known table shape issues that matter for MySQL
Group Replication.

That sequence means MediaWiki database problems often show up as Job failures
before they show up as clean application errors. Read the Jobs first:

```bash
cd pulumi/apps/mediawiki
NS="$(pulumi stack output --stack mx namespace)"

kubectl get innodbcluster -n "$NS" mediawiki-mysql -o wide
kubectl get jobs -n "$NS"
kubectl logs -n "$NS" job/mediawiki-db-init --tail=100
kubectl logs -n "$NS" job/mediawiki-install --tail=100
kubectl logs -n "$NS" job/mediawiki-update --tail=100
kubectl logs -n "$NS" job/mediawiki-db-compat --tail=100
```

If `mediawiki-db-init` is waiting for MySQL, inspect the cluster and router
path. If `mediawiki-install` cannot connect, check the app database Secret names
and the MySQL Service host. If `mediawiki-db-compat` fails, treat it as a real
database compatibility signal rather than a disposable bootstrap detail.

## WordPress

The WordPress stack lives at:

```text
pulumi/apps/wordpress
```

It creates:

```text
namespace:                 wordpress
InnoDBCluster:             wordpress-mysql
MySQL application host:    wordpress-mysql.wordpress.svc.cluster.local
database init Job:         wordpress-db-init
WordPress PVC:             wordpress-data
Deployment:                wordpress
Service:                   wordpress
Ingress class:             tailscale
```

The database init Job uses the same pattern as MediaWiki: wait for MySQL on
port `3306`, create the application database, create or update the application
user, grant privileges, and flush privileges. The WordPress container receives
the database host, name, user, password, table prefix, and URL settings through
environment variables and Secrets.

WordPress has two durable state planes:

```text
MySQL                 posts, pages, users, settings, plugin state
wordpress-data PVC    uploads, themes, plugins, generated app files
```

The MySQL Operator only accounts for the first one. A WordPress backup or
restore plan that ignores the PVC is incomplete.

For database-related WordPress failures:

```bash
cd pulumi/apps/wordpress
NS="$(pulumi stack output --stack mx namespace)"

kubectl get innodbcluster -n "$NS" wordpress-mysql -o wide
kubectl get pods,svc,endpoints,pvc,jobs -n "$NS"
kubectl logs -n "$NS" job/wordpress-db-init --tail=100
kubectl logs -n "$NS" deploy/wordpress --tail=200
```

If WordPress reports a database connection error, start with
`wordpress-db-init`, `wordpress-mysql` status, and the MySQL Service endpoints.
If ingress returns 502, inspect the WordPress Service endpoints and pod
readiness separately from the database.

## Reading Status

For `InnoDBCluster`, status is the operator's report about the live cluster. It
is more useful than guessing from pod names.

Start broad:

```bash
kubectl get pods -n mysql-operator
kubectl logs -n mysql-operator deploy/mysql-operator --tail=200
kubectl api-resources --api-group=mysql.oracle.com
kubectl get innodbclusters.mysql.oracle.com --all-namespaces
```

The CRD defines these useful `kubectl get` columns:

```text
Status     .status.cluster.status
Online     .status.cluster.onlineInstances
Instances  .spec.instances
Routers    .spec.router.instances
Type       .status.cluster.type
Age        .metadata.creationTimestamp
```

For a specific cluster:

```bash
NS=mediawiki
CLUSTER=mediawiki-mysql

kubectl get innodbcluster -n "$NS" "$CLUSTER" -o wide
kubectl describe innodbcluster -n "$NS" "$CLUSTER"
kubectl get innodbcluster -n "$NS" "$CLUSTER" -o yaml
```

When reading YAML, compare desired and observed state:

```bash
kubectl get innodbcluster -n "$NS" "$CLUSTER" \
  -o jsonpath='{.metadata.generation}{" observed="}{.status.observedGeneration}{"\n"}'

kubectl get innodbcluster -n "$NS" "$CLUSTER" \
  -o jsonpath='{.status.cluster.status}{" online="}{.status.cluster.onlineInstances}{" type="}{.status.cluster.type}{"\n"}'
```

If `observedGeneration` is behind `metadata.generation`, the controller has not
reported on the newest spec yet. Give the operator a chance to reconcile, then
read logs and events if it stays behind.

Then inspect the implementation objects:

```bash
kubectl get pods,svc,endpoints,endpointslices,pvc,jobs -n "$NS" | rg "$CLUSTER|mysql|router|db-init|db-compat"
kubectl get events -n "$NS" --sort-by=.lastTimestamp | tail -50
```

Use logs for the component that owns the failing step:

```bash
kubectl logs -n mysql-operator deploy/mysql-operator --tail=200
kubectl logs -n "$NS" job/mediawiki-db-init --tail=100
kubectl logs -n "$NS" job/wordpress-db-init --tail=100
kubectl logs -n "$NS" deploy/mediawiki --tail=200
kubectl logs -n "$NS" deploy/wordpress --tail=200
```

Avoid dumping full Secrets or generated config files. For credential debugging,
check that the expected Secret exists, that the expected keys exist, and that
the pod references the right Secret name. Only reveal secret values locally when
there is a specific operational reason.

## Debugging Order

Use the ownership boundary to avoid chasing symptoms.

If the operator itself looks wrong:

```text
1. Is the mysql-operator namespace present?
2. Is the Helm release present in that namespace?
3. Is the operator Deployment available?
4. Are the MySQL CRDs installed?
5. Do operator logs show chart, RBAC, API, or reconciliation errors?
```

If an app database looks wrong:

```text
1. Is the InnoDBCluster present in the app namespace?
2. Does its status show the expected instance and router counts?
3. Are MySQL pods scheduled and running?
4. Are PVCs bound?
5. Does the application-facing MySQL Service exist?
6. Does that Service have endpoints?
7. Did the app's database init Job finish?
8. Does the app Deployment reference the expected host and Secret names?
```

If the app page fails:

```text
1. Check app Service endpoints and pod readiness for 502-style failures.
2. Check database init and application logs for database connection failures.
3. Check InnoDBCluster status before editing app config.
4. Check ingress only after the app Service has ready endpoints.
```

The most common trap is editing the application Deployment when the database
Service has no endpoints, or editing ingress when the app pod is not ready. Keep
the layers separate and the problem usually gets smaller.

## Safe Operator Upgrades

Treat a MySQL Operator upgrade as a platform migration, even if the Pulumi diff
only shows a Helm chart version. The operator owns CRDs, controller behavior,
router behavior, status fields, backup/failover APIs, and defaulting behavior.
All MySQL-backed apps can be affected.

For an operator chart upgrade:

```text
1. Inspect the current operator stack and chart values.
2. Change the chart version deliberately.
3. Regenerate MySQL CRDs and typed bindings from that chart.
4. Review generated diffs for CRD field additions, removals, or default changes.
5. Run Python and lint checks.
6. Preview the operator stack.
7. Preview MediaWiki and WordPress.
8. Apply only when the previews and migration plan are understood.
9. After apply, verify existing InnoDBCluster status and application behavior.
```

The command path before apply is:

```bash
just generate-mysql-crds <chart-version>
just check-python
just lint
git diff --check

just preview pulumi/core/operators/mysql mx
just preview pulumi/apps/mediawiki mx
just preview pulumi/apps/wordpress mx
```

Do not change chart topology casually. The upstream chart values document
several install-time identities as persistent or topology-frozen, including
deployment identity, standalone mode, and watched namespace sets. This repo
currently relies on the default global operator. Changing that shape can leave
custom resources without a controller or conflict with existing operator
identity.

Do not hand-edit generated CRD bindings to make an app import compile. If
generated bindings do not match the CRDs, fix the generation workflow or chart
version alignment.

## Safe MySQL Cluster Changes

Changing an application `InnoDBCluster` is different from changing the operator.
That is an app stack migration because it can affect data, PVCs, database
version, router count, or connection names.

For MediaWiki or WordPress cluster changes, keep the change in the app stack:

```bash
just preview pulumi/apps/mediawiki mx
just preview pulumi/apps/wordpress mx
```

Use one app at a time unless there is a strong reason to coordinate them.
Preview the exact app being changed, then validate that app's database and page
flow after apply.

Be especially careful with:

```text
mysqlVersion
mysqlClusterName
mysqlInstances
mysqlRouterInstances
mysqlStorageSize
storageClassName
root credential Secret identity
database name, user, and table prefix
```

Increasing storage is usually safer than changing identity. Changing a cluster
name or namespace is a migration. Changing the MySQL version is a database
upgrade. Changing router count changes the app connection path. Changing the
storage class or PVC template can imply replacement depending on Kubernetes and
operator behavior.

For MediaWiki, also watch the install, update, and compatibility Jobs. The
database can be reachable while an application migration still fails. For
WordPress, remember that the database is only half the state; the
`wordpress-data` PVC matters too.

## When Not To Touch This Stack

Do not start with the operator stack for every MySQL-shaped symptom.

Use the app stack first when:

```text
MediaWiki cannot log in
MediaWiki maintenance Jobs fail
WordPress redirects to the wrong host
WordPress uploads are missing
an app's database init Job cannot create its database
an app Deployment references the wrong Secret or host
only one app's InnoDBCluster is unhealthy
```

Use the operator stack when:

```text
the mysql-operator Deployment is unhealthy
the MySQL CRDs are missing or incompatible
multiple app clusters show the same reconciliation failure
the Helm chart or controller version needs to change
the generated CRD bindings need to be refreshed
operator topology or cluster-domain behavior is the real subject
```

Do not patch operator-created child resources as a durable fix. They are useful
evidence, but the controller may overwrite manual changes. Durable changes
belong either in this operator stack, in the generated CRD workflow, or in the
application stack that owns the `InnoDBCluster`.
