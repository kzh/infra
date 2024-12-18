import pulumi
import pulumi_kubernetes as k8s

config = pulumi.Config()
namespace = k8s.core.v1.Namespace(
    "namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=config.require("namespace"),
    ),
)

chart = k8s.helm.v3.Release(
    "open-webui",
    chart="open-webui",
    repository_opts=k8s.helm.v3.RepositoryOptsArgs(
        repo="https://helm.openwebui.com",
    ),
    namespace=namespace.metadata.name,
    values={
        "ollama": {
            "enabled": False,
        },
        "ingress": {
            "enabled": True,
            "class": "tailscale",
            "host": "chat",
            "tls": True,
        },
        "persistence": {
            "size": "20Gi",
        },
        "pipelines": {
            "enabled": False,
        },
        "extraEnvVars": [
            {
                "name": "OPENAI_API_KEY",
                "value": config.require_secret("openai_api_key"),
            }
        ],
    },
)
