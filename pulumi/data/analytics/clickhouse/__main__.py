import pulumi
import pulumi_kubernetes as kubernetes

# Create namespace
namespace = kubernetes.core.v1.Namespace(
    "namespace",
    metadata=kubernetes.meta.v1.ObjectMetaArgs(
        name="clickhouse"
    )
)

# Deploy ClickHouse using Helm
clickhouse = kubernetes.helm.v3.Release(
    "clickhouse",
    chart="oci://registry-1.docker.io/bitnamicharts/clickhouse",
    version="8.0.5",
    namespace=namespace.metadata.name,
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