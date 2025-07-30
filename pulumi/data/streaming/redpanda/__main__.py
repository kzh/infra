import pulumi
import pulumi_kubernetes as k8s

redpanda_namespace = k8s.core.v1.Namespace(
    "redpanda-namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        labels={
            "app": "redpanda",
        },
        name="redpanda",
    ),
)

redpanda = k8s.helm.v4.Chart(
    "redpanda",
    chart="operator",
    namespace=redpanda_namespace.metadata.name,
    repository_opts=k8s.helm.v4.RepositoryOptsArgs(
        repo="https://charts.redpanda.com",
    ),
    skip_crds=True,
    values={
        "additionalCmdFlags": ["--enable-helm-controllers=false"],
    },
    version="2.4.3",
)

pulumi.export("name", "redpanda")