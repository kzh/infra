import pulumi_kubernetes as k8s

import pulumi

config = pulumi.Config()

namespace_name = config.get("namespace", "kafka")
operator_chart_version = config.get("operatorChartVersion", "1.0.0")
cluster_name = config.get("clusterName", "kafka")
kafka_version = config.get("kafkaVersion", "4.2.0")
storage_class_name = config.get("storageClassName", "local-path")
storage_size = config.get("storageSize", "20Gi")
delete_claim = config.get_bool("deleteClaim")
if delete_claim is None:
    delete_claim = False
topic_name = config.get("topicName", "homelab-smoke")
topic_partitions = config.get_int("topicPartitions")
if topic_partitions is None:
    topic_partitions = 1
topic_replicas = config.get_int("topicReplicas")
if topic_replicas is None:
    topic_replicas = 1
tailnet_enabled = config.get_bool("tailnetEnabled")
if tailnet_enabled is None:
    tailnet_enabled = True
tailnet_listener_name = config.get("tailnetListenerName", "tailnet")
tailnet_port = config.get_int("tailnetPort")
if tailnet_port is None:
    tailnet_port = 9094
tailnet_bootstrap_hostname = config.get("tailnetBootstrapHostname", cluster_name)
tailnet_broker_hostname = config.get("tailnetBrokerHostname", f"{cluster_name}-0")
tailnet_advertised_broker_host = config.get(
    "tailnetAdvertisedBrokerHost",
    tailnet_broker_hostname,
)

labels = {
    "app.kubernetes.io/name": "kafka",
    "app.kubernetes.io/part-of": "kafka",
}

kafka_listeners = [
    {
        "name": "plain",
        "port": 9092,
        "type": "internal",
        "tls": False,
    },
    {
        "name": "tls",
        "port": 9093,
        "type": "internal",
        "tls": True,
    },
]

if tailnet_enabled:
    kafka_listeners.append(
        {
            "name": tailnet_listener_name,
            "port": tailnet_port,
            "type": "loadbalancer",
            "tls": False,
            "configuration": {
                "class": "tailscale",
                "bootstrap": {
                    "annotations": {
                        "tailscale.com/hostname": tailnet_bootstrap_hostname,
                    },
                },
                "brokers": [
                    {
                        "broker": 0,
                        "advertisedHost": tailnet_advertised_broker_host,
                        "advertisedPort": tailnet_port,
                        "annotations": {
                            "tailscale.com/hostname": tailnet_broker_hostname,
                        },
                    }
                ],
            },
        }
    )

kafka_namespace = k8s.core.v1.Namespace(
    "kafka-namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=namespace_name,
        labels=labels,
    ),
)

strimzi_operator = k8s.helm.v3.Release(
    "strimzi",
    chart="strimzi-kafka-operator",
    name="strimzi",
    namespace=kafka_namespace.metadata.name,
    repository_opts=k8s.helm.v3.RepositoryOptsArgs(
        repo="https://strimzi.io/charts/",
    ),
    version=operator_chart_version,
    values={
        "watchNamespaces": [namespace_name],
        "resources": {
            "requests": {
                "cpu": "100m",
                "memory": "384Mi",
            },
            "limits": {
                "cpu": "500m",
                "memory": "384Mi",
            },
        },
    },
    opts=pulumi.ResourceOptions(depends_on=[kafka_namespace]),
)

kafka_node_pool = k8s.apiextensions.CustomResource(
    "kafka-node-pool",
    api_version="kafka.strimzi.io/v1",
    kind="KafkaNodePool",
    metadata={
        "name": "main",
        "namespace": namespace_name,
        "labels": {
            "strimzi.io/cluster": cluster_name,
        },
    },
    spec={
        "replicas": 1,
        "roles": ["controller", "broker"],
        "storage": {
            "type": "persistent-claim",
            "size": storage_size,
            "class": storage_class_name,
            "deleteClaim": delete_claim,
            "kraftMetadata": "shared",
        },
    },
    opts=pulumi.ResourceOptions(depends_on=[strimzi_operator]),
)

kafka_cluster = k8s.apiextensions.CustomResource(
    "kafka-cluster",
    api_version="kafka.strimzi.io/v1",
    kind="Kafka",
    metadata={
        "name": cluster_name,
        "namespace": namespace_name,
        "labels": labels,
    },
    spec={
        "kafka": {
            "version": kafka_version,
            "listeners": kafka_listeners,
            "config": {
                "auto.create.topics.enable": "false",
                "default.replication.factor": 1,
                "min.insync.replicas": 1,
                "offsets.topic.replication.factor": 1,
                "transaction.state.log.min.isr": 1,
                "transaction.state.log.replication.factor": 1,
            },
        },
        "entityOperator": {
            "topicOperator": {},
            "userOperator": {},
        },
    },
    opts=pulumi.ResourceOptions(depends_on=[kafka_node_pool]),
)

smoke_topic = k8s.apiextensions.CustomResource(
    "kafka-smoke-topic",
    api_version="kafka.strimzi.io/v1",
    kind="KafkaTopic",
    metadata={
        "name": topic_name,
        "namespace": namespace_name,
        "labels": {
            "strimzi.io/cluster": cluster_name,
        },
    },
    spec={
        "partitions": topic_partitions,
        "replicas": topic_replicas,
    },
    opts=pulumi.ResourceOptions(depends_on=[kafka_cluster]),
)

pulumi.export("namespace", kafka_namespace.metadata.name)
pulumi.export("operatorChartVersion", operator_chart_version)
pulumi.export("clusterName", cluster_name)
pulumi.export("kafkaVersion", kafka_version)
pulumi.export("bootstrapServers", f"{cluster_name}-kafka-bootstrap:9092")
pulumi.export("tlsBootstrapServers", f"{cluster_name}-kafka-bootstrap:9093")
pulumi.export("tailnetEnabled", tailnet_enabled)
pulumi.export("tailnetBootstrapServers", f"{tailnet_bootstrap_hostname}:{tailnet_port}")
pulumi.export("tailnetBroker", f"{tailnet_advertised_broker_host}:{tailnet_port}")
pulumi.export("nodePool", kafka_node_pool.metadata["name"])
pulumi.export("storageClassName", storage_class_name)
pulumi.export("storageSize", storage_size)
pulumi.export("deleteClaim", delete_claim)
pulumi.export("smokeTopic", smoke_topic.metadata["name"])
