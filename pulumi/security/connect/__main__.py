import pulumi
import pulumi_kubernetes as k8s

RESOURCE_NAME = "connect"
NAMESPACE = "connect"

namespace = k8s.core.v1.Namespace(
    NAMESPACE,
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=NAMESPACE,
    ),
)

config = pulumi.Config()

connect_chart = k8s.helm.v4.Chart(
    RESOURCE_NAME,
    chart="connect",
    namespace=NAMESPACE,
    repository_opts=k8s.helm.v4.RepositoryOptsArgs(
        repo="https://1password.github.io/connect-helm-charts",
    ),
    version="2.0.1",
    values={
        "connect": {
            "serviceType": "ClusterIP",
            "credentials": config.require_secret("CONNECT_CREDENTIALS"),
            "serviceAnnotations": {
                "tailscale.com/expose": "true",
                "tailscale.com/hostname": "onepassword-connect",
            },
        },
    },
    opts=pulumi.ResourceOptions(depends_on=[namespace]),
)