import pulumi
import pulumi_kubernetes as k8s

config = pulumi.Config()

cf_tunnel_namespace = k8s.core.v1.Namespace(
    "cf-tunnel-namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="cloudflare-tunnel",
    ),
)

cloudflare_tunnel_chart = k8s.helm.v4.Chart(
    "cloudflare-tunnel",
    chart="cloudflare-tunnel-ingress-controller",
    namespace=cf_tunnel_namespace.metadata.name,
    repository_opts=k8s.helm.v4.RepositoryOptsArgs(
        repo="https://helm.strrl.dev",
    ),
    values={
        "cloudflare": {
            "apiToken": config.require("cloudflareTunnelApiToken"),
            "accountId": config.require("cloudflareAccountId"),
            "tunnelName": config.get("tunnelName", "mx0"),
        },
    },
)
