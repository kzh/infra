import pulumi
import pulumi_kubernetes as k8s

NAMESPACE = "jhub"
HOSTNAME = "jupyterhub"

namespace = k8s.core.v1.Namespace(
    "jupyterhub-namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(name=NAMESPACE),
)

jupyterhub_chart = k8s.helm.v4.Chart(
    "jupyterhub",
    chart="jupyterhub",
    namespace=NAMESPACE,
    version="4.3.1",
    repository_opts=k8s.helm.v4.RepositoryOptsArgs(
        repo="https://hub.jupyter.org/helm-chart/",
    ),
    values={
        "ingress": {
            "enabled": True,
            "ingressClassName": "tailscale",
            "hosts": [HOSTNAME],
            "tls": [{"hosts": [HOSTNAME]}],
        },
        "proxy": {
            "service": {
                "type": "ClusterIP",
            }
        },
        "hub": {
            "db": {
                "type": "sqlite-pvc",
                "pvc": {
                    "storage": "5Gi",
                    "accessModes": ["ReadWriteOnce"],
                },
            },
            "networkPolicy": {"enabled": False},
        },
        "singleuser": {
            "startTimeout": 600,
            "image": {
                "name": "ghcr.io/kzh/jupyter",
                "tag": "py312-amd64",
                "pullPolicy": "Always",
            },
            "storage": {
                "type": "dynamic",
                "capacity": "60Gi",
                "dynamic": {
                    "storageAccessModes": ["ReadWriteOnce"],
                },
            }
        },
        "cull": {
            "enabled": False,
        },
        "scheduling": {
            "userScheduler": {"enabled": False},
            "userPlaceholder": {"enabled": False},
            "podPriority": {"enabled": False},
            "corePods": {"nodeAffinity": {"matchNodePurpose": "ignore"}},
            "userPods": {"nodeAffinity": {"matchNodePurpose": "ignore"}},
        },
    },
    opts=pulumi.ResourceOptions(depends_on=[namespace]),
)
