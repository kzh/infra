import pulumi
import pulumi_kubernetes as k8s
import pulumi_random as random

config = pulumi.Config()
ns = k8s.core.v1.Namespace(
    "namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=config.require("namespace"),
    ),
)

password = random.RandomPassword(
    "password",
    length=32,
    special=False,
).result.apply(lambda password: f"neo4j/{password}")

pulumi.export("password", password)

secret = k8s.core.v1.Secret(
    "password",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="neo4j-auth",
        namespace=ns.metadata.name,
    ),
    string_data={
        "NEO4J_AUTH": password,
    },
)

chart = k8s.helm.v4.Chart(
    "neo4j",
    chart="neo4j",
    repository_opts=k8s.helm.v4.RepositoryOptsArgs(
        repo="https://helm.neo4j.com/neo4j",
    ),
    version="5.26.0",
    values={
        "neo4j": {
            "name": "neo4j",
            "passwordFromSecret": secret.metadata.name,
        },
        "disableLookups": True,
        "volumes": {
            "data": {
                "mode": "dynamic",
                "dynamic": {
                    "storageClassName": "rook-ceph-block",
                },
            }
        },
        "services": {
            "neo4j": {
                "enabled": False,
            },
            "default": {
                "annotations": {
                    "tailscale.com/expose": "true",
                    "tailscale.com/hostname": "neo4j",
                },
            },
        },
    },
    namespace=ns.metadata.name,
)
