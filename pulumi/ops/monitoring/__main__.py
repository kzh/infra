import pulumi
import pulumi_kubernetes as k8s

def deploy_prometheus_stack_crds():
    """Deploy Prometheus Operator CRDs"""
    return k8s.helm.v4.Chart(
        "prometheus-operator-crds",
        chart="prometheus-operator-crds",
        namespace="monitoring",
        repository_opts=k8s.helm.v4.RepositoryOptsArgs(
            repo="https://prometheus-community.github.io/helm-charts",
        ),
        version="21.0.0",
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
        },
        "crds": {
            "enabled": False,
        },
    }

    return k8s.helm.v4.Chart(
        "kube-prometheus-stack",
        chart="kube-prometheus-stack",
        namespace="monitoring",
        repository_opts=k8s.helm.v4.RepositoryOptsArgs(
            repo="https://prometheus-community.github.io/helm-charts",
        ),
        version="75.10.0",
        values=values,
        opts=pulumi.ResourceOptions(depends_on=[crds_chart]),
    )

def deploy_kubernetes_monitoring():
    """Deploy Kubernetes monitoring components (currently disabled)"""
    # Components are commented out in the original Go code
    pass

def new_kubernetes_metrics_server():
    """Deploy Kubernetes Metrics Server (currently disabled)"""
    return k8s.helm.v4.Chart(
        "metrics-server",
        chart="metrics-server",
        namespace="kube-system",
        repository_opts=k8s.helm.v4.RepositoryOptsArgs(
            repo="https://kubernetes-sigs.github.io/metrics-server/",
        ),
        version="3.7.0",
    )

def new_kubernetes_dashboard():
    """Deploy Kubernetes Dashboard (currently disabled)"""
    return k8s.helm.v4.Chart(
        "kubernetes-dashboard",
        chart="kubernetes-dashboard",
        namespace="monitoring",
        repository_opts=k8s.helm.v4.RepositoryOptsArgs(
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
    )

# Main execution
crds_chart = deploy_prometheus_stack_crds()
prometheus_stack = deploy_prometheus_stack(crds_chart)