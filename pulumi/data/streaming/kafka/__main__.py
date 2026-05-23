from pathlib import Path

import pulumi_kubernetes as k8s
from infra_helpers.grafana import dashboard_config_maps
from pulumi_monitoring_crds.monitoring.v1 import PodMonitor

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
monitoring_release_label = config.get("monitoringReleaseLabel", "kube-prometheus-stack")
dashboards_dir = Path(__file__).resolve().parent / "dashboards"
dashboard_files = [
    "kafka-overview.json",
]

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

kafka_metrics = k8s.core.v1.ConfigMap(
    "kafka-metrics",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="kafka-metrics",
        namespace=kafka_namespace.metadata.name,
        labels=labels,
    ),
    data={
        "kafka-metrics-config.yml": """
lowercaseOutputName: true
rules:
  - pattern: 'kafka.server<type=BrokerTopicMetrics, name=(MessagesInPerSec|BytesInPerSec|BytesOutPerSec)><>Count'
    name: kafka_server_brokertopicmetrics_$1_total
    type: COUNTER
  - pattern: 'kafka.server<type=ReplicaManager, name=(UnderReplicatedPartitions|UnderMinIsrPartitionCount)><>Value'
    name: kafka_server_replicamanager_$1
    type: GAUGE
  - pattern: 'kafka.controller<type=KafkaController, name=ActiveControllerCount><>Value'
    name: kafka_controller_kafkacontroller_activecontrollercount
    type: GAUGE
""".lstrip(),
    },
    opts=pulumi.ResourceOptions(depends_on=[kafka_namespace]),
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
                "memory": "512Mi",
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
        "resources": {
            "requests": {
                "cpu": "250m",
                "memory": "768Mi",
            },
            "limits": {
                "cpu": "1",
                "memory": "1536Mi",
            },
        },
        "jvmOptions": {
            "-Xms": "384m",
            "-Xmx": "768m",
        },
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
            "metricsConfig": {
                "type": "jmxPrometheusExporter",
                "valueFrom": {
                    "configMapKeyRef": {
                        "name": kafka_metrics.metadata.name,
                        "key": "kafka-metrics-config.yml",
                    },
                },
            },
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
            "topicOperator": {
                "resources": {
                    "requests": {
                        "cpu": "50m",
                        "memory": "128Mi",
                    },
                    "limits": {
                        "cpu": "250m",
                        "memory": "384Mi",
                    },
                },
            },
            "userOperator": {
                "resources": {
                    "requests": {
                        "cpu": "50m",
                        "memory": "128Mi",
                    },
                    "limits": {
                        "cpu": "250m",
                        "memory": "384Mi",
                    },
                },
            },
        },
    },
    opts=pulumi.ResourceOptions(depends_on=[kafka_node_pool, kafka_metrics]),
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

strimzi_operator_podmonitor = PodMonitor(
    "strimzi-operator-podmonitor",
    metadata={
        "name": "strimzi-operator",
        "namespace": namespace_name,
        "labels": {
            "release": monitoring_release_label,
        },
    },
    spec={
        "selector": {
            "matchLabels": {
                "strimzi.io/kind": "cluster-operator",
            },
        },
        "podMetricsEndpoints": [
            {
                "port": "http",
                "path": "/metrics",
                "interval": "30s",
            },
        ],
    },
    opts=pulumi.ResourceOptions(depends_on=[strimzi_operator]),
)

kafka_podmonitor = PodMonitor(
    "kafka-podmonitor",
    metadata={
        "name": "kafka-brokers",
        "namespace": namespace_name,
        "labels": {
            "release": monitoring_release_label,
        },
    },
    spec={
        "selector": {
            "matchLabels": {
                "strimzi.io/cluster": cluster_name,
                "strimzi.io/name": f"{cluster_name}-kafka",
            },
        },
        "podMetricsEndpoints": [
            {
                "port": "tcp-prometheus",
                "path": "/metrics",
                "interval": "30s",
            },
        ],
    },
    opts=pulumi.ResourceOptions(depends_on=[kafka_cluster]),
)

dashboard_config_maps(
    name_prefix="kafka-dashboard",
    namespace=kafka_namespace.metadata.name,
    dashboards_dir=dashboards_dir,
    dashboard_files=dashboard_files,
    labels=labels,
    opts=pulumi.ResourceOptions(
        depends_on=[strimzi_operator_podmonitor, kafka_podmonitor]
    ),
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
