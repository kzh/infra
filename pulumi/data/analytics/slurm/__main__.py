from pathlib import Path

import pulumi_kubernetes as k8s
from infra_helpers.grafana import dashboard_config_maps

import pulumi

config = pulumi.Config()
operator_namespace_name = config.get("operatorNamespace", "slinky")
slurm_namespace_name = config.get("namespace", "slurm")
chart_version = config.get("chartVersion", "1.1.0")
login_hostname = config.get("loginHostname", "slurm")
restapi_replicas = config.get_int("restapiReplicas")
if restapi_replicas is None:
    restapi_replicas = 1
controller_persistence_enabled = config.get_bool("controllerPersistenceEnabled")
if controller_persistence_enabled is None:
    controller_persistence_enabled = False
controller_persistence_storage_class = config.get(
    "controllerPersistenceStorageClass", "local-path"
)
controller_persistence_storage_size = config.get(
    "controllerPersistenceStorageSize", "4Gi"
)
monitoring_release_label = config.get("monitoringReleaseLabel", "kube-prometheus-stack")
dashboards_dir = Path(__file__).resolve().parent / "dashboards"
dashboard_files = [
    "slurm-overview.json",
]

controller_persistence_values = {
    "enabled": controller_persistence_enabled,
}
if controller_persistence_enabled:
    controller_persistence_values.update(
        {
            "storageClassName": controller_persistence_storage_class,
            "resources": {
                "requests": {
                    "storage": controller_persistence_storage_size,
                },
            },
        }
    )

slinky_namespace = k8s.core.v1.Namespace(
    "slinky-namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(name=operator_namespace_name),
)

slurm_operator_crds = k8s.helm.v3.Release(
    "slurm-operator-crds",
    chart="oci://ghcr.io/slinkyproject/charts/slurm-operator-crds",
    namespace=slinky_namespace.metadata.name,
    version=chart_version,
    opts=pulumi.ResourceOptions(depends_on=[slinky_namespace]),
)

slurm_operator = k8s.helm.v3.Release(
    "slurm-operator",
    chart="oci://ghcr.io/slinkyproject/charts/slurm-operator",
    namespace=slinky_namespace.metadata.name,
    version=chart_version,
    values={
        "crds": {
            "enabled": False,
        },
    },
    opts=pulumi.ResourceOptions(depends_on=[slurm_operator_crds]),
)

slurm_namespace = k8s.core.v1.Namespace(
    "slurm-namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(name=slurm_namespace_name),
)

slurm_cluster = k8s.helm.v3.Release(
    "slurm",
    chart="oci://ghcr.io/slinkyproject/charts/slurm",
    namespace=slurm_namespace.metadata.name,
    version=chart_version,
    values={
        "fullnameOverride": "slurm",
        "controller": {
            "persistence": controller_persistence_values,
            "metrics": {
                "enabled": True,
                "serviceMonitor": {
                    "enabled": True,
                    "labels": {
                        "release": monitoring_release_label,
                    },
                },
            },
        },
        "restapi": {
            "replicas": restapi_replicas,
        },
        "loginsets": {
            "slinky": {
                "enabled": True,
                "service": {
                    "metadata": {
                        "annotations": {
                            "tailscale.com/expose": "true",
                            "tailscale.com/hostname": login_hostname,
                        },
                    },
                    "spec": {
                        "type": "ClusterIP",
                    },
                },
            },
        },
    },
    opts=pulumi.ResourceOptions(depends_on=[slurm_operator, slurm_namespace]),
)

dashboard_config_maps(
    name_prefix="slurm-dashboard",
    namespace=slurm_namespace.metadata.name,
    dashboards_dir=dashboards_dir,
    dashboard_files=dashboard_files,
    labels={"app": "slurm"},
    opts=pulumi.ResourceOptions(depends_on=[slurm_cluster]),
)

pulumi.export("operatorNamespace", slinky_namespace.metadata.name)
pulumi.export("namespace", slurm_namespace.metadata.name)
pulumi.export("chartVersion", chart_version)
pulumi.export("loginService", "slurm-login-slinky")
pulumi.export("loginHostname", login_hostname)
pulumi.export("controllerPersistenceEnabled", controller_persistence_enabled)
pulumi.export("controllerPersistenceStorageClass", controller_persistence_storage_class)
pulumi.export("controllerPersistenceStorageSize", controller_persistence_storage_size)
pulumi.export("restapiReplicas", restapi_replicas)
