import pulumi
import pulumi_kubernetes as k8s

config = pulumi.Config()
namespace_name = config.get("namespace", "clickhouse")
operator_chart_version = config.get("operatorChartVersion", "0.25.6")
clickhouse_installation_name = config.get("installationName", "clickhouse")
clickhouse_cluster_name = config.get("clusterName", "default")
clickhouse_image = config.get(
    "clickhouseImage",
    "altinity/clickhouse-server:25.3.8.10041.altinitystable",
)
storage_class_name = config.get("storageClassName", "local-path")
storage_size = config.get("storageSize", "100Gi")
clickhouse_hostname = config.get("hostname", "clickhouse")

clickhouse_namespace = k8s.core.v1.Namespace(
    "clickhouse-namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=namespace_name,
    ),
)

clickhouse_operator = k8s.helm.v3.Release(
    "clickhouse-operator",
    chart="altinity-clickhouse-operator",
    name="chop",
    namespace=namespace_name,
    version=operator_chart_version,
    repository_opts=k8s.helm.v3.RepositoryOptsArgs(
        repo="https://docs.altinity.com/clickhouse-operator",
    ),
    values={
        # Keep the operator footprint minimal for this single-node deployment.
        "metrics": {
            "enabled": False,
        },
        "serviceMonitor": {
            "enabled": False,
        },
        "dashboards": {
            "enabled": False,
        },
        "operator": {
            "resources": {},
        },
    },
    opts=pulumi.ResourceOptions(depends_on=[clickhouse_namespace]),
)

clickhouse_installation = k8s.apiextensions.CustomResource(
    "clickhouse-installation",
    api_version="clickhouse.altinity.com/v1",
    kind="ClickHouseInstallation",
    metadata={
        "name": clickhouse_installation_name,
        "namespace": namespace_name,
    },
    spec={
        "configuration": {
            "clusters": [
                {
                    "name": clickhouse_cluster_name,
                    "layout": {
                        "shardsCount": 1,
                        "replicasCount": 1,
                    },
                    "templates": {
                        "podTemplate": "clickhouse-pod-template",
                        "dataVolumeClaimTemplate": "clickhouse-data-volume-template",
                    },
                },
            ],
        },
        "templates": {
            "podTemplates": [
                {
                    "name": "clickhouse-pod-template",
                    "spec": {
                        "containers": [
                            {
                                "name": "clickhouse",
                                "image": clickhouse_image,
                                "resources": {},
                            },
                        ],
                    },
                },
            ],
            "volumeClaimTemplates": [
                {
                    "name": "clickhouse-data-volume-template",
                    "spec": {
                        "accessModes": ["ReadWriteOnce"],
                        "storageClassName": storage_class_name,
                        "resources": {
                            "requests": {
                                "storage": storage_size,
                            },
                        },
                    },
                },
            ],
        },
    },
    opts=pulumi.ResourceOptions(depends_on=[clickhouse_operator]),
)

clickhouse_tailscale_service = k8s.core.v1.Service(
    "clickhouse-tailscale-service",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="clickhouse",
        namespace=namespace_name,
        annotations={
            "tailscale.com/expose": "true",
            "tailscale.com/hostname": clickhouse_hostname,
        },
    ),
    spec=k8s.core.v1.ServiceSpecArgs(
        type="ClusterIP",
        selector={
            "clickhouse.altinity.com/app": "chop",
            "clickhouse.altinity.com/chi": clickhouse_installation_name,
            "clickhouse.altinity.com/namespace": namespace_name,
            "clickhouse.altinity.com/ready": "yes",
        },
        ports=[
            k8s.core.v1.ServicePortArgs(
                name="tcp",
                port=9000,
                target_port=9000,
            ),
            k8s.core.v1.ServicePortArgs(
                name="http",
                port=8123,
                target_port=8123,
            ),
        ],
    ),
    opts=pulumi.ResourceOptions(depends_on=[clickhouse_installation]),
)
