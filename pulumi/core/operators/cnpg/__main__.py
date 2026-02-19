import pulumi
import pulumi_kubernetes as k8s

config = pulumi.Config()
cnpg_namespace_name = config.get("namespace", "cloudnative-pg")
monitoring_release_label = config.get("monitoringReleaseLabel", "kube-prometheus-stack")
monitoring_namespace_name = config.get("monitoringNamespace", "monitoring")

cnpg_namespace = k8s.core.v1.Namespace(
    "cloudnative-pg",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=cnpg_namespace_name,
    ),
)

cloudnative_pg = k8s.helm.v4.Chart(
    "cloudnative-pg",
    chart="cloudnative-pg",
    namespace=cnpg_namespace.metadata.name,
    repository_opts=k8s.helm.v4.RepositoryOptsArgs(
        repo="https://cloudnative-pg.github.io/charts",
    ),
    version="0.27.1",
    values={
        "monitoring": {
            "podMonitorEnabled": True,
            "podMonitorAdditionalLabels": {
                "release": monitoring_release_label,
            },
            "grafanaDashboard": {
                "create": True,
                "namespace": monitoring_namespace_name,
                "labels": {
                    "grafana_dashboard": "1",
                },
            },
        },
    },
)

pulumi.export("namespace", cnpg_namespace_name)
pulumi.export("monitoring_namespace", monitoring_namespace_name)
