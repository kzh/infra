import pulumi
import pulumi_kubernetes as k8s

config = pulumi.Config()
namespace = k8s.core.v1.Namespace(
    "namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=config.require("namespace"),
    ),
)

chart = k8s.helm.v4.Chart(
    "penpot",
    chart="penpot",
    repository_opts=k8s.helm.v4.RepositoryOptsArgs(
        repo="https://helm.penpot.app",
    ),
    namespace=namespace.metadata.name,
    version="0.5.0",
    values={
        "global": {
            "postgresqlEnabled": True,
            "redisEnabled": True,
        },
        "config": {"publicUri": config.require("publicUri")},
        "ingress": {
            "enabled": True,
            "className": "tailscale",
            "hosts": [""],
            "tls": [
                {
                    "hosts": ["penpot"],
                    "secretName": "",
                },
            ],
        },
        "persistence": {
            "assets": {
                "enabled": True,
            },
        },
        "redis": {
            "replica": {
                "replicaCount": 0,
            },
        },
    },
)
