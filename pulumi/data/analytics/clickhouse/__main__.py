import hashlib
from pathlib import Path

import pulumi
import pulumi_kubernetes as k8s
import pulumi_random as random

config = pulumi.Config()
namespace_name = config.get("namespace", "clickhouse")
operator_chart_version = config.get("operatorChartVersion", "0.25.6")
clickhouse_installation_name = config.get("installationName", "clickhouse")
clickhouse_cluster_name = config.get("clusterName", "default")
clickhouse_admin_username = config.get("adminUsername", "admin")
clickhouse_admin_password_length = config.get_int("adminPasswordLength") or 32
monitoring_release_label = config.get("monitoringReleaseLabel", "kube-prometheus-stack")
grafana_dashboard_label = config.get("grafanaDashboardLabel", "1")
clickhouse_admin_networks = config.get_object(
    "adminNetworks",
    ["0.0.0.0/0", "::/0"],
)
clickhouse_image = config.get(
    "clickhouseImage",
    "altinity/clickhouse-server:25.3.8.10041.altinitystable",
)
storage_class_name = config.get("storageClassName", "local-path")
storage_size = config.get("storageSize", "100Gi")
clickhouse_hostname = config.get("hostname", "clickhouse")
tailscale_domain = config.get("tailscaleDomain", "tail1c114.ts.net")
clickhouse_host = f"{clickhouse_hostname}.{tailscale_domain}"
clickhouse_port = 9000
pulumi_dir = Path(__file__).resolve().parents[3]
dashboards_dir = pulumi_dir / "ops" / "dashboards" / "clickhouse"
dashboard_files = [
    "altinity-clickhouse-operator.json",
    "clickhouse-queries.json",
]

clickhouse_namespace = k8s.core.v1.Namespace(
    "clickhouse-namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=namespace_name,
    ),
)

clickhouse_admin_password_resource = random.RandomPassword(
    "clickhouse-admin-password",
    length=clickhouse_admin_password_length,
    lower=True,
    upper=True,
    numeric=True,
    special=False,
    min_lower=1,
    min_upper=1,
    min_numeric=1,
)
clickhouse_admin_password = clickhouse_admin_password_resource.result
clickhouse_admin_password_task_id = clickhouse_admin_password.apply(
    lambda password: hashlib.sha256(password.encode("utf-8")).hexdigest()[:16]
)

clickhouse_admin_credentials = k8s.core.v1.Secret(
    "clickhouse-admin-credentials",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="clickhouse-admin-credentials",
        namespace=namespace_name,
    ),
    type="Opaque",
    string_data={
        "password": clickhouse_admin_password,
    },
    opts=pulumi.ResourceOptions(depends_on=[clickhouse_namespace]),
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
            "enabled": True,
            "resources": {},
        },
        "serviceMonitor": {
            "enabled": True,
            "additionalLabels": {
                "release": monitoring_release_label,
            },
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

for dashboard_file in dashboard_files:
    dashboard_name = dashboard_file.replace(".json", "")
    dashboard_data = (dashboards_dir / dashboard_file).read_text(encoding="utf-8")
    k8s.core.v1.ConfigMap(
        f"clickhouse-dashboard-{dashboard_name}",
        metadata=k8s.meta.v1.ObjectMetaArgs(
            name=f"clickhouse-dashboard-{dashboard_name}",
            namespace=namespace_name,
            labels={
                "grafana_dashboard": grafana_dashboard_label,
                "app": "clickhouse",
            },
            annotations={
                "grafana_folder": "clickhouse",
            },
        ),
        data={
            dashboard_file: dashboard_data,
        },
        opts=pulumi.ResourceOptions(depends_on=[clickhouse_operator]),
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
        "taskID": clickhouse_admin_password_task_id,
        "configuration": {
            "users": {
                f"{clickhouse_admin_username}/profile": "default",
                f"{clickhouse_admin_username}/quota": "default",
                f"{clickhouse_admin_username}/networks/ip": clickhouse_admin_networks,
                f"{clickhouse_admin_username}/k8s_secret_password": "clickhouse-admin-credentials/password",
                f"{clickhouse_admin_username}/grants/query": [
                    "GRANT ALL ON *.*",
                ],
            },
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
    opts=pulumi.ResourceOptions(
        depends_on=[clickhouse_operator, clickhouse_admin_credentials]
    ),
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

pulumi.export("clickhouseHost", clickhouse_host)
pulumi.export("clickhousePort", clickhouse_port)
pulumi.export("clickhouseAdminUsername", clickhouse_admin_username)
pulumi.export("clickhouseAdminPassword", pulumi.Output.secret(clickhouse_admin_password))
