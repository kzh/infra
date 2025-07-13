import pulumi
import pulumi_kubernetes as k8s

namespace = k8s.core.v1.Namespace(
    "tailscale",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="tailscale",
    ),
)

config = pulumi.Config()
operator = k8s.helm.v3.Release(
    "tailscale-operator",
    chart="tailscale-operator",
    namespace=namespace.metadata.apply(lambda m: m.name),
    version="1.84.3",
    repository_opts=k8s.helm.v3.RepositoryOptsArgs(
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
    replace=True,
    opts=pulumi.ResourceOptions(depends_on=[namespace]),
)