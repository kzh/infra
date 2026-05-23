import pulumi_kubernetes as k8s

import pulumi

config = pulumi.Config()
grafana_admin_password = config.require_secret("grafanaAdminPassword")

MONITORING_NAMESPACE = "monitoring"
GRAFANA_RESOURCE_NAME = "kube-prometheus-stack-grafana"
PROMETHEUS_REPO = "https://prometheus-community.github.io/helm-charts"


def skip_await_for_grafana_pvc(obj, _opts):
    if obj.get("kind") != "PersistentVolumeClaim":
        return

    metadata = obj.setdefault("metadata", {})
    if metadata.get("name") != GRAFANA_RESOURCE_NAME:
        return

    annotations = metadata.setdefault("annotations", {})
    annotations["pulumi.com/skipAwait"] = "true"


def ignore_grafana_role_rule_drift(obj, opts):
    if obj.get("kind") != "Role":
        return

    metadata = obj.get("metadata", {})
    if metadata.get("name") != GRAFANA_RESOURCE_NAME:
        return

    ignore_changes = list(opts.ignore_changes or [])
    if "rules" not in ignore_changes:
        ignore_changes.append("rules")
    opts.ignore_changes = ignore_changes


def delete_before_replace_generated_chart_resources(obj, opts):
    metadata = obj.get("metadata", {})
    resource = (obj.get("kind"), metadata.get("name"))
    if resource not in {
        ("ConfigMap", "kube-prometheus-stack-cluster-total"),
        ("ConfigMap", "kube-prometheus-stack-k8s-resources-cluster"),
        ("ConfigMap", "kube-prometheus-stack-k8s-resources-multicluster"),
        ("ConfigMap", "kube-prometheus-stack-k8s-resources-namespace"),
        ("ConfigMap", "kube-prometheus-stack-k8s-resources-node"),
        ("ConfigMap", "kube-prometheus-stack-k8s-resources-workload"),
        ("ConfigMap", "kube-prometheus-stack-k8s-resources-workloads-namespace"),
        ("ConfigMap", "kube-prometheus-stack-kubelet"),
        ("ConfigMap", "kube-prometheus-stack-namespace-by-pod"),
        ("ConfigMap", "kube-prometheus-stack-namespace-by-workload"),
        ("ConfigMap", "kube-prometheus-stack-node-rsrc-use"),
        ("Job", "kube-prometheus-stack-admission-create"),
        ("Job", "kube-prometheus-stack-admission-patch"),
    }:
        return

    opts.delete_before_replace = True


def deploy_prometheus_stack_crds():
    """Deploy Prometheus Operator CRDs"""
    return k8s.helm.v3.Chart(
        "prometheus-operator-crds",
        k8s.helm.v3.ChartOpts(
            chart="prometheus-operator-crds",
            namespace=MONITORING_NAMESPACE,
            fetch_opts=k8s.helm.v3.FetchOpts(
                repo=PROMETHEUS_REPO,
            ),
            version="29.0.0",
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
                "retention": "7d",
                "retentionSize": "20GB",
                "walCompression": True,
                "enableAdminAPI": True,
                "resources": {
                    "requests": {
                        "cpu": "250m",
                        "memory": "512Mi",
                    },
                    "limits": {
                        "cpu": "1",
                        "memory": "1536Mi",
                    },
                },
            },
        },
        "grafana": {
            "adminPassword": grafana_admin_password,
            "resources": {
                "requests": {
                    "cpu": "50m",
                    "memory": "128Mi",
                },
                "limits": {
                    "cpu": "500m",
                    "memory": "512Mi",
                },
            },
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
            namespace=MONITORING_NAMESPACE,
            fetch_opts=k8s.helm.v3.FetchOpts(
                repo=PROMETHEUS_REPO,
            ),
            version="85.1.0",
            values=values,
            transformations=[
                skip_await_for_grafana_pvc,
                ignore_grafana_role_rule_drift,
                delete_before_replace_generated_chart_resources,
            ],
        ),
        opts=pulumi.ResourceOptions(depends_on=[crds_chart]),
    )


def deploy_kubernetes_monitoring():
    """Deploy Kubernetes monitoring components (currently disabled)"""
    # Components are commented out in the original Go code


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
            version="3.13.0",
        ),
    )


def new_kubernetes_dashboard():
    """Deploy Kubernetes Dashboard (currently disabled)"""
    return k8s.helm.v3.Chart(
        "kubernetes-dashboard",
        k8s.helm.v3.ChartOpts(
            chart="kubernetes-dashboard",
            namespace=MONITORING_NAMESPACE,
            fetch_opts=k8s.helm.v3.FetchOpts(
                repo="https://kubernetes-retired.github.io/dashboard",
            ),
            version="7.14.0",
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
