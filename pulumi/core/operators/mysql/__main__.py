import pulumi_kubernetes as k8s

import pulumi

config = pulumi.Config()

namespace_name = config.get("namespace") or "mysql-operator"
release_name = config.get("releaseName") or "mysql-operator"
chart_version = config.get("chartVersion") or "2.2.7"
k8s_cluster_domain = config.get("k8sClusterDomain") or "cluster.local"

mysql_operator_namespace = k8s.core.v1.Namespace(
    "mysql-operator-namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=namespace_name,
        labels={
            "app": "mysql-operator",
        },
    ),
)

mysql_operator = k8s.helm.v3.Release(
    "mysql-operator",
    chart="mysql-operator",
    name=release_name,
    namespace=namespace_name,
    version=chart_version,
    repository_opts=k8s.helm.v3.RepositoryOptsArgs(
        repo="https://mysql.github.io/mysql-operator/",
    ),
    values={
        "envs": {
            "k8sClusterDomain": k8s_cluster_domain,
        },
    },
    opts=pulumi.ResourceOptions(
        depends_on=[mysql_operator_namespace],
        delete_before_replace=True,
    ),
)

pulumi.export("namespace", namespace_name)
pulumi.export("releaseName", release_name)
pulumi.export("chartVersion", chart_version)
pulumi.export("k8sClusterDomain", k8s_cluster_domain)
pulumi.export("status", mysql_operator.status)
