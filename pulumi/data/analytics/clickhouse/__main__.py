import pulumi
import pulumi_kubernetes as k8s

# Create namespace
clickhouse_namespace = k8s.core.v1.Namespace(
    "clickhouse-namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="clickhouse"
    )
)

# Deploy ClickHouse using Helm
clickhouse_chart = k8s.helm.v4.Chart(
    "clickhouse",
    chart="oci://registry-1.docker.io/bitnamicharts/clickhouse",
    namespace=clickhouse_namespace.metadata.name,
    version="8.0.5",
    values={
        "auth": {
            "username": "admin"
        },
        "zookeeper": {
            "enabled": False,
        },
        "ingress": {
            "enabled": False,
        },
        "service": {
            "annotations": {
                "tailscale.com/expose": "true",
                "tailscale.com/hostname": "clickhouse"
            }
        },
        "persistence": {
            "storageClass": "local-path",
            "size": "100Gi",
        },
        "resourcesPreset": "none",
        "shards": 1,
        "replicaCount": 1
    }
)