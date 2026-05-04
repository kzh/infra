import pulumi_kubernetes as k8s

import pulumi

config = pulumi.Config()
cert_manager_namespace = k8s.core.v1.Namespace(
    "namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=config.require("namespace"),
    ),
)

cert_manager_chart = k8s.helm.v3.Release(
    "chart",
    chart="cert-manager",
    namespace=cert_manager_namespace.metadata.name,
    repository_opts=k8s.helm.v3.RepositoryOptsArgs(
        repo="https://charts.jetstack.io",
    ),
    version="v1.20.2",
    values={
        "crds": {
            "enabled": True,
        },
    },
)
