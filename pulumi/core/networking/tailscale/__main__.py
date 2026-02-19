from pathlib import Path

import pulumi
import pulumi_kubernetes as k8s

config = pulumi.Config()
tailscale_namespace_name = config.get("namespace", "tailscale")
monitoring_namespace_name = config.get("monitoringNamespace", tailscale_namespace_name)
monitoring_release_label = config.get("monitoringReleaseLabel", "kube-prometheus-stack")
chart_version = config.get("chartVersion", "1.94.2")
dashboards_dir = Path(__file__).parent / "dashboards"
dashboard_files = [
    "tailscale-operator-overview.json",
    "tailscale-proxy-metrics.json",
]

tailscale_namespace = k8s.core.v1.Namespace(
    "tailscale",
    metadata=k8s.meta.v1.ObjectMetaArgs(name=tailscale_namespace_name),
)

tailscale_operator = k8s.helm.v3.Release(
    "tailscale-operator",
    chart="tailscale-operator",
    namespace=tailscale_namespace_name,
    version=chart_version,
    replace=True,
    repository_opts=k8s.helm.v3.RepositoryOptsArgs(
        repo="https://pkgs.tailscale.com/helmcharts",
    ),
    values={
        "oauth": {
            "clientId": config.require("TS_CLIENT_ID"),
            "clientSecret": config.require_secret("TS_CLIENT_SECRET"),
        },
        "apiServerProxyConfig": {
            "mode": "true",
        },
        "proxyConfig": {
            "defaultProxyClass": "tailscale-default-metrics",
        },
    },
    opts=pulumi.ResourceOptions(depends_on=[tailscale_namespace]),
)

tailscale_operator_metrics_service = k8s.core.v1.Service(
    "tailscale-operator-metrics-service",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="tailscale-operator-metrics",
        namespace=tailscale_namespace_name,
        labels={
            "app": "tailscale-operator",
            "component": "metrics",
        },
    ),
    spec=k8s.core.v1.ServiceSpecArgs(
        selector={"app": "operator"},
        ports=[
            k8s.core.v1.ServicePortArgs(
                name="metrics",
                port=8080,
                target_port=8080,
            )
        ],
    ),
    opts=pulumi.ResourceOptions(depends_on=[tailscale_operator]),
)

tailscale_operator_servicemonitor = k8s.apiextensions.CustomResource(
    "tailscale-operator-servicemonitor",
    api_version="monitoring.coreos.com/v1",
    kind="ServiceMonitor",
    metadata={
        "name": "tailscale-operator",
        "namespace": tailscale_namespace_name,
        "labels": {
            "release": monitoring_release_label,
        },
    },
    spec={
        "selector": {
            "matchLabels": {
                "app": "tailscale-operator",
                "component": "metrics",
            },
        },
        "namespaceSelector": {
            "matchNames": [tailscale_namespace_name],
        },
        "endpoints": [
            {
                "port": "metrics",
                "path": "/metrics",
                "interval": "30s",
                "scheme": "http",
            },
        ],
    },
    opts=pulumi.ResourceOptions(depends_on=[tailscale_operator_metrics_service]),
)

tailscale_default_metrics_proxyclass = k8s.apiextensions.CustomResource(
    "tailscale-default-metrics-proxyclass",
    api_version="tailscale.com/v1alpha1",
    kind="ProxyClass",
    metadata={
        "name": "tailscale-default-metrics",
    },
    spec={
        "metrics": {
            "enable": True,
            "serviceMonitor": {
                "enable": True,
                "labels": {
                    "release": monitoring_release_label,
                },
            },
        },
    },
    opts=pulumi.ResourceOptions(depends_on=[tailscale_operator_servicemonitor]),
)

for dashboard_file in dashboard_files:
    dashboard_name = dashboard_file.replace(".json", "")
    dashboard_data = (dashboards_dir / dashboard_file).read_text(encoding="utf-8")
    k8s.core.v1.ConfigMap(
        f"tailscale-dashboard-{dashboard_name}",
        metadata=k8s.meta.v1.ObjectMetaArgs(
            name=f"tailscale-dashboard-{dashboard_name}",
            namespace=monitoring_namespace_name,
            labels={
                "grafana_dashboard": "1",
                "app": "tailscale-operator",
            },
        ),
        data={
            dashboard_file: dashboard_data,
        },
        opts=pulumi.ResourceOptions(depends_on=[tailscale_default_metrics_proxyclass]),
    )
