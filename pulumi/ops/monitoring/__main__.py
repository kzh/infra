import pulumi
import pulumi_kubernetes as k8s

config = pulumi.Config()
grafana_admin_password = config.require_secret("grafanaAdminPassword")


def skip_await_for_grafana_pvc(obj, opts):
    if obj.get("kind") != "PersistentVolumeClaim":
        return

    metadata = obj.setdefault("metadata", {})
    if metadata.get("name") != "kube-prometheus-stack-grafana":
        return

    annotations = metadata.setdefault("annotations", {})
    annotations["pulumi.com/skipAwait"] = "true"


def ignore_grafana_role_rule_drift(obj, opts):
    if obj.get("kind") != "Role":
        return

    metadata = obj.get("metadata", {})
    if metadata.get("name") != "kube-prometheus-stack-grafana":
        return

    ignore_changes = list(opts.ignore_changes or [])
    if "rules" not in ignore_changes:
        ignore_changes.append("rules")
    opts.ignore_changes = ignore_changes


def deploy_prometheus_stack_crds():
    """Deploy Prometheus Operator CRDs"""
    return k8s.helm.v3.Chart(
        "prometheus-operator-crds",
        k8s.helm.v3.ChartOpts(
            chart="prometheus-operator-crds",
            namespace="monitoring",
            fetch_opts=k8s.helm.v3.FetchOpts(
                repo="https://prometheus-community.github.io/helm-charts",
            ),
            version="27.0.0",
        ),
    )

def deploy_prometheus_stack(crds_chart):
    """Deploy Prometheus Stack"""
    values = {
        "prometheus": {
            "prometheusSpec": {
                "storageSpec": {
                    "volumeClaimTemplate": {
                        "spec": {
                            "storageClassName": "local-path",
                            "accessModes": ["ReadWriteOnce"],
                            "resources": {
                                "requests": {
                                    "storage": "100Gi",
                                },
                            },
                        },
                    },
                },
                "retention": "90d",
                "enableAdminAPI": True,
            },
        },
        "grafana": {
            "adminPassword": grafana_admin_password,
            "grafana.ini": {
                "auth.anonymous": {
                    "enabled": True,
                    "org_role": "Viewer",
                },
                "security": {
                    "allow_embedding": True,
                    "cookie_secure": True,
                    "cookie_samesite": "none",
                },
            },
            "persistence": {
                "enabled": True,
            },
            "ingress": {
                "enabled": True,
                "ingressClassName": "tailscale",
                "hosts": ["grafana"],
                "tls": [
                    {
                        "hosts": ["grafana"],
                    },
                ],
            },
            "sidecar": {
                "dashboards": {
                    "skipReload": True,
                },
                "datasources": {
                    "skipReload": True,
                },
            },
        },
        "crds": {
            "enabled": False,
        },
    }

    return k8s.helm.v3.Chart(
        "kube-prometheus-stack",
        k8s.helm.v3.ChartOpts(
            chart="kube-prometheus-stack",
            namespace="monitoring",
            fetch_opts=k8s.helm.v3.FetchOpts(
                repo="https://prometheus-community.github.io/helm-charts",
            ),
            version="82.1.0",
            values=values,
            transformations=[
                skip_await_for_grafana_pvc,
                ignore_grafana_role_rule_drift,
            ],
        ),
        opts=pulumi.ResourceOptions(depends_on=[crds_chart]),
    )

def deploy_kubernetes_monitoring():
    """Deploy Kubernetes monitoring components (currently disabled)"""
    # Components are commented out in the original Go code
    pass

def new_kubernetes_metrics_server():
    """Deploy Kubernetes Metrics Server (currently disabled)"""
    return k8s.helm.v3.Chart(
        "metrics-server",
        k8s.helm.v3.ChartOpts(
            chart="metrics-server",
            namespace="kube-system",
            fetch_opts=k8s.helm.v3.FetchOpts(
                repo="https://kubernetes-sigs.github.io/metrics-server/",
            ),
            version="3.7.0",
        ),
    )

def new_kubernetes_dashboard():
    """Deploy Kubernetes Dashboard (currently disabled)"""
    return k8s.helm.v3.Chart(
        "kubernetes-dashboard",
        k8s.helm.v3.ChartOpts(
            chart="kubernetes-dashboard",
            namespace="monitoring",
            fetch_opts=k8s.helm.v3.FetchOpts(
                repo="https://kubernetes.github.io/dashboard/",
            ),
            version="7.3.2",
            values={
                "rbac": {
                    "clusterReadOnlyRole": True,
                },
                "service": {
                    "externalPort": 80,
                },
                "protocolHttp": True,
                "metricsScraper": {
                    "enabled": True,
                },
            },
        ),
    )

# Main execution
crds_chart = deploy_prometheus_stack_crds()
prometheus_stack = deploy_prometheus_stack(crds_chart)
