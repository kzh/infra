import pulumi
import pulumi_kubernetes as k8s
import pulumi_random as random

config = pulumi.Config()
namespace = k8s.core.v1.Namespace(
    "namespace", metadata={"name": config.require("namespace")}
)

secret = random.RandomBytes("secret", length=42)

superset = k8s.helm.v3.Release(
    "chart",
    namespace=namespace.metadata["name"],
    chart="superset",
    repository_opts=k8s.helm.v4.RepositoryOptsArgs(
        repo="https://apache.github.io/superset"
    ),
    values={
        "configOverrides": {
            "secret": secret.base64.apply(lambda b: f"SECRET_KEY = '{b}'"),
        },
        "ingress": {
            "enabled": True,
            "ingressClassName": "tailscale",
            "pathType": "Prefix",
            "hosts": [""],
            "tls": [
                {
                    "hosts": ["superset"],
                    "secretName": None,
                }
            ],
        },
        "bootstrapScript": "#!/bin/bash\npip install sqlalchemy-drill psycopg2-binary",
    },
)
