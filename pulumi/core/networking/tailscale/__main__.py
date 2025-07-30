import pulumi
import pulumi_kubernetes as k8s

tailscale_namespace = k8s.core.v1.Namespace(
    "tailscale-namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="tailscale",
    ),
)

config = pulumi.Config()
tailscale_operator = k8s.helm.v4.Chart(
    "tailscale-operator",
    chart="tailscale-operator",
    namespace=tailscale_namespace.metadata.name,
    version="1.84.3",
    repository_opts=k8s.helm.v4.RepositoryOptsArgs(
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
    },
    opts=pulumi.ResourceOptions(depends_on=[tailscale_namespace]),
)