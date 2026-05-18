# Flink

Source: `pulumi/data/streaming/flink`

Flink is the stream processing engine in this repo. Kafka stores ordered event
streams. Flink runs computations over streams while the streams are still
moving. A Flink job can read records from Kafka, keep state about what it has
seen, join or window records by event time, and continuously write new records
or derived results somewhere else.

The important difference from a batch engine is that a Flink job is normally
long-running. It is not a query that starts, reads a fixed input, and exits. It
is a dataflow that stays alive, consumes new events, maintains state, and makes
progress through checkpoints. When operating Flink, the main questions are:

```text
is the operator reconciling the cluster?
is the JobManager healthy?
are enough TaskManagers registered?
what jobs are running?
where is job state stored?
are checkpoints completing?
can the job restore after a pod, code, image, or version change?
```

This page is about the Flink stack as it exists in this repository. It does not
describe a generic enterprise Flink platform with a job catalog, artifact
registry, and production checkpoint backend. Those pieces can be added, but
they are not present in the current `pulumi/data/streaming/flink` program.

## What This Stack Creates

Pulumi installs the Apache Flink Kubernetes Operator and creates one Flink
session cluster. A session cluster is a reusable cluster that accepts submitted
jobs. That is different from an application cluster, where the job itself is
part of the `FlinkDeployment` spec and the cluster exists for that job.

The Kubernetes objects are owned by the Flink Pulumi project:

```text
Pulumi project:          pulumi/data/streaming/flink
Pulumi stack:            mx
Namespace:               flink
Helm release:            flink-kubernetes-operator
Helm chart:              flink-kubernetes-operator
Helm chart version:      1.14.0
Helm repo:               https://downloads.apache.org/flink/flink-kubernetes-operator-1.14.0/
Operator webhook:        disabled
Flink custom resource:   FlinkDeployment/flink-session
Flink API version:       flink.apache.org/v1beta1
Cluster mode:            session cluster
REST service:            flink-session-rest
REST port:               8081
Ingress:                 flink-ui
Ingress class:           tailscale
UI URL output:           https://flink
```

The `FlinkDeployment` defaults are intentionally small:

```text
Flink version:           v2_2
Image:                   docker.io/library/flink:2.2.0-scala_2.12-java17
Image pull policy:       IfNotPresent
Service account:         flink
JobManager memory:       1024m
JobManager CPU:          0.5
TaskManager memory:      1024m
TaskManager CPU:         0.5
TaskManager replicas:    1 requested
Task slots:              2
Default parallelism:     1
REST service type:       ClusterIP
```

Pulumi exports these values for operators and scripts:

```text
namespace
operatorChartVersion
clusterName
flinkVersion
image
restService
hostname
url
taskManagerReplicas
taskSlots
parallelism
ingressName
```

Read them from the project directory:

```bash
cd pulumi/data/streaming/flink

pulumi stack output --stack mx namespace
pulumi stack output --stack mx clusterName
pulumi stack output --stack mx restService
pulumi stack output --stack mx url
```

The source is deliberately compact. It creates a namespace, installs the Helm
chart, creates a raw Kubernetes `CustomResource` for `FlinkDeployment`, and then
creates a Tailscale ingress pointing at the operator-created REST service. There
is no `StackReference` to Kafka, no declared `FlinkSessionJob`, no production
job artifact path, and no configured durable checkpoint or savepoint directory.

## The Stream Processing Model

A stream is an unbounded sequence of records. In this repo, Kafka is the natural
stream source: a topic contains partitions, and each partition is an ordered
log. Flink consumes records from those partitions and turns them into a graph of
operators.

A Flink job is that graph. The graph usually has:

```text
source operators       read records from Kafka or another system
transform operators    map, filter, parse, join, aggregate, or enrich records
state                  remembers information across records
sink operators         write results to Kafka, a database, object storage, or another system
```

The word "operator" is overloaded. In Kubernetes, the Flink Kubernetes Operator
is the controller installed by the Helm chart. Inside Flink, an operator is a
node in the job graph, such as a Kafka source, a windowed aggregation, or a sink.
This page uses "Kubernetes operator" for the controller and "job operator" for a
node inside a Flink job.

Flink's useful property is not just that it runs continuously. Its useful
property is that it can run continuously while keeping consistent state. For
example, a job might count events per user over five-minute windows, maintain a
join table keyed by account ID, or remember whether a device has already emitted
a matching event. That memory is Flink state.

State is partitioned by key. If the job groups records by `user_id`, then all
records for one user need to reach the same keyed state partition. Parallelism
controls how many parallel subtasks run. Task slots control how much concurrent
work the session cluster can host. The current custom resource requests one
TaskManager replica, two slots per TaskManager, and default parallelism `1`, so
it is sized for a small foundation and smoke testing, not a busy multi-job
platform.

## Jobs, JobManager, TaskManagers, And Slots

The JobManager coordinates a Flink cluster. It accepts job submissions through
the REST API, builds the execution graph, schedules work, tracks checkpoint
progress, and coordinates recovery.

TaskManagers run the actual work. A TaskManager has task slots. Slots are the
cluster capacity units that Flink uses to place parallel job subtasks. In this
repo's defaults:

```text
Requested TaskManager replicas: 1
Task slots per TaskManager: 2
Configured slots at requested replica count: 2
Default job parallelism: 1
```

When the runtime has TaskManagers registered at the requested size, a simple job
can run and a small amount of extra parallel work can fit. There is not much
room for concurrent jobs, high parallelism, or large state. Increasing
`taskManagerReplicas` or `taskSlots` changes available capacity. Increasing
`parallelism` changes how many subtasks a job uses by default. Those are
operational changes, not just numeric cleanups, because they affect scheduling,
resource pressure, checkpoint load, and state layout.

The `FlinkDeployment` sets:

```text
kubernetes.rest-service.exposed.type = ClusterIP
parallelism.default = <parallelism>
taskmanager.numberOfTaskSlots = <taskSlots>
```

The REST service is not exposed as a public `LoadBalancer` or `NodePort`.
External access to the UI goes through the Tailscale ingress.

## Kubernetes And Session Cluster Wiring

The stack is built around the Kubernetes operator:

1. Pulumi creates the `flink` namespace.
2. Pulumi installs the `flink-kubernetes-operator` Helm release in that
   namespace.
3. Pulumi creates `FlinkDeployment/flink-session`.
4. The Kubernetes operator reconciles that custom resource into JobManager,
   TaskManager, Service, and related runtime objects.
5. Pulumi creates `Ingress/flink-ui`, using ingress class `tailscale`, with the
   host exported as `url`.

The dependency chain in the Pulumi program is explicit:

```text
Namespace -> Helm Release -> FlinkDeployment -> Ingress
```

The ingress backend is the REST service named from the cluster name:

```text
<clusterName>-rest
```

With the default cluster name, that is:

```text
flink-session-rest
```

The ingress sends `/` to service port `8081`. The same endpoint is the Flink
REST API used by the CLI and the Web UI. If the UI is unavailable, inspect the
service and endpoints before assuming the ingress is wrong:

```bash
cd pulumi/data/streaming/flink
NS="$(pulumi stack output --stack mx namespace)"
REST="$(pulumi stack output --stack mx restService)"

kubectl get svc,endpoints,ingress -n "$NS"
kubectl describe svc -n "$NS" "$REST"
kubectl get endpoints -n "$NS" "$REST" -o wide
```

The Helm values disable the operator webhook:

```text
webhook.create = false
```

That means Kubernetes admission validation/mutation from the Flink operator
webhook is not part of this deployment. The operator still reconciles the
`FlinkDeployment`; the disabled webhook only changes the admission path.

The operator pod itself is resource-limited by chart values:

```text
operator CPU request:     100m
operator CPU limit:       500m
operator memory request:  512Mi
operator memory limit:    512Mi
```

Those are separate from JobManager and TaskManager resources. Operator health
and Flink cluster health are related but not the same thing.

## Opening The UI

Use the stack output rather than hard-coding the URL in scripts:

```bash
cd pulumi/data/streaming/flink
pulumi stack output --stack mx url
```

The UI is the fastest place to answer basic runtime questions:

```text
is the JobManager reachable?
how many TaskManagers are registered?
how many slots are available?
which jobs are running, canceled, failed, or finished?
are checkpoints completing?
is any job backpressured?
what exception caused a job failure?
```

If the URL opens but there are no TaskManagers, the ingress path is probably
fine and the issue is inside the Flink deployment. If the URL does not open,
check the ingress, REST service, endpoints, and JobManager pod.

For local REST inspection, port-forward the service:

```bash
cd pulumi/data/streaming/flink
NS="$(pulumi stack output --stack mx namespace)"
REST="$(pulumi stack output --stack mx restService)"

kubectl port-forward -n "$NS" "svc/$REST" 8081:8081
```

Then query the REST API from another shell:

```bash
curl -s http://localhost:8081/overview
curl -s http://localhost:8081/taskmanagers
curl -s http://localhost:8081/jobs/overview
```

Those endpoints are useful when the UI is slow, when you want structured output,
or when you are checking the cluster from a terminal-only environment.

## Job Submission In This Repo

The current Pulumi program creates a session cluster, but it does not declare
any jobs. That means there are two different levels of support:

```text
Manual job submission:      supported by the Flink session cluster
Repo-backed durable jobs:   not currently defined
```

Manual submission is useful for smoke tests and experiments. It is not a good
way to operate important jobs because the job artifact, arguments, checkpoint
settings, ownership, and upgrade path are not captured in the repo.

The most direct smoke-test path is to port-forward the REST service and use a
Flink CLI that matches the cluster version:

```bash
cd pulumi/data/streaming/flink
NS="$(pulumi stack output --stack mx namespace)"
REST="$(pulumi stack output --stack mx restService)"

kubectl port-forward -n "$NS" "svc/$REST" 8081:8081
```

In another shell, from an environment that has a compatible Flink CLI:

```bash
flink list -m localhost:8081
flink run -m localhost:8081 /path/to/job.jar
```

You can also run the CLI from inside the JobManager container if you want to use
the Flink distribution already present in the deployed image. Discover the
JobManager first rather than relying on a local shell alias:

```bash
cd pulumi/data/streaming/flink
NS="$(pulumi stack output --stack mx namespace)"
CLUSTER="$(pulumi stack output --stack mx clusterName)"

kubectl get pods -n "$NS" -o wide
kubectl exec -n "$NS" deploy/"$CLUSTER" -- /opt/flink/bin/flink list -m localhost:8081
kubectl exec -n "$NS" deploy/"$CLUSTER" -- find /opt/flink/examples -maxdepth 2 -type f
```

If the image contains example jars, a one-off run has this shape:

```bash
kubectl exec -n "$NS" deploy/"$CLUSTER" -- \
  /opt/flink/bin/flink run \
    -m localhost:8081 \
    /opt/flink/examples/streaming/<example>.jar
```

For anything that should survive beyond a smoke test, add a repo-backed Flink
job resource instead. The Apache Flink Kubernetes Operator supports job-bearing
custom resources such as `FlinkDeployment` application clusters and
`FlinkSessionJob` resources. This repo does not currently generate typed Flink
CRD bindings, so a first implementation would likely mirror the current raw
`k8s.apiextensions.CustomResource` style unless typed bindings are added
deliberately.

A durable job definition should include:

```text
the job artifact location
the entry class or Python entrypoint
the arguments
the desired parallelism
the restart behavior
the checkpoint configuration
the savepoint and upgrade procedure
the source and sink contracts
```

Without those details, the cluster can run the job, but the repo cannot explain
how to rebuild, restore, or safely upgrade it.

## Kafka Relation

Kafka and Flink are separate stacks in this repo:

```text
Kafka stack:   pulumi/data/streaming/kafka
Flink stack:   pulumi/data/streaming/flink
```

There is no `StackReference` from Flink to Kafka, and the Flink session cluster
does not inject Kafka bootstrap settings into jobs. A Flink job that reads Kafka
must be configured by the job itself.

Inside Kubernetes, use Kafka's internal plain bootstrap service from the Kafka
stack:

```text
kafka-kafka-bootstrap:9092
```

The Kafka stack also exposes:

```text
tlsBootstrapServers
tailnetBootstrapServers
tailnetBroker
smokeTopic
```

Those are useful for clients outside the cluster or for smoke testing Kafka
itself. A Flink job running in the `flink` namespace is still an in-cluster
client, so the internal bootstrap service is the natural path unless the job is
deliberately testing the tailnet listener.

Kafka auto-create topics is disabled in this repo. That is important for Flink.
If a job writes to a new topic, declare that topic through the Kafka Pulumi
stack instead of relying on the producer to create it implicitly. Topic
partitions also matter for Flink parallelism: a Kafka source cannot read one
topic partition with more parallel source subtasks than there are partitions
doing useful work.

A typical topology here would be:

```text
producer service -> Kafka topic -> Flink job -> Kafka topic or database
```

For stateful Flink jobs, Kafka is not a substitute for checkpoints. Kafka can
retain input records so a job can reread them, but Flink's internal state,
timers, window contents, and exactly-once source positions live in checkpoints.
If checkpoints are missing or not durable, a failed stateful job may be able to
replay input but still lose its computed state.

Flink jobs that use Kafka usually need Kafka connector dependencies in the job
artifact or image. The base Flink image configured by this stack is the standard
Flink image; do not assume every connector jar needed by an application is
already available.

## State, Checkpoints, And Savepoints

State is what makes Flink valuable and what makes Flink operationally serious.
A stateless job can often be restarted with little ceremony. A stateful job
needs a recovery plan.

A checkpoint is an automatic, consistent snapshot of a running job's state. It
lets Flink recover after TaskManager loss, JobManager restart, or other runtime
failure. Checkpoints are part of normal operations.

A savepoint is a deliberate snapshot, usually taken before planned work:

```text
changing job code
changing job parallelism
changing the Flink image or version
changing state schema
moving from a manually submitted job to a repo-backed job
decommissioning or renaming a job
```

This stack does not currently configure durable checkpoint storage. The
`FlinkDeployment` does not set `state.checkpoints.dir`,
`state.savepoints.dir`, a state backend, S3 filesystem configuration, or
credentials for object storage. That is acceptable for a foundation cluster, but
it is not enough for an important stateful job.

Before adding a production stateful job, answer these in the job's docs or code
review:

```text
where are checkpoints written?
where are savepoints written?
does every TaskManager and JobManager pod have access to that location?
which Kubernetes Secret or Pulumi secret provides storage credentials?
how often are checkpoints triggered?
how many completed checkpoints are retained?
what is the restore command or CRD field?
what is the rollback plan if the new job cannot restore?
```

For CLI-submitted session jobs, the savepoint flow has this general shape. Use
the exact CLI help from the deployed image when doing real work:

```bash
cd pulumi/data/streaming/flink
NS="$(pulumi stack output --stack mx namespace)"
CLUSTER="$(pulumi stack output --stack mx clusterName)"

kubectl exec -n "$NS" deploy/"$CLUSTER" -- /opt/flink/bin/flink list -m localhost:8081

kubectl exec -n "$NS" deploy/"$CLUSTER" -- \
  /opt/flink/bin/flink savepoint \
    <job-id> \
    <durable-savepoint-directory> \
    -m localhost:8081

kubectl exec -n "$NS" deploy/"$CLUSTER" -- \
  /opt/flink/bin/flink run \
    -s <savepoint-path> \
    -m localhost:8081 \
    /path/to/new-job.jar
```

The placeholder path must be a real durable location reachable from the Flink
pods. A path on a single container filesystem is not a safe recovery point.

For repo-backed `FlinkSessionJob` or application `FlinkDeployment` resources,
prefer the operator-supported savepoint and upgrade fields instead of manual
terminal-only procedure. The exact fields should be chosen from the CRD version
installed by this chart and previewed before use.

## What To Inspect First

Start with the custom resource and the objects it reconciles:

```bash
cd pulumi/data/streaming/flink
NS="$(pulumi stack output --stack mx namespace)"
CLUSTER="$(pulumi stack output --stack mx clusterName)"
REST="$(pulumi stack output --stack mx restService)"

kubectl get flinkdeployments -n "$NS"
kubectl describe flinkdeployment -n "$NS" "$CLUSTER"
kubectl get pods,deploy,svc,endpoints,ingress -n "$NS"
kubectl describe svc -n "$NS" "$REST"
kubectl logs -n "$NS" deploy/flink-kubernetes-operator --tail=200
```

Then inspect the Flink runtime:

```bash
kubectl logs -n "$NS" deploy/"$CLUSTER" --tail=200
kubectl logs -n "$NS" -l app="$CLUSTER",component=taskmanager --tail=200 --all-containers=true
```

TaskManager pods are runtime objects reconciled by Flink and may not be present
in an idle session cluster. If a job is running and the label selector returns
nothing, list the pods and select the JobManager and TaskManager pods by name:

```bash
kubectl get pods -n "$NS" --show-labels
```

With a port-forward running, the REST API is often faster than reading raw logs:

```bash
curl -s http://localhost:8081/overview
curl -s http://localhost:8081/taskmanagers
curl -s http://localhost:8081/jobs/overview
curl -s http://localhost:8081/jobmanager/config
```

For a specific job, get its job ID from the UI or `jobs/overview`, then inspect
it:

```bash
JOB_ID="<job-id>"

curl -s "http://localhost:8081/jobs/$JOB_ID"
curl -s "http://localhost:8081/jobs/$JOB_ID/checkpoints"
curl -s "http://localhost:8081/jobs/$JOB_ID/exceptions"
```

The Kubernetes operator can be healthy while a Flink job is failing. The
JobManager can be healthy while TaskManagers are missing. The UI can be
reachable while a job is backpressured or failing checkpoints. Keep those layers
separate while debugging.

## Common Failure Patterns

The UI does not load. Check `Ingress/flink-ui`, `Service/flink-session-rest`,
the service endpoints, and the JobManager pod. If the service has no endpoints,
the ingress is not the first problem.

The operator is running but the session cluster does not appear. Describe the
`FlinkDeployment` and read the operator logs. The Helm release only installs the
controller; the controller still has to reconcile the custom resource.

The UI loads but no TaskManagers are registered. First check whether the session
cluster is simply idle. If a job is running or waiting for slots, inspect
TaskManager pods, resource requests, scheduling events, and the
`FlinkDeployment` status. The custom resource requests only one TaskManager
replica by default.

A job submission fails. Confirm that you are submitting to the REST service for
this cluster, that the artifact matches Flink `v2_2` and the configured image,
and that required connector jars are included.

A Kafka job cannot connect. From a Flink pod, test the in-cluster bootstrap
address `kafka-kafka-bootstrap:9092`. Do not use the Kafka tailnet listener from
inside the cluster unless that is the specific path being tested. Also confirm
the topic exists; Kafka auto-create topics is disabled.

A job starts but makes no progress. Check source partitions, consumer group
position, backpressure, TaskManager logs, and whether the job parallelism is
greater than useful source parallelism. One Kafka partition can only be consumed
by one active subtask within a consumer group.

Checkpoints fail. Check the checkpoint directory, storage credentials,
filesystem connector availability, and whether all Flink pods can reach the
same durable path. In the current stack, durable checkpoint storage has not been
configured by Pulumi.

A stateful job restarts from the beginning or with empty state. Check whether
the job had completed checkpoints and whether the new submission restored from a
checkpoint or savepoint. Kafka replay does not restore Flink's internal keyed
state by itself.

An upgrade loses a manually submitted job. Manual session submissions are not
repo-owned. If the session cluster is replaced or the job is canceled without a
savepoint, the repo does not have enough information to recreate that exact
runtime state.

## Safe Changes

For docs-only changes to this page, keep edits scoped to this file. For Pulumi
changes to the Flink stack, use the normal repo validation path:

```bash
just sync pulumi/data/streaming/flink
just check-python
just lint
git diff --check
just preview pulumi/data/streaming/flink stack=mx
```

Do not apply with `pulumi up` or `just up` unless an apply is explicitly part of
the task.

Treat these as high-caution changes:

```text
clusterName
flinkVersion
image
operatorChartVersion
the Helm repository URL
serviceAccount
taskManagerReplicas
taskSlots
parallelism
checkpoint and savepoint locations
ingress host
REST service exposure type
job artifact paths
Kafka bootstrap addresses and topic names
```

Changing `clusterName` changes names derived from it, including the REST service
and ingress backend. That can replace or disconnect runtime objects.

Changing `flinkVersion` or `image` affects job compatibility, connector
availability, savepoint compatibility, and sometimes the operator's expected
schema. Take a savepoint before changing a stateful job's runtime.

Changing `operatorChartVersion` may also require changing the Helm repository
URL in the Pulumi source, because the current URL includes the operator version
path. Do not bump only one of them without checking the chart location and CRD
schema.

Changing slots or parallelism affects capacity and state distribution. Raising
parallelism can increase throughput if the sources, sinks, and TaskManagers have
enough parallelism. Lowering or reshaping parallelism for a stateful job should
be treated as a savepoint-backed migration.

Adding checkpoint storage is a good next production-hardening step, but it must
include the storage path, credentials, filesystem support, and restore test. For
an S3-compatible target, that usually means configuring Flink's filesystem
settings and making sure the image has the required plugin or connector.

Adding a real job should be done as infrastructure, not as an undocumented
manual submission. Declare the job through Pulumi, declare any Kafka topics it
needs through the Kafka stack, write down the checkpoint/savepoint behavior, and
preview the change before applying.

## Operational Checklist

For a quick health check:

```bash
cd pulumi/data/streaming/flink
NS="$(pulumi stack output --stack mx namespace)"
CLUSTER="$(pulumi stack output --stack mx clusterName)"
REST="$(pulumi stack output --stack mx restService)"

kubectl get flinkdeployments -n "$NS"
kubectl get pods,svc,endpoints,ingress -n "$NS"
kubectl describe flinkdeployment -n "$NS" "$CLUSTER"
kubectl describe svc -n "$NS" "$REST"
kubectl logs -n "$NS" deploy/flink-kubernetes-operator --tail=100
```

For a runtime check:

```bash
kubectl port-forward -n "$NS" "svc/$REST" 8081:8081
curl -s http://localhost:8081/overview
curl -s http://localhost:8081/taskmanagers
curl -s http://localhost:8081/jobs/overview
```

For a Kafka-backed job, check both sides:

```bash
kubectl get flinkdeployments -n flink
kubectl get kafka,kafkanodepool,kafkatopic -n kafka
kubectl get svc -n kafka kafka-kafka-bootstrap
```

Flink is healthy when the operator reconciles the `FlinkDeployment`, the
JobManager is reachable, TaskManagers register when jobs need capacity, slots
are available for the intended workload, jobs are in the expected state, and
checkpoint behavior matches the importance of the job. For this repo today, the
cluster is a session-cluster foundation. Production stream processing starts
when the job, topics, artifacts, checkpoints, savepoints, and restore procedure
are all repo-owned and tested.
