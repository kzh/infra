# Redpanda

Source: `pulumi/data/streaming/redpanda`

This page is deliberately careful about the word "Redpanda". Redpanda the
product is a Kafka-compatible streaming platform. Redpanda in this repo, today,
is only the Redpanda Kubernetes operator installation and its CRDs. There is no
repo-owned Redpanda broker cluster, no repo-owned Redpanda topic, no repo-owned
Redpanda user, and no bootstrap address that an application can use.

That distinction is the difference between a control plane and a data plane.
The operator is a Kubernetes controller. It watches Redpanda custom resources
and reconciles Kubernetes objects from them. A broker cluster is the thing that
actually accepts Kafka protocol connections, stores topic partitions on disk,
replicates records, and serves producers and consumers. This project installs
the former foundation; it does not declare the latter.

## The Mental Model

Redpanda is useful to think about through the Kafka client model first. A client
does not usually care whether the server implementation is Apache Kafka,
Redpanda, or another Kafka-compatible broker. The client connects to a bootstrap
server, asks for cluster metadata, then talks to the brokers that own the topic
partitions it needs.

A topic is an ordered event log split into partitions. Producers append records
to those partitions. Consumers read records and usually commit offsets as part
of a consumer group. Replication controls how many broker copies exist for a
partition. Retention controls how long or how large the log can grow before old
records are removed. This model is simple at the API surface and deeply
operational underneath, because every useful client interaction depends on the
same concrete pieces being correct:

- at least one running broker;
- a reachable bootstrap listener;
- advertised broker addresses that clients can actually reach after metadata
  lookup;
- topic definitions with sane partition and replication settings;
- credentials and authorization when auth is enabled;
- durable storage for broker data;
- monitoring that can distinguish "the controller is up" from "the data plane
  can move records".

The current `redpanda` Pulumi project does not create those pieces. It creates
the machinery that could reconcile them later.

## What This Project Owns

The source is small enough to read in one pass. `__main__.py` creates a
Kubernetes namespace named `redpanda`, then installs the Redpanda `operator`
Helm chart from `https://charts.redpanda.com` at version `26.1.3`.

The repo-owned configuration is:

```text
Namespace:      redpanda
Namespace label app: redpanda
Helm chart:     operator
Chart repo:     https://charts.redpanda.com
Chart version:  26.1.3
CRDs:           enabled
Extra flag:     --enable-helm-controllers=false
Pulumi export:  name=redpanda
```

`crds.enabled` is explicitly set to `true`; the operator chart default is
`false`. That is why this stack installs the Redpanda CRDs instead of only the
operator Deployment and RBAC.

The additional `--enable-helm-controllers=false` flag is present in the Pulumi
source. With the running `v26.1.3` operator binary, `redpanda-operator run
--help` reports that this flag is deprecated and unused. Do not read this flag
as the reason no Redpanda cluster exists. The reason no cluster exists is
simpler and more important: this repo does not declare a `Redpanda` custom
resource.

The Pulumi project has no stack config files beside `Pulumi.yaml`. It depends on
`pulumi` and `pulumi-kubernetes`, uses the Python `uv` toolchain, and exports
only the string `name=redpanda`. There are no service-local dashboards, images,
generated Redpanda CRD bindings, Redpanda custom resources, broker Services,
StatefulSets, PVCs, users, topics, schemas, or Console resources in this
project.

## What Exists In The Cluster

The live cluster can have two different kinds of Redpanda objects:

- objects managed by this Pulumi project, such as the operator Deployment;
- objects that the operator might later reconcile from Redpanda custom
  resources.

At the time this page was expanded, read-only inspection matched the repo-owned
source: the `redpanda` namespace had a running operator Deployment and a metrics
Service, and the Redpanda CRDs were installed. There were no `Redpanda`,
`Topic`, `User`, `Console`, `Group`, `Schema`, `RedpandaRole`, or `ShadowLink`
custom resources present.

Recheck before relying on that live statement:

```bash
kubectl config current-context
kubectl get deploy,pods,svc -n redpanda
kubectl api-resources --api-group=cluster.redpanda.com
kubectl get redpandas.cluster.redpanda.com -A
kubectl get topics.cluster.redpanda.com -A
kubectl get users.cluster.redpanda.com -A
kubectl get consoles.cluster.redpanda.com -A
```

The installed `cluster.redpanda.com` API resources include:

```text
consoles
groups
redpandaroles
redpandas
schemas
shadowlinks
topics
users
```

Those CRDs are an API surface, not proof of a running broker. A CRD means the
Kubernetes API server knows how to store a kind of custom resource. It does not
mean any instance of that kind exists.

## What You Can Use Today

You can use this stack as the repo-owned installation point for the Redpanda
operator. That means it is useful for:

- verifying that the operator Deployment is healthy;
- verifying that Redpanda CRDs are installed;
- reading the CRD schema with `kubectl explain`;
- planning a future Pulumi change that adds a Redpanda cluster;
- checking operator logs when a future Redpanda custom resource fails to
  reconcile.

You cannot use this stack today as a streaming system for applications. There is
no bootstrap server to give to Kafka clients. There are no broker pods to
receive records. There are no topic resources managed here. There are no user
credentials, ACLs, schema registry settings, Console instance, or client smoke
test commands.

If you need a working event log in this repo right now, use the Kafka stack
rather than assuming Redpanda is ready because the operator is installed.
Redpanda becomes usable for clients only after this project grows a real broker
cluster and documents the client path.

## Operator Versus Broker Failures

Most confusion around this stack comes from mixing up operator health with
broker health.

If the operator is down, Kubernetes cannot reconcile Redpanda custom resources.
That is a control-plane problem. It affects future or existing Redpanda CRs, but
it is not the same as a broker refusing Kafka traffic.

If a broker cluster exists in the future and clients cannot produce or consume,
start with the Kafka data-plane checks: bootstrap address, advertised listeners,
network path, TLS/SASL settings, credentials, topic existence, partition
assignment, broker pod readiness, and service endpoints. The operator may be
perfectly healthy while the listener model is wrong.

Today, because there is no broker cluster, produce/consume is not the right
smoke test. The right smoke test is: the operator is running, the CRDs are
registered, and there are no unexpected Redpanda custom resources pretending to
be repo-owned.

## Inspecting The Operator

Start with the namespace and Deployment. The chart-generated Deployment name is
not a stable hand-written name, so prefer labels over hard-coding it.

```bash
kubectl get ns redpanda
kubectl get deploy,pods,svc -n redpanda
kubectl get deploy -n redpanda -l app.kubernetes.io/name=operator --show-labels
kubectl describe deploy -n redpanda -l app.kubernetes.io/name=operator
```

Read logs from the selected operator Deployment:

```bash
operator_deploy="$(kubectl get deploy -n redpanda \
  -l app.kubernetes.io/name=operator \
  -o jsonpath='{.items[0].metadata.name}')"

kubectl logs -n redpanda "deploy/${operator_deploy}" --tail=200
```

The running operator should advertise controller startup for the
`cluster.redpanda.com` kinds, including `Redpanda`, `Topic`, `User`, and
`Console`. If those controllers do not start, inspect the operator args, chart
version, RBAC, and CRD installation before changing application-facing docs.

The current chart also creates a metrics Service, but this project does not add
a ServiceMonitor, dashboard, alert, or Redpanda broker metrics pipeline. Treat
operator metrics as controller observability, not data-plane observability.

## Inspecting CRDs And Custom Resources

CRD inspection answers two different questions:

- "Can the API server store this kind?"
- "Has anyone created instances of this kind?"

Use both checks:

```bash
kubectl get crds | rg 'cluster.redpanda.com|redpanda'
kubectl api-resources --api-group=cluster.redpanda.com

kubectl get redpandas.cluster.redpanda.com -A
kubectl get topics.cluster.redpanda.com -A
kubectl get users.cluster.redpanda.com -A
kubectl get consoles.cluster.redpanda.com -A
kubectl get groups.cluster.redpanda.com -A
kubectl get schemas.cluster.redpanda.com -A
kubectl get redpandaroles.cluster.redpanda.com -A
kubectl get shadowlinks.cluster.redpanda.com -A
```

Use `kubectl explain` when designing a future Pulumi resource:

```bash
kubectl explain redpandas.cluster.redpanda.com.spec
kubectl explain topics.cluster.redpanda.com.spec
kubectl explain users.cluster.redpanda.com.spec
```

The current `Redpanda` CRD describes a spec with `chartRef`, `clusterSpec`, and
a deprecated `migration` field. `Topic` and `User` specs both need a cluster
connection source; without a cluster behind them, they cannot configure a
working broker.

When a future custom resource exists but does not reconcile, read status and
events before changing chart values:

```bash
kubectl describe redpandas.cluster.redpanda.com -n redpanda <name>
kubectl get events -n redpanda --sort-by=.lastTimestamp
kubectl logs -n redpanda "deploy/${operator_deploy}" --tail=300
```

If the resource lives outside the `redpanda` namespace, inspect that namespace
too. Redpanda custom resources are namespaced.

## What A Future Redpanda Cluster Needs

Adding a Redpanda broker cluster is not a small documentation tweak. It is a new
data-plane deployment and should be treated as infrastructure design.

At minimum, a future repo-owned cluster needs a `Redpanda` custom resource or
equivalent Pulumi-managed resource that declares the actual cluster. The current
CRD shape points to `spec.chartRef` and `spec.clusterSpec`, where `clusterSpec`
is the Helm values surface used to deploy the cluster. The guide should not add
example YAML until the repo owns a concrete spec, because small listener and
storage details decide whether clients can use the cluster safely.

The cluster design needs to answer:

- how many brokers run, and whether that count is enough for the chosen
  replication factor;
- which storage class and PVC size hold broker data;
- what happens to PVCs on scale-down or cluster deletion;
- whether the cluster is single-node evaluation infrastructure or something
  that should preserve data through node loss;
- which listener is for in-cluster clients;
- whether any listener is exposed over Tailscale, ingress, LoadBalancer, or not
  exposed at all;
- what advertised broker addresses clients receive after connecting to the
  bootstrap server;
- whether TLS and SASL are enabled, and where credentials live;
- whether users, ACLs, topics, schemas, and roles are declared through Redpanda
  CRDs;
- whether Redpanda Console should exist, and how it is reached;
- how the broker and operator are monitored;
- what smoke test proves that produce and consume both work.

The listener and advertised-address decisions are especially important. A Kafka
client can successfully reach the bootstrap Service and still fail immediately
after metadata lookup if the brokers advertise addresses that the client cannot
route to. For in-cluster clients, Kubernetes DNS names are often enough. For
private external clients, the repo should choose a deliberate access path and
document it with the exact bootstrap address. Do not invent a bootstrap address
from the namespace name.

Topic and user ownership should also be explicit. A future page should say
whether topics are created by application code, by Pulumi, by Redpanda `Topic`
custom resources, or manually during experiments. For durable repo-managed
infrastructure, prefer Pulumi-owned resources and keep secrets in Pulumi secret
config or Kubernetes Secrets, not in Markdown or plain stack config.

## Minimum Client Documentation For A Real Cluster

Once a broker cluster exists, this page should stop being operator-centered and
include real client commands. A useful Redpanda page should give a reader enough
to verify the data plane without reading chart templates.

The minimum set is:

- the in-cluster bootstrap address;
- the private external bootstrap address, if one exists;
- the authentication mode;
- where generated credentials are stored, without printing secret values;
- one smoke topic name;
- a produce command;
- a consume command;
- expected success output in non-secret form;
- the service and pod names to inspect when the smoke test fails;
- the storage and retention expectations;
- the monitoring surface.

For example, a future smoke test might use `rpk` or `kcat` from a temporary pod.
Do not add such a command here until the repo owns the cluster and the command
has been tested against the actual listener and credential model.

## Debugging Future Client Failures

When clients eventually exist, debug from the outside in.

First prove that the Kubernetes objects exist:

```bash
kubectl get redpandas.cluster.redpanda.com -A
kubectl get pods,svc,endpoints,endpointslices -n redpanda
```

Then prove that the broker pods are ready and have storage:

```bash
kubectl get pods -n redpanda -o wide
kubectl get pvc -n redpanda
kubectl describe pod -n redpanda <broker-pod>
```

Then prove that the client is using the right endpoint and protocol. Kafka
clients first connect to a bootstrap address, then connect to the broker
addresses returned in metadata. If those advertised addresses are internal-only,
an external client will fail even though the bootstrap Service looked reachable.
If TLS or SASL settings are wrong, the network path can be fine while
authentication fails.

Only after those checks should you suspect application code. A missing topic, a
wrong partition count assumption, or a consumer group offset issue can look like
a platform failure from the client side.

## Safe Changes

Keep changes repo-owned. Do not repair this stack with one-off `kubectl apply`
resources and then document them as if Pulumi owns them. If a Redpanda cluster,
topic, user, or Console should be durable, add it to the Pulumi project and
preview the stack.

For the current operator-only project, normal validation is:

```bash
just sync pulumi/data/streaming/redpanda
just check-python
just lint
git diff --check
just preview pulumi/data/streaming/redpanda stack=mx
```

Do not run `just up`, `pulumi up`, or `pulumi destroy` unless the requested task
explicitly calls for an apply or destructive action.

Treat operator upgrades as migrations. Before bumping the chart, inspect the new
chart values and CRDs:

```bash
helm show values operator --repo https://charts.redpanda.com --version <version>
helm show readme operator --repo https://charts.redpanda.com --version <version>
```

Then run a targeted preview and read the Kubernetes diff. CRD changes, RBAC
changes, webhook settings, and operator args can affect every future Redpanda
custom resource. If the chart or binary removes an old flag, update the Pulumi
values intentionally and let preview show the Deployment rollout.

For a future broker cluster, be even more conservative:

- use stable Pulumi resource names and Kubernetes `metadata.name` values;
- avoid renaming StatefulSets, Services, PVC templates, or custom resources
  without a deliberate migration plan;
- pass Pulumi outputs directly as inputs instead of creating resources inside
  `apply`;
- use `ResourceOptions(depends_on=...)` only for real ordering constraints;
- keep credentials in secrets;
- preview listener, Service, StatefulSet, and PVC changes before applying;
- treat storage, replica count, advertised listeners, TLS mode, and auth mode as
  data-plane migrations.

If typed Redpanda CRD bindings are added later, generate them through a repo
workflow and place them under `pulumi/lib`. Do not hand-edit generated SDK files.

## Reading This Page Later

This page should change the moment this project owns a real Redpanda cluster.
Until then, the honest operating model is:

```text
Redpanda operator: yes
Redpanda CRDs:     yes
Redpanda brokers:  no repo-owned brokers
Kafka endpoint:    no repo-owned bootstrap address
Topics/users:      no repo-owned Redpanda topics or users
Console:           no repo-owned Redpanda Console
```

That is a valid foundation. It is not yet a streaming service.
