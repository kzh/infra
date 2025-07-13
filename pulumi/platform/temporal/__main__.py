import pulumi
import pulumi_kubernetes as k8s

config = pulumi.Config()
namespace = k8s.core.v1.Namespace(
    "namespace",
    metadata={
        "name": config.require("namespace"),
    },
)

chart = k8s.helm.v3.Release(
    "chart",
    chart="temporal",
    repository_opts=k8s.helm.v3.RepositoryOptsArgs(
        repo="https://temporalio.github.io/helm-charts",
    ),
    version="0.52.0",
    namespace=namespace.metadata.name,
    values={
        "server": {
            "replicaCount": 1,
            "frontend": {
                "service": {
                    "annotations": {
                        "tailscale.com/expose": "true",
                        "tailscale.com/hostname": "temporal-frontend",
                    }
                }
            },
            "config": {
                "namespaces": {
                    "create": True,
                }
            },
        },
        "web": {
            "ingress": {
                "enabled": True,
                "className": "tailscale",
                "tls": [
                    {
                        "hosts": ["temporal"],
                        "secretName": "",
                    }
                ],
            }
        },
        "cassandra": {
            "config": {
                "cluster_size": 1,
            },
            "persistence": {
                "enabled": True,
            },
        },
        "elasticsearch": {
            "replicas": 1,
            "persistence": {
                "enabled": True,
            },
        },
        "prometheus": {
            "enabled": False,
        },
        "grafana": {
            "enabled": False,
        },
    },
)
