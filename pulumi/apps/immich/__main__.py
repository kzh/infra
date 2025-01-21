import base64

import pulumi
import pulumi_kubernetes as k8s

config = pulumi.Config()
namespace = k8s.core.v1.Namespace(
    "namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=config.require("namespace"),
    ),
)

cloudnative_pg = k8s.helm.v4.Chart(
    "cloudnative-pg",
    chart="cloudnative-pg",
    namespace=namespace.metadata.name,
    repository_opts=k8s.helm.v4.RepositoryOptsArgs(
        repo="https://cloudnative-pg.github.io/charts",
    ),
    values={
        "config": {
            "clusterWide": False,
        }
    },
)

postgres = k8s.helm.v4.Chart(
    "postgres",
    chart="cluster",
    namespace=namespace.metadata.name,
    repository_opts=k8s.helm.v4.RepositoryOptsArgs(
        repo="https://cloudnative-pg.github.io/charts",
    ),
    values={
        "cluster": {
            "imageName": "ghcr.io/tensorchord/cloudnative-pgvecto.rs:16.5-v0.3.0@sha256:be3f025d79aa1b747817f478e07e71be43236e14d00d8a9eb3914146245035ba",
            "instances": 1,
            "postgresql": {"shared_preload_libraries": ["vectors.so"]},
            "annotations": {
                "pulumi.com/waitFor": "jsonpath={.status.readyInstances}=1"
            },
            "roles": [
                {
                    "name": "immich",
                    "superuser": True,
                    "login": True,
                }
            ],
            "initdb": {
                "database": "immich",
                "owner": "immich",
                "postInitSQL": [
                    'CREATE EXTENSION IF NOT EXISTS "vectors";',
                    'CREATE EXTENSION IF NOT EXISTS "cube" CASCADE;',
                    'CREATE EXTENSION IF NOT EXISTS "earthdistance" CASCADE;',
                ],
            },
            "storage": {
                "size": "20Gi",
                "storageClassName": "rook-ceph-block",
            },
        }
    },
    opts=pulumi.ResourceOptions(depends_on=[cloudnative_pg]),
)

secret = postgres.resources[0].apply(
    lambda cluster: k8s.core.v1.Secret.get("postgres-secret", f"{cluster}-superuser")
)


def from_secret(key: str):
    return secret.data.apply(lambda data: base64.b64decode(data[key]).decode("utf-8"))


db_hostname = from_secret("host")
db_username = from_secret("username")
db_password = from_secret("password")

pvc = k8s.core.v1.PersistentVolumeClaim(
    "immich-pvc",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="immich-pvc",
        namespace=namespace.metadata.name,
    ),
    spec=k8s.core.v1.PersistentVolumeClaimSpecArgs(
        access_modes=["ReadWriteOnce"],
        resources=k8s.core.v1.ResourceRequirementsArgs(
            requests={"storage": "200Gi"},
        ),
        storage_class_name="rook-ceph-block",
    ),
)

immich = k8s.helm.v4.Chart(
    "immich",
    chart="immich",
    namespace=namespace.metadata.name,
    repository_opts=k8s.helm.v4.RepositoryOptsArgs(
        repo="https://immich-app.github.io/immich-charts",
    ),
    values={
        "redis": {
            "enabled": True,
        },
        "image": {
            "tag": "v1.124.2",
        },
        "immich": {
            "persistence": {
                "library": {
                    "existingClaim": pvc.metadata.name,
                }
            }
        },
        "env": {
            "DB_HOSTNAME": db_hostname,
            "DB_USERNAME": db_username,
            "DB_PASSWORD": db_password,
            "DB_DATABASE_NAME": "immich",
        },
        "machine-learning": {
            "enabled": False,
        },
    },
    opts=pulumi.ResourceOptions(depends_on=[postgres]),
)

ingress = k8s.networking.v1.Ingress(
    "ingress",
    metadata={
        "name": "immich",
        "namespace": namespace.metadata["name"],
    },
    spec=k8s.networking.v1.IngressSpecArgs(
        ingress_class_name="tailscale",
        rules=[
            k8s.networking.v1.IngressRuleArgs(
                http=k8s.networking.v1.HTTPIngressRuleValueArgs(
                    paths=[
                        k8s.networking.v1.HTTPIngressPathArgs(
                            path="/",
                            path_type="Prefix",
                            backend=k8s.networking.v1.IngressBackendArgs(
                                service=k8s.networking.v1.IngressServiceBackendArgs(
                                    name="immich-server",
                                    port=k8s.networking.v1.ServiceBackendPortArgs(
                                        number=2283,
                                    ),
                                ),
                            ),
                        ),
                    ],
                ),
            ),
        ],
        tls=[
            k8s.networking.v1.IngressTLSArgs(
                hosts=["immich"],
            ),
        ],
    ),
    opts=pulumi.ResourceOptions(depends_on=[immich]),
)
