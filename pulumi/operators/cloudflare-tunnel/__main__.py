import pulumi
import pulumi_kubernetes as kubernetes

config = pulumi.Config()

namespace = kubernetes.core.v1.Namespace(
    "cloudflare-tunnel",
    metadata=kubernetes.meta.v1.ObjectMetaArgs(
        name="cloudflare-tunnel",
    ),
)

cloudflare_tunnel = kubernetes.helm.v3.Release(
    "cloudflare-tunnel",
    chart="cloudflare-tunnel-ingress-controller",
    namespace=namespace.metadata.name,
    repository_opts={
        "repo": "https://helm.strrl.dev",
    },
    values={
        "cloudflare": {
            "apiToken": config.require("cloudflareTunnelApiToken"),
            "accountId": config.require("cloudflareAccountId"),
            "tunnelName": "mx0",
        },
    },
)
