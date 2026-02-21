import pulumi_kubernetes as k8s

import pulumi


def ensure_namespace(name: str):
    return k8s.core.v1.Namespace(
        f"{name}-namespace",
        metadata=k8s.meta.v1.ObjectMetaArgs(name=name),
    )


def add_skip_await_annotation(*args):
    if not args:
        return

    obj = args[0].props if hasattr(args[0], "props") else args[0]
    if not isinstance(obj, dict):
        return

    metadata = obj.setdefault("metadata", {})
    annotations = metadata.setdefault("annotations", {})
    annotations["pulumi.com/skipAwait"] = "true"


config = pulumi.Config()
immich_namespace = ensure_namespace(config.require("namespace"))

# Postgres connection via required StackReference
pgref = pulumi.StackReference(config.require("postgres_stack"))

# Provider (runs locally) prefers Tailscale hostname; app (in‑cluster) uses Service DNS
_ts = pgref.get_output("ts_hostname")      # e.g., "postgresql" (tailscale L4)
_host = pgref.get_output("host")            # internal service host from producer
_ns = pgref.get_output("k8s_namespace")     # e.g., "postgresql"

pg_app_host = _ns.apply(lambda ns: f"postgresql-cluster-rw.{ns}.svc.cluster.local")
pg_provider_host = pulumi.Output.all(_ts, _host).apply(lambda t: t[0] or t[1])
pg_port = pgref.get_output("port")
pg_admin_user = pgref.get_output("username")
pg_admin_password = pgref.get_output("password")

immich_db_name = config.get("db_name") or "immich"

db_hostname = pg_app_host
db_port = pg_port
db_username = pg_admin_user
db_password = pg_admin_password

library_size = config.get("library_storage_size") or "200Gi"

# Image tag chosen via config (use bootstrap manually if needed)
image_tag = pulumi.Output.from_input(config.get("image_tag") or "v1.139.4")
redis_image_registry = config.get("redis_image_registry") or "docker.io"
redis_image_repository = config.get("redis_image_repository") or "bitnami/redis"
redis_image_tag = config.get("redis_image_tag") or "latest"
# Pin digest because redis chart default tag was removed upstream.
redis_image_digest = config.get("redis_image_digest") or "sha256:1c41e7028ac48d7a9d79d855a432eef368aa440f37c8073ae1651879b02c72f4"

pvc = k8s.core.v1.PersistentVolumeClaim(
    "immich-pvc",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="immich-pvc",
        namespace=immich_namespace.metadata.name,
    ),
    spec=k8s.core.v1.PersistentVolumeClaimSpecArgs(
        access_modes=["ReadWriteOnce"],
        resources=k8s.core.v1.VolumeResourceRequirementsArgs(requests={"storage": library_size}),
    ),
)


immich = k8s.helm.v4.Chart(
    "immich",
    chart="immich",
    version="0.9.3",
    namespace=immich_namespace.metadata.name,
    repository_opts=k8s.helm.v4.RepositoryOptsArgs(
        repo="https://immich-app.github.io/immich-charts",
    ),
    values={
        "redis": {
            "enabled": True,
            "image": {
                "registry": redis_image_registry,
                "repository": redis_image_repository,
                "tag": redis_image_tag,
                "digest": redis_image_digest,
            },
            "master": {
                "updateStrategy": {
                    "type": "RollingUpdate",
                    "rollingUpdate": {
                        "partition": 0,
                    },
                },
            },
        },
        "image": {
            "tag": image_tag,
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
            "DB_PORT": pulumi.Output.format("{}", db_port),
            "DB_USERNAME": db_username,
            "DB_PASSWORD": db_password,
            "DB_DATABASE_NAME": immich_db_name,
            "DB_VECTOR_EXTENSION": "pgvector",
        },
        "machine-learning": {
            "enabled": False,
        },
    },
    opts=pulumi.ResourceOptions(
        depends_on=[immich_namespace],
        transformations=[add_skip_await_annotation],
    ),
)

immich_ingress = k8s.networking.v1.Ingress(
    "immich-ingress",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="immich",
        namespace=immich_namespace.metadata.name,
    ),
    spec=k8s.networking.v1.IngressSpecArgs(
        ingress_class_name="tailscale",
        rules=[
            k8s.networking.v1.IngressRuleArgs(
                host="immich",
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
