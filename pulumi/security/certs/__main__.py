import pulumi
import pulumi_kubernetes as k8s

config = pulumi.Config()
namespace = k8s.core.v1.Namespace(
    "namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=config.require("namespace"),
    ),
)

chart = k8s.helm.v3.Release(
    "chart",
    chart="cert-manager",
    repository_opts=k8s.helm.v3.RepositoryOptsArgs(
        repo="https://charts.jetstack.io",
    ),
    namespace=namespace.metadata.name,
    version="1.16.2",
    values={
        "crds": {
            "enabled": True,
        },
    }
)