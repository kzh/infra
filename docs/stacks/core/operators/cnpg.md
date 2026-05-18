# CloudNativePG Operator

Source: `pulumi/core/operators/cnpg`

CloudNativePG is the PostgreSQL control plane for this cluster. It is not the
database itself, and it is not where application database users are created.
This stack installs the Kubernetes API extensions and controller that make
PostgreSQL clusters manageable as Kubernetes resources.

From first principles, Kubernetes starts with a small set of built-in resource
types: Pods, Services, Secrets, ConfigMaps, StatefulSets, and so on. Operators
extend that model. CloudNativePG adds PostgreSQL-specific custom resource
definitions, then runs a controller that watches those custom resources and
turns them into the lower-level Kubernetes objects that a database needs.

That separation is the most important idea on this page:

```text
CloudNativePG operator stack:  API types, controller, webhook, RBAC, metrics
PostgreSQL data stack:        actual CNPG Cluster and database-facing outputs
Consumer stacks:              app databases, roles, credentials, connection use
```

If an application cannot connect to PostgreSQL, this operator is only one
possible layer. Start from the layer that owns the failing contract. Application
credentials live with the application and PostgreSQL stacks. The database
cluster spec lives in `pulumi/data/databases/postgres`. The controller,
webhook, CRDs, and operator telemetry live here.

## What This Stack Installs

The Pulumi program is intentionally small. It creates the operator namespace and
installs the upstream CloudNativePG Helm chart:

```text
Pulumi project:        cloudnative-pg
Stack source:          pulumi/core/operators/cnpg
Namespace default:     cloudnative-pg
Helm chart:            cloudnative-pg
Chart repository:      https://cloudnative-pg.github.io/charts
Chart version:         0.28.2
Operator app version:  1.29.1
Python runtime:        3.12 through uv
```

The stack currently exposes only two plain outputs:

```text
namespace
monitoring_namespace
```

Those outputs describe where the operator and dashboard resources are installed.
They are not database connection details. They should not be used by app stacks
as a substitute for depending on the PostgreSQL data stack.

The configurable inputs in `__main__.py` are deliberately narrow:

```text
namespace                 defaults to cloudnative-pg
monitoringReleaseLabel    defaults to kube-prometheus-stack
monitoringNamespace       defaults to monitoring
```

The chart values enable operator monitoring:

```text
monitoring.podMonitorEnabled: true
monitoring.podMonitorAdditionalLabels.release: kube-prometheus-stack
monitoring.grafanaDashboard.create: true
monitoring.grafanaDashboard.namespace: monitoring
monitoring.grafanaDashboard.labels.grafana_dashboard: "1"
```

Rendered with the current values, the chart includes the operator
`ServiceAccount`, controller config, default monitoring query ConfigMap,
CloudNativePG CRDs, cluster-wide RBAC, the webhook Service, the controller
Deployment, mutating and validating webhook configurations, an operator
PodMonitor, and a Grafana dashboard ConfigMap in the monitoring namespace.

The controller image tag comes from the chart app version unless overridden.
With chart `0.28.2`, the rendered operator image is
`ghcr.io/cloudnative-pg/cloudnative-pg:1.29.1`.

## The Operator Model

CloudNativePG works by adding a PostgreSQL API to Kubernetes. Once the CRDs
exist, Kubernetes can store objects such as:

```yaml
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: postgresql-cluster
  namespace: postgresql
spec:
  instances: 1
```

Kubernetes does not know how to run PostgreSQL just because it can store that
object. The CNPG controller supplies the behavior. It watches the custom
resources, compares desired state with live state, creates or updates the
supporting Kubernetes objects, and writes status back onto the custom resources.

That gives the repo a clean division of labor:

The Pulumi stack defines desired Kubernetes resources. For this operator stack,
that desired state is the controller installation and its CRDs.

The Kubernetes API server stores both the operator resources and the
PostgreSQL custom resources.

The CNPG controller reconciles `postgresql.cnpg.io` objects into running
database infrastructure.

The PostgreSQL engine still behaves like PostgreSQL. SQL roles, databases,
extensions, connections, locks, slow queries, disk pressure, and authentication
are database concerns, not generic Kubernetes concerns.

When something is wrong, keep those responsibilities separate. A healthy
operator can still manage an unhealthy database cluster. A healthy database
cluster can still reject a consumer because credentials, database names, network
routes, TLS expectations, or app configuration are wrong.

## Custom Resources

The operator chart currently renders these CRDs under
`postgresql.cnpg.io`:

```text
backups.postgresql.cnpg.io
clusterimagecatalogs.postgresql.cnpg.io
clusters.postgresql.cnpg.io
databases.postgresql.cnpg.io
failoverquorums.postgresql.cnpg.io
imagecatalogs.postgresql.cnpg.io
poolers.postgresql.cnpg.io
publications.postgresql.cnpg.io
scheduledbackups.postgresql.cnpg.io
subscriptions.postgresql.cnpg.io
```

The most important one in this repo today is `Cluster`. A CNPG `Cluster`
describes a PostgreSQL cluster: instance count, storage, image, PostgreSQL
settings, bootstrap behavior, services, monitoring, and upgrade behavior. The
operator turns that into Pods, Services, Secrets, certificates, PodDisruption
Budgets, status, and failover behavior.

The other CRDs are available because the operator supports them, but that does
not mean this repo is actively using all of them:

`Backup` and `ScheduledBackup` are for CNPG-managed backup objects and backup
schedules.

`Pooler` is for PgBouncer-style connection pooling managed by the operator.

`Database` is for declarative database management through CNPG. This repo does
not currently use CNPG `Database` objects for application databases; the
PostgreSQL stack uses the PostgreSQL provider after the cluster is running.

`Publication` and `Subscription` model PostgreSQL logical replication objects.

`ImageCatalog` and `ClusterImageCatalog` provide a catalog-based way to select
database images.

`FailoverQuorum` supports CNPG failover quorum behavior.

The key practical rule is: the existence of a CRD only means the API type is
installed. It does not mean a stack has declared a resource of that kind.
Inspect the source and the live objects before assuming a feature is active.

## Relationship To The PostgreSQL Stack

The actual shared PostgreSQL service is declared in
`pulumi/data/databases/postgres`, not in this operator stack.

That data stack installs the CNPG `cluster` Helm chart at version `0.6.1`. The
chart renders a `postgresql.cnpg.io/v1` `Cluster` named
`postgresql-cluster` in the `postgresql` namespace. The source configures one
PostgreSQL instance, PostgreSQL major version `18`, a VectorChord-enabled image,
`vchord.so` as a shared preload library, and init SQL for the VectorChord
extension.

The PostgreSQL stack also defines the app-facing service shape. It disables the
default read-only services from the cluster chart and adds a writable
ClusterIP service with Tailscale exposure. It exports the writable service name,
the writable service FQDN, the CA certificate output, and secret outputs derived
from the CNPG-generated superuser Secret. Do not paste those secret output
values into docs, tickets, chat, or commit messages.

After the CNPG cluster exists, the PostgreSQL stack uses the
`pulumi-postgresql` provider to connect to PostgreSQL and manage configured
application databases and extensions. That is a database-management layer on
top of the Kubernetes-managed cluster. It is intentionally separate from the
operator installation.

Consumer stacks should depend on the PostgreSQL stack contract, not on this
operator stack. For example, analytics and app stacks that need a database read
PostgreSQL outputs through a stack reference, create their own Kubernetes
Secrets or chart values, and then connect to the writable PostgreSQL endpoint.
They do not create or talk to the CNPG operator directly.

When debugging a consumer, ask the questions in order:

```text
Does the app have the database name, username, password, host, and port it expects?
Does the PostgreSQL stack export the expected connection contract?
Is the CNPG Cluster healthy?
Is the CNPG operator reconciling resources?
Are the CRDs and webhooks installed and serving the Kubernetes API?
```

Most connection failures are above the operator layer. Move down to the
operator only when the `Cluster` status, database Pods, or admission webhooks
point there.

## What The Operator Owns

This stack owns the operator installation:

```text
Namespace:                         cloudnative-pg
Controller Deployment:             cloudnative-pg
Webhook Service:                   cnpg-webhook-service
MutatingWebhookConfiguration:      cnpg-mutating-webhook-configuration
ValidatingWebhookConfiguration:    cnpg-validating-webhook-configuration
Operator PodMonitor:               cloudnative-pg
Dashboard ConfigMap:               cnpg-grafana-dashboard
CRDs:                              *.postgresql.cnpg.io
RBAC:                              cloudnative-pg controller roles and binding
```

The operator itself owns the resources it creates while reconciling PostgreSQL
custom resources. For the shared PostgreSQL cluster, that includes database
Pods, CNPG-managed Services, generated Secrets, certificates, status fields,
and related operational objects.

Pulumi owns the declared desired state. The operator owns the reconciliation
from custom resource to database runtime. Humans should avoid hand-mutating
operator-owned resources unless they are doing short-lived incident response
and understand that the controller may revert the change.

If a generated database Pod is wrong, do not edit the Pod. Change the CNPG
`Cluster` spec in the stack that declares it. If a generated Service selector
is wrong, inspect whether it is owned by the operator, the cluster chart, or
this repo's explicit Pulumi code before patching it. If a webhook is rejecting a
resource, inspect the webhook and CRD schema before rewriting unrelated app
configuration.

## CRD Ownership

CloudNativePG CRDs are cluster-scoped API definitions. They are not namespaced,
and they are not disposable implementation details. Deleting or replacing a CRD
can make existing custom resources unreadable, interrupt reconciliation, or
remove the API surface that the database stack depends on.

In this repo, the operator Helm chart is the owner of the CNPG CRDs. The chart
default has `crds.create: true`, and the current rendered chart includes the
CRDs as chart-managed resources. That means CRD changes belong in this operator
stack, not in the PostgreSQL data stack and not in an app stack.

There is currently no repo-local generated CNPG Pulumi SDK under `pulumi/lib`.
The PostgreSQL data stack creates the CNPG `Cluster` by installing the upstream
CNPG `cluster` Helm chart. The generated CRD package used there is
`pulumi_monitoring_crds`, and it is for the Prometheus `PodMonitor` resource,
not for CloudNativePG.

If future work needs first-class Pulumi objects for CNPG custom resources, add
that deliberately as a CRD-bindings task. Do not hand-edit generated SDK files,
copy large raw manifests into unrelated stacks, or let multiple stacks compete
to own the same CRDs.

Useful CRD inspection commands:

```bash
kubectl get crd | rg 'postgresql.cnpg.io'
kubectl describe crd clusters.postgresql.cnpg.io
kubectl explain cluster.postgresql.cnpg.io.spec
kubectl explain cluster.postgresql.cnpg.io.status
```

`kubectl explain` is often the fastest way to check whether the currently
installed CRD supports a field before changing Pulumi code or chart values.

## Status And Debugging

Start with the layer that owns the symptom. This keeps operator debugging from
turning into guesswork.

For operator installation health:

```bash
kubectl get pods -n cloudnative-pg
kubectl get deploy -n cloudnative-pg cloudnative-pg
kubectl get svc -n cloudnative-pg cnpg-webhook-service
kubectl logs -n cloudnative-pg deploy/cloudnative-pg --tail=200
```

For webhook/admission problems:

```bash
kubectl get mutatingwebhookconfiguration cnpg-mutating-webhook-configuration
kubectl get validatingwebhookconfiguration cnpg-validating-webhook-configuration
kubectl describe validatingwebhookconfiguration cnpg-validating-webhook-configuration
kubectl get endpoints -n cloudnative-pg cnpg-webhook-service
```

Webhook failures often show up during `pulumi preview` or `pulumi up` as
admission errors for `Cluster`, `Backup`, `Database`, or related resources. If
that happens, verify the webhook Service has endpoints and the operator Pod is
ready before changing the resource spec.

For CRD presence and schema:

```bash
kubectl get crd | rg 'postgresql.cnpg.io'
kubectl get clusters.postgresql.cnpg.io --all-namespaces
kubectl get databases.postgresql.cnpg.io --all-namespaces
kubectl get poolers.postgresql.cnpg.io --all-namespaces
```

For the shared PostgreSQL cluster:

```bash
kubectl get cluster -n postgresql postgresql-cluster
kubectl describe cluster -n postgresql postgresql-cluster
kubectl get pods -n postgresql -l cnpg.io/cluster=postgresql-cluster -o wide
kubectl get svc -n postgresql
kubectl get endpointslices -n postgresql -l kubernetes.io/service-name=postgresql-cluster-rw
kubectl get events -n postgresql --sort-by=.lastTimestamp
```

The PostgreSQL stack adds a Pulumi wait annotation to CNPG `Cluster` resources:

```text
pulumi.com/waitFor: jsonpath={.status.phase}=Cluster in healthy state
```

That is there because generic Kubernetes await behavior does not fully
understand CNPG cluster readiness. If a preview or apply is waiting on the
database stack, look at the `Cluster` status first. The status and conditions
usually say whether the controller is waiting for Pods, storage, images,
certificates, bootstrap, recovery, or some other dependency.

For service routing:

```bash
kubectl get svc -n postgresql -l cnpg.io/cluster=postgresql-cluster
kubectl describe svc -n postgresql postgresql-cluster-rw
kubectl get endpointslices -n postgresql -l kubernetes.io/service-name=postgresql-cluster-rw
```

Healthy Pods do not guarantee a healthy Service. Check selectors and endpoint
slices when an app sees connection refused, no route, or a 502 from a proxy in
front of a database-backed service.

For storage and scheduling:

```bash
kubectl get pvc -n postgresql
kubectl describe pod -n postgresql -l cnpg.io/cluster=postgresql-cluster
kubectl get events -n postgresql --sort-by=.lastTimestamp
```

Pending database Pods are often about storage classes, PVC binding, node
capacity, taints, image pulls, or topology constraints. Those are Kubernetes
runtime problems around the `Cluster`, not operator installation problems unless
the controller is also unhealthy.

For monitoring resources:

```bash
kubectl get podmonitor -n cloudnative-pg cloudnative-pg
kubectl get configmap -n monitoring cnpg-grafana-dashboard
kubectl get podmonitor -n postgresql postgresql-cluster
kubectl get prometheusrule -n postgresql postgresql-cluster-alert-rules
```

There are two monitoring layers. This operator stack enables the operator
PodMonitor and dashboard. The PostgreSQL data stack creates the database
PodMonitor and PrometheusRule for the actual cluster.

## Common Failure Patterns

Admission webhook errors usually mean the Kubernetes API server cannot call the
CNPG webhook or the webhook rejected the object. Check the webhook Service,
operator Pod readiness, webhook configurations, and the CRD schema.

`Cluster` stuck before healthy state usually means the operator is alive but
cannot satisfy the database spec. Read `status.conditions`, check events in the
database namespace, then check Pods, PVCs, image pulls, and generated Services.

Database Pod pending usually points to storage, scheduling, image, or resource
constraints. Start with `kubectl describe pod` and namespace events.

Application connection failure with a healthy `Cluster` is usually above the
operator. Check the consuming stack's Secret, host, port, database name,
username, password, SSL mode, and service endpoints.

Missing metrics can be either operator-level or database-level. Confirm whether
the missing target is the operator PodMonitor in `cloudnative-pg` or the
database PodMonitor in `postgresql`.

CRD field rejected after a chart change means the operator CRDs may not support
that field yet, or the field moved between chart/operator versions. Check the
installed CRD with `kubectl explain`, compare chart values, and preview the
operator stack before changing the data stack.

## Safe Upgrades

Treat CNPG upgrades as control-plane migrations. A small version bump in this
stack can change CRD schemas, webhook validation, default reconciliation
behavior, generated Services, failover behavior, metrics, and status fields for
every PostgreSQL cluster in the Kubernetes cluster.

There are two related but distinct upgrade surfaces:

```text
Operator chart/app version:  pulumi/core/operators/cnpg
PostgreSQL cluster chart:    pulumi/data/databases/postgres
PostgreSQL engine image:     pulumi/data/databases/postgres
```

Do not mix those up. Upgrading the operator does not by itself upgrade the
PostgreSQL engine image. Upgrading the PostgreSQL engine image belongs to the
data stack and should be reasoned about as a database upgrade.

Before changing the operator chart version, inspect the chart you are moving to:

```bash
helm show chart cloudnative-pg \
  --repo https://cloudnative-pg.github.io/charts \
  --version <new-version>

helm show values cloudnative-pg \
  --repo https://cloudnative-pg.github.io/charts \
  --version <new-version>

helm template cloudnative-pg cloudnative-pg \
  --repo https://cloudnative-pg.github.io/charts \
  --version <new-version> \
  --namespace cloudnative-pg
```

Compare the new chart against the current version for CRDs, webhook settings,
RBAC, image tag, monitoring defaults, and removed or renamed values. The value
that looks harmless in `__main__.py` may expand into cluster-scoped resources
after Helm rendering.

For code validation in this repo:

```bash
just sync pulumi/core/operators/cnpg
just check-python
just lint
git diff --check
just preview pulumi/core/operators/cnpg stack=mx
```

If the operator preview changes CRDs or webhook behavior, also preview the
PostgreSQL data stack before applying the change:

```bash
just preview pulumi/data/databases/postgres stack=mx
```

A good upgrade review answers these questions:

```text
Does the new operator chart still install the same expected CRDs?
Did any CRD schemas remove, rename, or tighten fields used by the PostgreSQL stack?
Did the controller image app version change?
Did webhook failure policy, service name, or port change?
Did monitoring labels, PodMonitor shape, or dashboard ConfigMap shape change?
Does the PostgreSQL cluster chart still render a valid Cluster against the new CRDs?
Does the live shared PostgreSQL Cluster remain healthy after reconciliation?
```

When an approved apply is part of the change, apply the control-plane change
before applying any data-stack change that depends on new CRD schema. After the
operator change, verify the controller and webhook first, then the shared
PostgreSQL `Cluster` status. Do not delete CRDs to force a clean install.

Post-upgrade checks should stay focused and non-secret-bearing:

```bash
kubectl get pods -n cloudnative-pg
kubectl logs -n cloudnative-pg deploy/cloudnative-pg --tail=100
kubectl get clusters.postgresql.cnpg.io --all-namespaces
kubectl get cluster -n postgresql postgresql-cluster
kubectl get pods -n postgresql -l cnpg.io/cluster=postgresql-cluster
```

If the operator is healthy but the database `Cluster` is not, move to the
PostgreSQL stack and the `Cluster` status. If both are healthy but an app is
failing, move to the consumer stack.

## Editing Rules For This Stack

Keep edits scoped to the layer that owns the behavior:

```text
Operator chart version, operator namespace, operator monitoring: this stack
CNPG Cluster spec, database image, database services: PostgreSQL data stack
Application databases and extensions: PostgreSQL data stack configuration
Application credentials and chart values: consumer stack
Generated monitoring CRD bindings: pulumi/lib/monitoring_crds generation flow
CNPG CRD bindings, if ever needed: deliberate CRD generation task
```

Prefer preview evidence over inference. If a chart upgrade changes rendered
resources, say what changed. If a preview fails, classify the failure before
editing more code: missing config, ESC/config issue, live-state drift, provider
behavior, CRD/schema mismatch, webhook health, or a real program bug.

For this operator stack, the normal non-apply validation sequence is:

```bash
just sync pulumi/core/operators/cnpg
just check-python
just lint
git diff --check
just preview pulumi/core/operators/cnpg stack=mx
```

For changes that may affect the shared database cluster, add:

```bash
just preview pulumi/data/databases/postgres stack=mx
```

Do not run apply or destroy commands unless the task explicitly calls for that.
This repo manages live infrastructure, and CloudNativePG sits directly in the
path of shared PostgreSQL availability.
