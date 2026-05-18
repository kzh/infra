# Kafka

Source: `pulumi/data/streaming/kafka`

Kafka is the event log for this repo. The useful way to think about it is not
"a queue with extra parts", but "a set of named, durable append-only logs that
many applications can read at their own pace."

A producer appends records to a topic. Kafka stores those records in partition
logs on disk. A consumer reads records by offset. Kafka does not remove a record
just because one consumer read it, so another consumer group can read the same
topic later, or the same group can replay from an older offset. That replay
property is the reason Kafka is useful for event-driven services, CDC-style
movement, audit trails, stream processing, and pipelines where "what happened"
matters more than a one-off request.

This stack is intentionally small. It gives the homelab a real Kafka control
plane and a working broker, but it is not a multi-broker, fault-tolerant Kafka
deployment yet. Read the single-broker details below before treating it as a
place for irreplaceable data.

## What This Stack Owns

The Pulumi project creates these pieces:

```text
Namespace:               kafka
Helm release:            strimzi
Helm chart:              strimzi-kafka-operator
Operator chart version:  1.0.0
Strimzi watch scope:     kafka namespace
Kafka cluster name:      kafka
Kafka version:           4.2.0
Kafka mode:              KRaft through KafkaNodePool, no ZooKeeper
Node pool name:          main
Node pool roles:         controller and broker
Node pool replicas:      1
Storage class:           local-path
Storage size:            20Gi
deleteClaim:             false
Internal plain listener: kafka-kafka-bootstrap:9092
Internal TLS listener:   kafka-kafka-bootstrap:9093
Tailnet listener:        enabled in mx config
Tailnet listener port:   9094
Smoke topic:             homelab-smoke
Smoke partitions:        1
Smoke replicas:          1
Auto-create topics:      disabled
```

The source of truth for those values is the Pulumi program and stack config:

- `pulumi/data/streaming/kafka/__main__.py` declares the Kubernetes namespace,
  Strimzi Helm release, `KafkaNodePool`, `Kafka`, and `KafkaTopic` resources.
- `pulumi/data/streaming/kafka/Pulumi.mx.yaml` sets the current `mx` values:
  namespace `kafka`, cluster name `kafka`, Kafka `4.2.0`, Strimzi chart
  `1.0.0`, `local-path` storage, and Tailscale listener settings.

The stack does not deploy Schema Registry, Kafka Connect, Kafka UI, MirrorMaker,
or a multi-broker cluster. It also does not create `KafkaUser` resources, SASL
users, ACLs, or an application-specific authorization model. The Strimzi User
Operator is enabled, so those can be added later, but they are not wired today.

## How Strimzi Fits

Kafka itself is the broker software. Strimzi is the Kubernetes operator that
turns Kubernetes custom resources into Kafka infrastructure.

Pulumi installs Strimzi with the `strimzi-kafka-operator` Helm chart. The Helm
release is named `strimzi`, and the chart is configured with:

```text
watchNamespaces:
  - kafka
```

That means the operator reconciles Strimzi resources in the Kafka namespace
rather than acting as an unconstrained cluster-wide reconciler for every
namespace. The chart also sets explicit resource requests and limits for the
operator:

```text
requests: 100m CPU, 384Mi memory
limits:   500m CPU, 384Mi memory
```

Pulumi then creates a `KafkaNodePool` named `main`. The node pool has one
replica and both KRaft roles:

```text
roles:
  - controller
  - broker
```

That is why this cluster has no ZooKeeper. KRaft stores Kafka metadata inside
Kafka itself. The node pool storage has `kraftMetadata: shared`, so metadata and
log data share the same persistent claim shape instead of using a separate
metadata-only volume.

The `Kafka` custom resource then defines the broker version, listeners, and a
few single-broker-safe Kafka defaults:

```text
auto.create.topics.enable: false
default.replication.factor: 1
min.insync.replicas: 1
offsets.topic.replication.factor: 1
transaction.state.log.replication.factor: 1
transaction.state.log.min.isr: 1
```

Those values are correct for a one-broker cluster. They are not the values you
would keep unchanged after adding more brokers. In a replicated cluster,
replication factor and minimum in-sync replicas become part of the durability
contract.

The Entity Operator is enabled with both the Topic Operator and the User
Operator:

```text
entityOperator:
  topicOperator: {}
  userOperator: {}
```

The Topic Operator is the important one today. It watches `KafkaTopic` resources
and reconciles them into real Kafka topics. The User Operator is available for
future `KafkaUser` resources, but this project does not currently declare any.

## The Event Log Model

A Kafka record is a key, value, timestamp, headers, and metadata appended to a
topic partition. A topic is a logical stream name, but the storage and ordering
unit is the partition.

Ordering is guaranteed inside one partition. Ordering is not guaranteed across
all partitions in a topic. If all events for `customer-123` must be processed in
order, producers should use a stable key such as `customer-123` so Kafka's
partitioner sends related records to the same partition.

Offsets are just positions in a partition log. A consumer stores "I have read
through offset N" for each assigned partition. That offset is per consumer
group, not global. One group can read near real time while another group starts
from the beginning for a backfill.

That gives Kafka its most important behavior:

- Adding another consumer to the same group spreads work across partitions.
- Adding another consumer group creates another independent view of the same
  event history.
- Replaying data usually means resetting or choosing offsets, not asking the
  producer to send old events again.
- Parallelism is bounded by partition count. A topic with one partition can
  only have one active consumer in a group for that topic.

The smoke topic in this repo has one partition and one replica. That makes it
simple and predictable for a first end-to-end test. For real topics, choose
partition count based on the required parallelism and expected key distribution.
Increasing partitions later is possible, but it can change key-to-partition
mapping and therefore ordering behavior for keyed streams. Decreasing partitions
is not a normal Kafka operation; plan topic shape before making it part of an
application contract.

## Internal Access

The stack always creates two internal listeners:

```text
plain: 9092, tls: false, type: internal
tls:   9093, tls: true,  type: internal
```

The Pulumi outputs are:

```text
bootstrapServers:    kafka-kafka-bootstrap:9092
tlsBootstrapServers: kafka-kafka-bootstrap:9093
```

Those are short Kubernetes service names. They work naturally for clients in the
`kafka` namespace. Clients in other namespaces should use the namespace-qualified
service name:

```text
kafka-kafka-bootstrap.kafka.svc.cluster.local:9092
kafka-kafka-bootstrap.kafka.svc.cluster.local:9093
```

Use the plain listener for basic internal smoke tests. The TLS listener is
available, but applications need to trust the Strimzi cluster CA before it is
useful. This Pulumi project does not export a ready-made truststore and does not
create application `KafkaUser` credentials.

## Tailnet Access

The `mx` stack config enables a third listener:

```text
name:          tailnet
type:          loadbalancer
class:         tailscale
port:          9094
tls:           false
bootstrap DNS: kafka
broker DNS:    kafka-0
```

This relies on the Tailscale Kubernetes operator and its LoadBalancer class
being available elsewhere in the cluster. The Kafka stack itself does not create
the Tailscale operator.

Kafka external access has one important rule: the bootstrap address is only the
first connection. After a client connects, Kafka returns broker metadata, and
the client opens connections to the advertised broker addresses. For this stack:

```text
tailnetBootstrapServers: kafka:9094
tailnetBroker:           kafka-0:9094
```

So an external tailnet client must be able to resolve and reach both the
bootstrap hostname and the broker hostname. If `kcat -L` can connect to the
bootstrap address but produce or consume fails afterward, inspect the advertised
broker address before assuming the topic or client library is broken.

The stack exports tailnet bootstrap values even though the listener is
conditional. Check `tailnetEnabled` first:

```bash
cd pulumi/data/streaming/kafka
pulumi stack output --stack mx tailnetEnabled
pulumi stack output --stack mx tailnetBootstrapServers
pulumi stack output --stack mx tailnetBroker
```

If `tailnetEnabled` is false, those host strings are only config-derived strings;
there is no external Kafka listener behind them.

## Producing And Consuming

Start by reading the non-secret outputs:

```bash
cd pulumi/data/streaming/kafka

NS="$(pulumi stack output --stack mx namespace)"
CLUSTER="$(pulumi stack output --stack mx clusterName)"
TOPIC="$(pulumi stack output --stack mx smokeTopic)"
INTERNAL_BOOTSTRAP="$(pulumi stack output --stack mx bootstrapServers)"
TAILNET_BOOTSTRAP="$(pulumi stack output --stack mx tailnetBootstrapServers)"
```

From inside the cluster, the least surprising test is to use the Kafka scripts
already present in the broker container. This does not require choosing a
separate toolbox image.

```bash
BROKER_POD="$(
  kubectl get pod -n "$NS" \
    -l strimzi.io/name="${CLUSTER}-kafka" \
    -o jsonpath='{.items[0].metadata.name}'
)"

printf 'hello from kafka\n' | kubectl exec -i -n "$NS" "$BROKER_POD" -c kafka -- \
  /opt/kafka/bin/kafka-console-producer.sh \
    --bootstrap-server "$INTERNAL_BOOTSTRAP" \
    --topic "$TOPIC"

kubectl exec -it -n "$NS" "$BROKER_POD" -c kafka -- \
  /opt/kafka/bin/kafka-console-consumer.sh \
    --bootstrap-server "$INTERNAL_BOOTSTRAP" \
    --topic "$TOPIC" \
    --from-beginning \
    --timeout-ms 10000
```

For an application pod outside the `kafka` namespace, use the qualified service
name instead of the short output:

```text
kafka-kafka-bootstrap.kafka.svc.cluster.local:9092
```

From a tailnet client with `kcat` installed:

```bash
printf 'hello from tailnet\n' | kcat \
  -b "$TAILNET_BOOTSTRAP" \
  -t "$TOPIC" \
  -P

kcat \
  -b "$TAILNET_BOOTSTRAP" \
  -t "$TOPIC" \
  -C \
  -o beginning \
  -e
```

To inspect what addresses Kafka is advertising to that client:

```bash
kcat -b "$TAILNET_BOOTSTRAP" -L
```

The metadata should show a reachable broker on the tailnet listener, currently
`kafka-0:9094` from the Pulumi wiring.

## Topics Are Infrastructure

Auto-create topics is disabled:

```text
auto.create.topics.enable: false
```

That is deliberate. A topic is an application contract, not just a string in a
producer config. Its name, partition count, replication factor, cleanup policy,
retention, and compaction behavior affect correctness and operations.

The current stack creates one `KafkaTopic`:

```text
metadata.name: homelab-smoke
spec.partitions: 1
spec.replicas: 1
```

For a durable application topic, add another `KafkaTopic` resource in
`pulumi/data/streaming/kafka/__main__.py` and let Pulumi manage it. Follow the
same pattern as the smoke topic:

```python
orders_topic = k8s.apiextensions.CustomResource(
    "kafka-orders-topic",
    api_version="kafka.strimzi.io/v1",
    kind="KafkaTopic",
    metadata={
        "name": "orders-events",
        "namespace": namespace_name,
        "labels": {
            "strimzi.io/cluster": cluster_name,
        },
    },
    spec={
        "partitions": 3,
        "replicas": 1,
        "config": {
            "retention.ms": "604800000",
            "cleanup.policy": "delete",
        },
    },
    opts=pulumi.ResourceOptions(depends_on=[kafka_cluster]),
)
```

Keep `replicas: 1` while the cluster has one broker. Raising a topic replication
factor above the available broker count will not make the data safer; it will
make the topic impossible to place correctly.

If you need a Kafka topic name that is not a valid Kubernetes object name, use a
valid Kubernetes `metadata.name` and set the actual Kafka topic name through the
Strimzi topic spec. Keep that distinction explicit in code comments because it
is easy to confuse the Kubernetes object identity with the Kafka topic identity.

Do not create long-lived topics by hand with `kafka-topics.sh` or `kubectl edit`.
The Topic Operator and Pulumi should be the steady-state source of truth. Manual
commands are useful for inspection and emergency diagnosis; repo-backed Pulumi
changes are the durable path.

## Consumer Groups

Consumer groups are Kafka's unit of shared consumption. Every consumer with the
same group id cooperates with the others in that group. Kafka assigns partitions
to group members, and each group stores its committed offsets separately.

This means:

- Two instances of the same worker should usually use the same group id.
- Two different applications should usually use different group ids.
- A replay or backfill job should usually use its own group id or explicitly
  reset offsets.
- With the current one-partition smoke topic, only one consumer in a group can
  actively read that topic at a time.

Consumer group offsets live in Kafka's internal offsets topic. This repo sets
`offsets.topic.replication.factor` to `1` because there is only one broker. If
the broker storage is lost, committed offsets are part of what can be lost too.

Useful inspection commands:

```bash
kubectl exec -it -n "$NS" "$BROKER_POD" -c kafka -- \
  /opt/kafka/bin/kafka-consumer-groups.sh \
    --bootstrap-server "$INTERNAL_BOOTSTRAP" \
    --list

kubectl exec -it -n "$NS" "$BROKER_POD" -c kafka -- \
  /opt/kafka/bin/kafka-consumer-groups.sh \
    --bootstrap-server "$INTERNAL_BOOTSTRAP" \
    --describe \
    --group <group-id>
```

Avoid resetting offsets for an application group until you know what the
application will do with replayed records. Replays are powerful, but they can
also duplicate side effects if the consumer was not written to be idempotent.

## Storage And Durability

The node pool storage is:

```text
type:          persistent-claim
class:         local-path
size:          20Gi
deleteClaim:   false
kraftMetadata: shared
```

The important consequences are practical:

- Kafka data is persisted to a PVC rather than stored only in the pod filesystem.
- `deleteClaim: false` means Strimzi should not delete the PVC just because the
  Kafka cluster object is deleted.
- `local-path` ties the data to local node storage behavior. This is good enough
  for a homelab path, but it is not the same as networked replicated storage.
- There is one broker and topic replication factor is one, so Kafka has no
  second broker copy of topic data.

Use this cluster as a real event log, but do not confuse "persistent volume" with
"high availability." A single broker can persist through pod restarts and still
be vulnerable to node loss, disk loss, or a bad storage migration.

Check storage with:

```bash
kubectl get pvc -n "$NS"
kubectl get pods -n "$NS" -l strimzi.io/name="${CLUSTER}-kafka" -o wide
kubectl describe kafkanodepool -n "$NS" main
```

The stack does not set topic retention or compaction defaults beyond Kafka's
own defaults. For topics where retention matters, declare it explicitly in the
`KafkaTopic` `spec.config` so the retention policy is visible in code review.

## Schemas And Payload Compatibility

Kafka does not understand the meaning of your record payload. It stores bytes.
The broker can retain, replicate, compact, and deliver records, but it does not
know whether a JSON field was removed safely or whether an Avro schema stayed
backward compatible.

This stack does not deploy Schema Registry. That means schema compatibility is
currently an application discipline:

- Version event payloads intentionally.
- Prefer additive changes for shared events.
- Keep consumers tolerant of unknown fields.
- Do not reuse one topic for unrelated event shapes.
- Document the owner and compatibility promise for every durable topic.

If schema enforcement becomes important, add it as an explicit stack or service
instead of treating Kafka itself as the schema authority.

Topic-level changes are also compatibility changes. For example:

- Increasing partitions can change keyed ordering for new records.
- Changing `cleanup.policy` from delete to compact changes how history behaves.
- Lowering retention can remove data consumers expected to replay.
- Renaming a topic is a migration, not a cosmetic cleanup.
- Raising replication factor requires enough brokers to host the replicas.

For application-facing topics, prefer a small migration note in the same PR as
the Pulumi change: who produces, who consumes, whether old consumers still work,
and how replay/backfill should behave.

## Debugging

Start by separating four layers:

1. Pulumi declared the resource.
2. Strimzi reconciled the custom resource.
3. Kubernetes scheduled and exposed the pods/services.
4. Kafka clients can reach the advertised broker and read/write the topic.

Useful commands:

```bash
cd pulumi/data/streaming/kafka

NS="$(pulumi stack output --stack mx namespace)"
CLUSTER="$(pulumi stack output --stack mx clusterName)"
TOPIC="$(pulumi stack output --stack mx smokeTopic)"

kubectl get kafka,kafkanodepool,kafkatopic -n "$NS"
kubectl describe kafka -n "$NS" "$CLUSTER"
kubectl describe kafkanodepool -n "$NS" main
kubectl describe kafkatopic -n "$NS" "$TOPIC"

kubectl get pods,svc,pvc -n "$NS"
kubectl logs -n "$NS" deploy/strimzi-cluster-operator --tail=200
```

If a topic is missing, check whether it exists as a `KafkaTopic` with the
`strimzi.io/cluster: kafka` label. Because auto-create is disabled, a producer
typo should fail instead of silently creating a new topic.

If a consumer reads nothing, check:

- Was a message produced to the same topic?
- Is the consumer starting at the end rather than the beginning?
- Is it using a group id that already committed offsets?
- Does the topic have more partitions than the number of active consumers in
  that group?

If internal clients fail, check the broker readiness and the internal bootstrap
service first:

```bash
kubectl get svc -n "$NS" "${CLUSTER}-kafka-bootstrap"
kubectl get endpoints -n "$NS" "${CLUSTER}-kafka-bootstrap"
```

If tailnet clients fail, check advertised metadata:

```bash
kcat -b "$(pulumi stack output --stack mx tailnetBootstrapServers)" -L
```

Bootstrap success followed by produce/consume failure usually points to a broker
advertised address that the client cannot resolve or reach. In this stack, the
broker advertised address is controlled by:

```text
kafka:tailnetAdvertisedBrokerHost
kafka:tailnetBrokerHostname
kafka:tailnetPort
```

The current `mx` config sets the broker hostname to `kafka-0` and leaves the
advertised broker host at its default, which is also `kafka-0`.

If pods look healthy but writes fail, inspect broker logs and PVC state:

```bash
BROKER_POD="$(
  kubectl get pod -n "$NS" \
    -l strimzi.io/name="${CLUSTER}-kafka" \
    -o jsonpath='{.items[0].metadata.name}'
)"

kubectl logs -n "$NS" "$BROKER_POD" -c kafka --tail=200
kubectl get pvc -n "$NS"
kubectl describe pod -n "$NS" "$BROKER_POD"
```

Do not paste full logs into commits or docs. Summarize the condition and keep
secret-bearing or environment-specific values out of repository text.

## Safe Changes

For docs-only work, no Pulumi preview is required. For code or config changes to
this stack, use the repo wrappers:

```bash
just sync pulumi/data/streaming/kafka
just check-python
just lint
git diff --check
just preview pulumi/data/streaming/kafka stack=mx
```

Do not run `pulumi up`, `pulumi destroy`, or `just up` unless the user has
explicitly asked for an apply or destructive action.

Safe Kafka changes are usually additive:

- Add a new `KafkaTopic` for a new stream.
- Add explicit topic config such as retention for a topic that already has an
  agreed contract.
- Add application-specific documentation around producers, consumers, and
  consumer groups.

Changes that need extra care:

- Changing listener names, ports, hostnames, or advertised broker hosts. Kafka
  clients cache and depend on advertised metadata.
- Changing `clusterName`, node pool name, or Kubernetes object names. Renames
  can imply replacement or migration work.
- Changing storage class, storage size, or `deleteClaim`.
- Changing Kafka version or Strimzi chart version.
- Increasing partition count for a topic with keyed ordering assumptions.
- Changing topic retention or compaction policy.
- Moving from one broker to multiple brokers.

When changing topics, remember that Pulumi and Strimzi reconcile the desired
state. If you make an emergency manual topic change, follow up with a Pulumi
change or expect the repo and cluster to drift.

## Upgrade Notes

There are two separate version surfaces:

```text
kafka:operatorChartVersion -> Strimzi Helm chart version
kafka:kafkaVersion         -> Kafka broker version
```

Do not treat either as an unchecked bump. Check that the chosen Strimzi operator
version supports the chosen Kafka version, then preview the stack before any
apply. The repo does not encode a compatibility matrix for you.

Because this deployment has one broker, a broker restart is service-impacting.
There is no second broker to keep partitions available during a rolling upgrade.
Before an upgrade, produce and consume a smoke message, record the current
client path you care about, and check PVC health. After an upgrade, repeat the
same internal and tailnet smoke tests.

A future move to a more durable Kafka shape should be planned as a migration:

- Increase node pool replicas.
- Revisit broker storage and node placement.
- Raise internal topic replication factors.
- Raise durable topic replication factors.
- Set `min.insync.replicas` for the desired write durability.
- Decide whether tailnet exposure should remain per-broker or move behind a
  different client access pattern.

That is a real design change, not just "replicas: 3." Kafka durability comes
from broker count, replica placement, producer acks, in-sync replica settings,
and storage behavior all agreeing with each other.

## Operational Baseline

A healthy baseline for this repo looks like:

- The Strimzi operator deployment is running in `kafka`.
- The `Kafka` resource named `kafka` is ready.
- The `KafkaNodePool` named `main` is ready.
- The broker pod is running and has its PVC bound.
- The internal bootstrap service has endpoints.
- The `homelab-smoke` `KafkaTopic` is ready.
- Internal produce/consume works through `kafka-kafka-bootstrap:9092`.
- If tailnet access matters, `kcat -L` through `kafka:9094` advertises a
  reachable `kafka-0:9094` broker.

When those all hold, the stack is doing what the current Pulumi wiring says it
should do.
