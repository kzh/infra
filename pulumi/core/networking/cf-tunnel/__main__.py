from pathlib import Path

import pulumi_kubernetes as k8s
from pulumi_monitoring_crds.monitoring.v1 import ServiceMonitor

import pulumi

config = pulumi.Config()
cf_tunnel_namespace_name = config.get("namespace", "cloudflare-tunnel")
monitoring_release_label = config.get("monitoringReleaseLabel", "kube-prometheus-stack")
dashboards_dir = Path(__file__).resolve().parent / "dashboards"

cf_tunnel_namespace = k8s.core.v1.Namespace(
    "cloudflare-tunnel",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=cf_tunnel_namespace_name,
    ),
)

cloudflare_tunnel_chart = k8s.helm.v3.Release(
    "cloudflare-tunnel",
    chart="cloudflare-tunnel-ingress-controller",
    name="cloudflare-tunnel-b6e117c1",
    version="0.0.23",
    namespace=cf_tunnel_namespace.metadata.name,
    repository_opts=k8s.helm.v3.RepositoryOptsArgs(
        repo="https://helm.strrl.dev",
    ),
    values={
        "cloudflare": {
            "apiToken": config.require_secret("cloudflareTunnelApiToken"),
            "accountId": config.require("cloudflareAccountId"),
            "tunnelName": config.get("tunnelName", "mx0"),
        },
    },
)

cloudflare_tunnel_servicemonitor = ServiceMonitor(
    "cloudflare-tunnel-servicemonitor",
    metadata={
        "name": "cloudflare-tunnel",
        "namespace": cf_tunnel_namespace_name,
        "labels": {
            "release": monitoring_release_label,
        },
    },
    spec={
        "selector": {
            "matchLabels": {
                "app.kubernetes.io/component": "controlled-cloudflared",
                "app.kubernetes.io/name": "cloudflare-tunnel-ingress-controller",
            },
        },
        "namespaceSelector": {
            "matchNames": [cf_tunnel_namespace_name],
        },
        "endpoints": [
            {
                "port": "metrics",
                "path": "/metrics",
                "interval": "30s",
                "scheme": "http",
            }
        ],
    },
    opts=pulumi.ResourceOptions(depends_on=[cloudflare_tunnel_chart]),
)

for dashboard_file in [
    "cloudflare-tunnel-overview.json",
    "cloudflare-tunnel-transport.json",
]:
    dashboard_name = dashboard_file.replace(".json", "")
    dashboard_data = (dashboards_dir / dashboard_file).read_text(encoding="utf-8")
    k8s.core.v1.ConfigMap(
        f"cloudflare-tunnel-dashboard-{dashboard_name}",
        metadata=k8s.meta.v1.ObjectMetaArgs(
            name=f"cloudflare-tunnel-dashboard-{dashboard_name}",
            namespace=cf_tunnel_namespace_name,
            labels={
                "grafana_dashboard": "1",
            },
        ),
        data={
            dashboard_file: dashboard_data,
        },
        opts=pulumi.ResourceOptions(depends_on=[cf_tunnel_namespace]),
    )
