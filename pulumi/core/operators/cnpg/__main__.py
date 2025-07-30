import pulumi
import pulumi_kubernetes as k8s

config = pulumi.Config()
cnpg_namespace = k8s.core.v1.Namespace(
    "cloudnative-pg",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=config.get("namespace", "cloudnative-pg"),
    ),
)

cloudnative_pg = k8s.helm.v4.Chart(
    "cloudnative-pg",
    chart="cloudnative-pg",
    namespace=cnpg_namespace.metadata.name,
    repository_opts=k8s.helm.v4.RepositoryOptsArgs(
        repo="https://cloudnative-pg.github.io/charts",
    ),
    version="0.25.0",
)
