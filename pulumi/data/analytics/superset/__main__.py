import pulumi_kubernetes as k8s
import pulumi_random as random

import pulumi

config = pulumi.Config()
superset_namespace = k8s.core.v1.Namespace(
    "namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(name=config.require("namespace")),
)

secret = random.RandomBytes("secret", length=42)

superset_chart = k8s.helm.v3.Release(
    "chart",
    namespace=superset_namespace.metadata.name,
    chart="superset",
    repository_opts=k8s.helm.v3.RepositoryOptsArgs(
        repo="https://apache.github.io/superset"
    ),
    version="0.15.5",
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
        "bootstrapScript": (
            "#!/bin/bash\n"
            "SITE_PACKAGES=$(/app/.venv/bin/python -c 'import sysconfig; print(sysconfig.get_paths()[\"purelib\"])')\n"
            '/usr/local/bin/python -m pip install --target "$SITE_PACKAGES" --no-deps '
            "ijson psycopg2-binary sqlalchemy-drill"
        ),
        "postgresql": {
            "image": {
                "repository": "bitnamilegacy/postgresql",
                "tag": "14.17.0-debian-12-r3",
            },
        },
        "redis": {
            "image": {
                "repository": "bitnamilegacy/redis",
                "tag": "7.0.10-debian-11-r4",
            },
        },
    },
)
