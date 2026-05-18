# Streaming

Streaming is the part of the data platform that deals with events before they
settle into a database, table, object store, or dashboard. The important word is
not "fast"; it is "continuous." A streaming system lets producers publish facts
as they happen, lets consumers read those facts independently, and gives stream
processors a way to keep useful state while new records keep arriving.

In this repo, the streaming area has three different kinds of machinery.
[Kafka](/stacks/data/streaming/kafka) is the currently usable durable event log,
managed by Strimzi. [Flink](/stacks/data/streaming/flink) is the stream
processing engine, managed by the Apache Flink Kubernetes Operator.
[Redpanda](/stacks/data/streaming/redpanda) currently installs the Redpanda
operator layer, not a broker cluster. Those are related pieces, but they are not
interchangeable: logs store streams, processors compute over streams, and
operators reconcile Kubernetes custom resources into running systems.

That distinction is the fastest way to debug this layer. If producers cannot
write or consumers cannot read, start with the log: listener addresses, topic
existence, partition health, credentials if enabled, and client reachability. If
a long-running computation restarts, emits duplicates, or loses its place, start
with the processor: job status, checkpoints, savepoints, source offsets, sink
semantics, and state backend. If a custom resource is not turning into pods,
services, or topics, start with the operator: CRD status, reconciliation errors,
and controller logs.

## Logs, Queues, And Streams

Kafka-style systems are durable append logs. Producers append records to a topic.
The topic is split into partitions, and each partition is an ordered sequence of
records with monotonically increasing offsets. Kafka keeps those records for a
retention period or according to compaction rules. Consumers do not remove
records when they read them; they record their position and can come back later.

That model differs from a traditional work queue. In a queue, one worker usually
takes a message and the queue considers it handled. In Kafka, many independent
consumer groups can read the same topic without interfering with each other. One
group can power a live service, another can feed a warehouse, and a third can be
created temporarily to replay history for a migration or investigation.

It also differs from stream processing. Kafka stores the stream and provides
replay. Flink reads streams, keeps state, applies event-time logic, and writes
derived results. A common path looks like this:

```text
service emits domain events
Kafka stores the events in durable topics
Flink reads one or more topics
Flink keeps keyed state, windows, joins, and timers
Flink writes results to another topic, database, or object store
```

Keeping those jobs separate is useful operationally. If a Flink job is down,
Kafka can keep retaining input records while the job is fixed, provided
retention is long enough. If a producer deploy goes wrong, Flink can be paused
or restarted without changing the source topic. The log is the boundary between
systems.

## Operators Are Control Planes

The Kubernetes operators in this area are controllers, not the data path
themselves. Strimzi watches `Kafka`, `KafkaNodePool`, and `KafkaTopic` resources
and reconciles brokers, listeners, and topics. The Flink operator watches
`FlinkDeployment` and related resources and reconciles JobManagers,
TaskManagers, services, and job state. The Redpanda operator is installed so a
future Redpanda cluster can be declared, but this repo does not currently create
that broker data plane.

This matters because operator health and application health are different
questions. A healthy operator can be managing an unhealthy broker, and an
unhealthy operator can leave an already-running broker serving traffic for a
while. When something fails, inspect both the custom resource status and the pods
that do the actual work.

Useful first checks:

```bash
kubectl get kafka,kafkanodepool,kafkatopic -n kafka
kubectl get flinkdeployments -n flink
kubectl get pods,svc,pvc -n kafka
kubectl get pods,svc,ingress -n flink
kubectl get pods,svc,deploy -n redpanda
kubectl get events -n kafka --sort-by=.lastTimestamp
kubectl get events -n flink --sort-by=.lastTimestamp
```

For repo-backed changes, preview the owning stack before applying anything:

```bash
just preview pulumi/data/streaming/kafka stack=mx
just preview pulumi/data/streaming/flink stack=mx
just preview pulumi/data/streaming/redpanda stack=mx
```

Do not treat a manual `kubectl` edit as the final fix for a Pulumi-owned
resource. It may be useful for diagnosis, but durable behavior belongs in the
owning Pulumi project.

## Offsets Are Positions, Not Messages

An offset is a position within one topic partition. It is not globally unique
across a topic, and it is not the same thing as a timestamp or event ID. The
pair that identifies a record position is the partition plus the offset.
Ordering is guaranteed within one partition, not across all partitions in a
topic.

Consumers decide where to start. A new consumer group with `auto.offset.reset`
set to `latest` starts at the end and sees only new records. A new group with
`earliest` can replay retained records from the beginning. An existing group
usually resumes from committed offsets. That means "consumer reads nothing" can
mean several different things:

```text
no records were produced to this topic
records went to a different topic or cluster
the group already committed past those records
the group started at the end of the log
retention deleted the records
the consumer is assigned partitions that are currently empty
deserialization failed before records reached application code
```

For manual tests, use an explicit throwaway group and an explicit start position.
With Kafka CLI tooling, the checks usually look like this:

```bash
kubectl run kafka-admin -n kafka --rm -it --restart=Never \
  --image=quay.io/strimzi/kafka:latest-kafka-4.2.0 -- \
  bin/kafka-consumer-groups.sh \
    --bootstrap-server kafka-kafka-bootstrap:9092 \
    --describe \
    --all-groups

kubectl run kafka-consumer -n kafka --rm -it --restart=Never \
  --image=quay.io/strimzi/kafka:latest-kafka-4.2.0 -- \
  bin/kafka-console-consumer.sh \
    --bootstrap-server kafka-kafka-bootstrap:9092 \
    --topic homelab-smoke \
    --group docs-smoke-$(date +%s) \
    --from-beginning \
    --timeout-ms 10000
```

Be careful with offset resets. Resetting a consumer group's offsets changes what
the application will process next. For ordinary services, that can replay old
events or skip events. For a Flink job, manual Kafka group resets can conflict
with checkpointed source state; the job may restore offsets from its checkpoint
instead of the values you changed in Kafka. Treat offset resets as a recovery or
migration operation, not routine debugging.

## Consumer Groups Control Parallelism And Independence

A consumer group is a named set of consumers that share work. Kafka assigns each
partition to at most one active consumer within a group. If a topic has one
partition, only one consumer in the group can do useful read work for that topic.
If a topic has eight partitions, up to eight consumers in the same group can read
in parallel.

Different groups are independent. A `billing-service` group and an
`analytics-loader` group can read the same topic at different speeds and commit
different offsets. This is why group names are part of the application contract.
Changing a group name creates a new reader identity. That may be correct for a
new service or a deliberate replay, but it is not a harmless rename.

Consumer lag is the distance between the end of the log and the group's
committed position. Some lag is normal during bursts. Persistent lag means the
consumer cannot keep up, is repeatedly failing, or is blocked on downstream
work. Before adding partitions or replicas, identify which part is slow:

```text
broker cannot accept writes fast enough
consumer has too little parallelism
consumer processing is slow
sink writes are slow
records are repeatedly failing and being retried
one hot key is pinning work to a single partition
```

Partition keys deserve care. Related records that must be ordered together
should use a stable key so they land on the same partition. Spreading records
across more partitions can raise throughput, but it can also change ordering and
load distribution. Increasing partition count is usually possible; decreasing it
is effectively a new-topic migration.

## Checkpoints Are The Processor's Memory

Flink jobs can keep state: counts, windows, joins, timers, deduplication sets,
and other keyed data that evolves as records arrive. Checkpoints are consistent
snapshots of that state. When a job restarts, it can restore from the latest
checkpoint and continue without starting from empty state.

For Kafka sources, Flink checkpoints also include source position. That is why
source offsets should be thought of as part of Flink recovery, not only Kafka
consumer-group metadata. If the job has a valid checkpoint, it can resume from
the source offsets captured in that checkpoint. If checkpointing is not durable
or not enabled, a restart can fall back to connector defaults and produce
surprising replay or skip behavior.

A savepoint is a deliberate snapshot taken for planned work: changing job code,
upgrading Flink, moving state, or migrating a pipeline. Checkpoints are routine
recovery points; savepoints are migration handles. For any important stateful
job, write down:

```text
where checkpoints live
how often checkpoints run
how long completed checkpoints are retained
how savepoints are taken before upgrades
how source offsets are restored
whether sinks are idempotent or transactional
how failed records are handled
```

The current Flink stack creates a session cluster foundation and does not define
a production checkpoint store for arbitrary jobs. That is acceptable for
experiments and control-plane validation, but a durable stateful pipeline needs
checkpoint storage, artifact ownership, and a tested restore path before it
becomes operationally important.

## Delivery Guarantees Need Both Source And Sink Discipline

"At least once" usually means records will not be lost if the system can recover,
but duplicates are possible. "Exactly once" requires more than a checkbox; the
source, processor, checkpointing, and sink all have to cooperate. Kafka plus
Flink can support strong guarantees in the right design, but a database sink
that unconditionally inserts rows can still duplicate output after a retry.

Design consumers and sinks so reprocessing is acceptable. Prefer stable event
IDs, idempotent writes, deterministic upserts, compacted output topics for latest
state, or transactional sink support where it is available. If a pipeline cannot
tolerate duplicate input, that constraint should be visible in its topic,
schema, and sink design from the start.

Dead-letter topics are useful when a small number of records cannot be decoded
or processed. They are not a substitute for compatibility. A dead-letter path
should preserve enough context to repair or replay the record later: original
topic, partition, offset, key, timestamp, error category, and the original
payload if it is safe to store.

## Schemas And Topics Are Contracts

Topic names, partition counts, key choices, retention, compaction, and event
schemas are API contracts. Kubernetes may accept a change while consumers still
break at runtime. Treat stream changes like migrations.

Safe schema evolution usually means adding optional fields, adding fields with
defaults, or widening a value in a way older readers can ignore. Risky changes
include renaming fields, removing fields, changing meaning while keeping the same
name, changing key shape, narrowing numeric types, changing timestamp semantics,
or switching serialization formats in place. If the repo later adds a schema
registry, compatibility checks should become part of the deployment path. Until
then, compatibility is a code-review responsibility.

Topic changes have their own hazards:

```text
renaming a topic creates a new stream, not a rename of history
deleting a topic deletes retained records
raising partition count can change key distribution
lowering partition count requires a migration to a new topic
shortening retention can remove data needed for replay
switching cleanup policy between delete and compact changes log semantics
changing the event key can break ordering and compaction assumptions
changing replication or storage affects durability and recovery
```

For meaningful changes, prefer an explicit migration path. Create a new topic
when the contract really changes. Dual-write or bridge from old to new when
needed. Backfill from the retained log if retention covers the migration window.
Move one consumer group at a time. Keep the old topic until lag is drained and
rollback is no longer needed. Document when it is safe to remove the old path.

## A Safe Change Checklist

Before changing a streaming contract, answer these questions in the pull request
or adjacent docs:

```text
which producers write this topic?
which consumer groups read it?
does any Flink job checkpoint offsets or state for it?
what ordering does the key provide?
what retention or compaction behavior do consumers rely on?
is the schema change backward-compatible for existing readers?
is it forward-compatible for old producers during rollout?
does the change require a savepoint, backfill, or dual-write period?
how will lag, failed records, and replay be observed after rollout?
what is the rollback path if consumers reject the new records?
```

For Flink jobs, add a processor-specific set of checks:

```text
take a savepoint before changing stateful code
verify the new job can restore from that savepoint
keep operator UIDs stable when state must be preserved
avoid changing keyBy logic without a state migration plan
confirm checkpoint storage survives pod replacement
confirm sink writes remain idempotent or transactional after replay
```

For Kafka topics, add a log-specific set of checks:

```text
declare durable topics through Pulumi
preview Strimzi topic changes before apply
avoid producer-side auto-creation as a control path
confirm partition count and key choice match expected parallelism
validate internal and tailnet listener addresses separately if both matter
produce and consume a smoke record after listener or topic changes
```

## Reading The Current State

The sibling pages are the service-specific runbooks:

- [Kafka](/stacks/data/streaming/kafka) has the current broker shape, listener
  model, smoke topic, produce/consume commands, and topic-management notes.
- [Flink](/stacks/data/streaming/flink) has the session cluster shape, UI access,
  job submission notes, and checkpoint warnings.
- [Redpanda](/stacks/data/streaming/redpanda) explains the current operator-only
  state and what would need to exist before Redpanda is a usable broker path.

For a broad streaming read, start with the data path and then move to the
control plane:

```bash
kubectl get pods,svc,pvc -A | rg 'kafka|strimzi|flink|redpanda'
kubectl get kafka,kafkanodepool,kafkatopic -n kafka
kubectl get flinkdeployments -n flink
kubectl get crds | rg 'kafka|flink|redpanda'
kubectl logs -n kafka deploy/strimzi-cluster-operator --tail=200
kubectl logs -n flink deploy/flink-kubernetes-operator --tail=200
kubectl logs -n redpanda deploy/redpanda-controller-operator --tail=200
```

Then test the actual client path that matters. An in-cluster Kafka client should
use the internal bootstrap service. A private network client should use the
tailnet listener from the Kafka stack outputs. A Flink issue should be checked
in the Flink UI as well as Kubernetes, because the UI exposes job exceptions,
checkpoint state, restarts, backpressure, and TaskManager availability.

The central rule is simple: preserve the log, preserve state, and make contract
changes deliberately. Streaming systems are forgiving when replay and checkpoints
are intact. They become hard to reason about when topic history, consumer
offsets, processor state, and event schemas are changed independently.
