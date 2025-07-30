import pulumi
import pulumi_kubernetes as k8s

config = pulumi.Config()
cert_manager_namespace = k8s.core.v1.Namespace(
    "cert-manager-namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=config.require("namespace"),
    ),
)

cert_manager_chart = k8s.helm.v4.Chart(
    "cert-manager",
    chart="cert-manager",
    namespace=cert_manager_namespace.metadata.name,
    repository_opts=k8s.helm.v4.RepositoryOptsArgs(
        repo="https://charts.jetstack.io",
    ),
    version="1.18.2",
    values={
        "crds": {
            "enabled": True,
        },
    }
)