import pulumi_kubernetes as k8s

import pulumi

redpanda_namespace = k8s.core.v1.Namespace(
    "namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        labels={
            "app": "redpanda",
        },
        name="redpanda",
    ),
)

redpanda = k8s.helm.v3.Release(
    "redpanda",
    chart="operator",
    namespace=redpanda_namespace.metadata.name,
    repository_opts=k8s.helm.v3.RepositoryOptsArgs(
        repo="https://charts.redpanda.com",
    ),
    values={
        "additionalCmdFlags": [
            "--enable-helm-controllers=false",
        ],
        "crds": {
            "enabled": True,
        },
    },
    version="26.1.3",
)

pulumi.export("name", "redpanda")
