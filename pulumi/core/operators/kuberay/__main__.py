from pathlib import Path

import pulumi_kubernetes as k8s
from infra_helpers.grafana import dashboard_config_maps
from pulumi_kuberay_crds.ray.v1 import RayCluster
from pulumi_monitoring_crds.monitoring.v1 import PodMonitor

import pulumi

config = pulumi.Config()
namespace_name = config.get("namespace", "kuberay-operator")
chart_version = config.get("chartVersion", "1.6.1")
ray_dev_namespace_name = config.get("rayDevNamespace", "ray-dev")
monitoring_namespace_name = config.get("monitoringNamespace", "monitoring")
ray_dev_cluster_name = config.get("rayDevClusterName", "ray-dev")
ray_dev_api_hostname = config.get("rayDevApiHostname", "ray-dev-api")
ray_dev_dashboard_host = config.get("rayDevDashboardHost", "ray-dev")
ray_dev_prometheus_host = config.get(
    "rayDevPrometheusHost",
    "http://kube-prometheus-stack-prometheus.monitoring.svc:9090",
)
ray_dev_prometheus_name = config.get("rayDevPrometheusName", "Prometheus")
ray_dev_grafana_host = config.get(
    "rayDevGrafanaHost",
    "http://kube-prometheus-stack-grafana.monitoring.svc:80",
)
ray_dev_grafana_iframe_host = config.get("rayDevGrafanaIframeHost", "https://grafana")
ray_dev_grafana_org_id = config.get("rayDevGrafanaOrgId", "1")
ray_dev_image = config.get("rayDevImage", "rayproject/ray:2.55.1")
ray_dev_version = config.get("rayDevVersion", "2.55.1")
ray_dev_worker_replicas = int(config.get("rayDevWorkerReplicas", "1"))

RAY_DASHBOARD_FILES = [
    "default_grafana_dashboard.json",
    "serve_grafana_dashboard.json",
    "serve_deployment_grafana_dashboard.json",
    "serve_llm_grafana_dashboard.json",
    "data_grafana_dashboard.json",
    "data_llm_grafana_dashboard.json",
    "train_grafana_dashboard.json",
]
RAY_DASHBOARDS_DIR = Path(__file__).resolve().parent / "dashboards"


def ray_dashboard_name(dashboard_file: str) -> str:
    return dashboard_file.replace("_", "-").removesuffix(".json")


kuberay_namespace = k8s.core.v1.Namespace(
    "kuberay-namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=namespace_name,
    ),
)

kuberay_operator = k8s.helm.v4.Chart(
    "kuberay-operator",
    chart="kuberay-operator",
    namespace=kuberay_namespace.metadata.name,
    repository_opts=k8s.helm.v4.RepositoryOptsArgs(
        repo="https://ray-project.github.io/kuberay-helm",
    ),
    version=chart_version,
    values={
        "metrics": {
            "serviceMonitor": {
                "enabled": True,
                "selector": {
                    "release": "kube-prometheus-stack",
                },
            },
        },
    },
)

ray_dev_namespace = k8s.core.v1.Namespace(
    "ray-dev-namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(name=ray_dev_namespace_name),
)

ray_grafana_dashboards = dashboard_config_maps(
    name_prefix="ray-grafana-dashboard",
    namespace=monitoring_namespace_name,
    dashboards_dir=RAY_DASHBOARDS_DIR,
    dashboard_files=RAY_DASHBOARD_FILES,
    opts=pulumi.ResourceOptions(delete_before_replace=True),
    dashboard_name=ray_dashboard_name,
)

ray_dev_cluster = RayCluster(
    "ray-dev-cluster",
    metadata={
        "name": ray_dev_cluster_name,
        "namespace": ray_dev_namespace.metadata.name,
    },
    spec={
        "rayVersion": ray_dev_version,
        "headGroupSpec": {
            "rayStartParams": {
                "object-store-memory": "100000000",
            },
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": "ray-head",
                            "image": ray_dev_image,
                            "env": [
                                {
                                    "name": "RAY_PROMETHEUS_HOST",
                                    "value": ray_dev_prometheus_host,
                                },
                                {
                                    "name": "RAY_PROMETHEUS_NAME",
                                    "value": ray_dev_prometheus_name,
                                },
                                {
                                    "name": "RAY_GRAFANA_HOST",
                                    "value": ray_dev_grafana_host,
                                },
                                {
                                    "name": "RAY_GRAFANA_IFRAME_HOST",
                                    "value": ray_dev_grafana_iframe_host,
                                },
                                {
                                    "name": "RAY_GRAFANA_ORG_ID",
                                    "value": ray_dev_grafana_org_id,
                                },
                            ],
                            "ports": [
                                {"containerPort": 6379, "name": "gcs-server"},
                                {"containerPort": 8265, "name": "dashboard"},
                                {"containerPort": 10001, "name": "client"},
                            ],
                            "resources": {
                                "requests": {"cpu": "1000m", "memory": "2Gi"},
                                "limits": {"cpu": "1000m", "memory": "2Gi"},
                            },
                        }
                    ]
                }
            },
        },
        "workerGroupSpecs": [
            {
                "groupName": "dev-workers",
                "replicas": ray_dev_worker_replicas,
                "minReplicas": ray_dev_worker_replicas,
                "maxReplicas": ray_dev_worker_replicas,
                "rayStartParams": {
                    "object-store-memory": "100000000",
                },
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": "ray-worker",
                                "image": ray_dev_image,
                                "resources": {
                                    "requests": {"cpu": "500m", "memory": "1Gi"},
                                    "limits": {"cpu": "500m", "memory": "1Gi"},
                                },
                            }
                        ]
                    }
                },
            }
        ],
    },
    opts=pulumi.ResourceOptions(depends_on=[kuberay_operator, ray_dev_namespace]),
)

ray_dev_dashboard_service = k8s.core.v1.Service(
    "ray-dev-dashboard-service",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="ray-dev-dashboard",
        namespace=ray_dev_namespace.metadata.name,
    ),
    spec=k8s.core.v1.ServiceSpecArgs(
        type="ClusterIP",
        selector={
            "ray.io/cluster": ray_dev_cluster_name,
            "ray.io/node-type": "head",
        },
        ports=[
            k8s.core.v1.ServicePortArgs(
                name="dashboard",
                port=8265,
                target_port=8265,
            ),
        ],
    ),
    opts=pulumi.ResourceOptions(depends_on=[ray_dev_cluster]),
)

ray_dev_api_service = k8s.core.v1.Service(
    "ray-dev-api-service",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="ray-dev-api",
        namespace=ray_dev_namespace.metadata.name,
        annotations={
            "tailscale.com/expose": "true",
            "tailscale.com/hostname": ray_dev_api_hostname,
        },
    ),
    spec=k8s.core.v1.ServiceSpecArgs(
        type="ClusterIP",
        selector={
            "ray.io/cluster": ray_dev_cluster_name,
            "ray.io/node-type": "head",
        },
        ports=[
            k8s.core.v1.ServicePortArgs(
                name="ray-client",
                port=10001,
                target_port=10001,
            ),
        ],
    ),
    opts=pulumi.ResourceOptions(depends_on=[ray_dev_cluster]),
)

ray_dev_dashboard_ingress = k8s.networking.v1.Ingress(
    "ray-dev-dashboard-ingress",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="ray-dev-dashboard",
        namespace=ray_dev_namespace.metadata.name,
    ),
    spec=k8s.networking.v1.IngressSpecArgs(
        ingress_class_name="tailscale",
        rules=[
            k8s.networking.v1.IngressRuleArgs(
                host=ray_dev_dashboard_host,
                http=k8s.networking.v1.HTTPIngressRuleValueArgs(
                    paths=[
                        k8s.networking.v1.HTTPIngressPathArgs(
                            path="/",
                            path_type="Prefix",
                            backend=k8s.networking.v1.IngressBackendArgs(
                                service=k8s.networking.v1.IngressServiceBackendArgs(
                                    name=ray_dev_dashboard_service.metadata.name,
                                    port=k8s.networking.v1.ServiceBackendPortArgs(
                                        number=8265
                                    ),
                                ),
                            ),
                        ),
                    ],
                ),
            ),
        ],
        tls=[k8s.networking.v1.IngressTLSArgs(hosts=[ray_dev_dashboard_host])],
    ),
    opts=pulumi.ResourceOptions(depends_on=[ray_dev_dashboard_service]),
)

ray_dev_podmonitor = PodMonitor(
    "ray-dev-podmonitor",
    metadata={
        "name": "ray-dev-pods",
        "namespace": ray_dev_namespace.metadata.name,
        "labels": {
            "release": "kube-prometheus-stack",
        },
    },
    spec={
        "selector": {
            "matchLabels": {
                "ray.io/cluster": ray_dev_cluster_name,
                "ray.io/is-ray-node": "yes",
            },
        },
        "podMetricsEndpoints": [
            {
                "port": "metrics",
                "path": "/metrics",
                "interval": "30s",
            }
        ],
    },
    opts=pulumi.ResourceOptions(depends_on=[ray_dev_cluster]),
)

pulumi.export("namespace", kuberay_namespace.metadata.name)
pulumi.export("chart_version", chart_version)
pulumi.export("ray_dev_namespace", ray_dev_namespace.metadata.name)
pulumi.export("ray_dev_cluster_name", ray_dev_cluster_name)
pulumi.export("ray_dev_api_service", ray_dev_api_service.metadata.name)
pulumi.export("ray_dev_api_hostname", ray_dev_api_hostname)
pulumi.export("ray_dev_dashboard_ingress_host", ray_dev_dashboard_host)
pulumi.export("ray_dev_prometheus_host", ray_dev_prometheus_host)
pulumi.export("ray_dev_prometheus_name", ray_dev_prometheus_name)
pulumi.export("ray_dev_grafana_host", ray_dev_grafana_host)
pulumi.export("ray_dev_grafana_iframe_host", ray_dev_grafana_iframe_host)
pulumi.export("monitoring_namespace", monitoring_namespace_name)
