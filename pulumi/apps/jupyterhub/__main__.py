import pulumi
import pulumi_kubernetes as k8s

config = pulumi.Config()
namespace = k8s.core.v1.Namespace(
    "namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=config.require("namespace"),
    ),
)

chart = k8s.helm.v4.Chart(
    "jupyterhub",
    chart="jupyterhub",
    repository_opts=k8s.helm.v4.RepositoryOptsArgs(
        repo="https://hub.jupyter.org/helm-chart/",
    ),
    namespace=namespace.metadata.name,
    values={
        "proxy": {
            "service": {"type": "ClusterIP"},
            "chp": {
                "networkPolicy": {
                    "enabled": False,
                }
            },
            "traefik": {
                "networkPolicy": {
                    "enabled": False,
                }
            },
        },
        "singleuser": {
            "storage": {"capacity": "100Gi"},
            "networkPolicy": {
                "enabled": False,
            },
        },
        "ingress": {
            "enabled": True,
            "ingressClassName": "tailscale",
            "tls": [
                {
                    "hosts": ["jupyter"],
                }
            ],
        },
        "hub": {
            "networkPolicy": {
                "enabled": False,
            }
        },
        "scheduling": {
            "userScheduler": {
                "replicas": 1,
            }
        },
    },
)
