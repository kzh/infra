import pulumi_kubernetes as k8s

import pulumi


def ensure_namespace(name: str):
    return k8s.core.v1.Namespace(
        f"{name}-namespace",
        metadata=k8s.meta.v1.ObjectMetaArgs(name=name),
    )


def add_skip_await_annotation(
    args: pulumi.ResourceTransformArgs,
) -> pulumi.ResourceTransformResult | None:
    if not isinstance(args.props, dict):
        return None

    props = dict(args.props)
    metadata = dict(props.get("metadata") or {})
    annotations = dict(metadata.get("annotations") or {})
    annotations["pulumi.com/skipAwait"] = "true"
    metadata["annotations"] = annotations
    props["metadata"] = metadata
    return pulumi.ResourceTransformResult(props=props, opts=args.opts)


def preserve_immich_server_selector(
    args: pulumi.ResourceTransformArgs,
) -> pulumi.ResourceTransformResult | None:
    if not isinstance(args.props, dict):
        return None

    props = dict(args.props)
    metadata = props.get("metadata") or {}
    if metadata.get("name") != "immich-server":
        return None

    kind = props.get("kind")
    spec = dict(props.get("spec") or {})
    changed = False
    if kind == "Service":
        selector = dict(spec.get("selector") or {})
        changed = selector.pop("app.kubernetes.io/controller", None) is not None
        spec["selector"] = selector
    elif kind == "Deployment":
        selector = dict(spec.get("selector") or {})
        match_labels = dict(selector.get("matchLabels") or {})
        changed = match_labels.pop("app.kubernetes.io/controller", None) is not None
        selector["matchLabels"] = match_labels
        spec["selector"] = selector

        template = dict(spec.get("template") or {})
        template_metadata = dict(template.get("metadata") or {})
        pod_labels = dict(template_metadata.get("labels") or {})
        changed = (
            pod_labels.pop("app.kubernetes.io/controller", None) is not None or changed
        )
        template_metadata["labels"] = pod_labels
        template["metadata"] = template_metadata
        spec["template"] = template

    if not changed:
        return None

    props["spec"] = spec
    return pulumi.ResourceTransformResult(props=props, opts=args.opts)


config = pulumi.Config()
immich_namespace = ensure_namespace(config.require("namespace"))

# Production uses the Postgres stack outputs; dev-preview can still pass explicit
# connection config without needing a separate Postgres stack.
postgres_stack = config.get("postgres_stack")
if postgres_stack:
    pgref = pulumi.StackReference(postgres_stack)
    pg_app_host = pgref.require_output("rw_service_fqdn")
    pg_port = pgref.require_output("port")
    pg_admin_user = pgref.require_output("username")
    pg_admin_password = pgref.require_output("password")
else:
    pg_app_host = pulumi.Output.from_input(config.require("pg_host"))
    pg_port = pulumi.Output.from_input(config.require("pg_port"))
    pg_admin_user = pulumi.Output.from_input(config.require("pg_admin_user"))
    pg_admin_password = config.require_secret("pg_admin_password")

immich_db_name = config.get("db_name") or "immich"

db_hostname = pg_app_host
db_port = pg_port
db_username = pg_admin_user
db_password = pg_admin_password

library_size = config.get("library_storage_size") or "200Gi"

image_tag = pulumi.Output.from_input(config.get("image_tag") or "v2.7.5")

pvc = k8s.core.v1.PersistentVolumeClaim(
    "immich-pvc",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="immich-pvc",
        namespace=immich_namespace.metadata.name,
    ),
    spec=k8s.core.v1.PersistentVolumeClaimSpecArgs(
        access_modes=["ReadWriteOnce"],
        resources=k8s.core.v1.VolumeResourceRequirementsArgs(
            requests={"storage": library_size}
        ),
    ),
)

db_secret = k8s.core.v1.Secret(
    "immich-db-credentials",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="immich-db-credentials",
        namespace=immich_namespace.metadata.name,
    ),
    string_data={
        "DB_PASSWORD": db_password,
    },
    type="Opaque",
    opts=pulumi.ResourceOptions(depends_on=[immich_namespace]),
)

immich = k8s.helm.v4.Chart(
    "immich",
    chart="immich",
    version="0.11.1",
    namespace=immich_namespace.metadata.name,
    repository_opts=k8s.helm.v4.RepositoryOptsArgs(
        repo="https://immich-app.github.io/immich-charts",
    ),
    values={
        "controllers": {
            "main": {
                "containers": {
                    "main": {
                        "image": {
                            "tag": image_tag,
                        },
                        "env": {
                            "DB_HOSTNAME": db_hostname,
                            "DB_PORT": pulumi.Output.format("{}", db_port),
                            "DB_USERNAME": db_username,
                            "DB_PASSWORD": {
                                "valueFrom": {
                                    "secretKeyRef": {
                                        "name": db_secret.metadata.name,
                                        "key": "DB_PASSWORD",
                                    },
                                },
                            },
                            "DB_DATABASE_NAME": immich_db_name,
                            "DB_VECTOR_EXTENSION": "pgvector",
                        },
                    },
                },
            },
        },
        "valkey": {
            "enabled": True,
            "persistence": {
                "data": {
                    "enabled": True,
                    "size": "8Gi",
                    "type": "persistentVolumeClaim",
                    "accessMode": "ReadWriteOnce",
                },
            },
        },
        "immich": {
            "persistence": {
                "library": {
                    "existingClaim": pvc.metadata.name,
                }
            }
        },
        "machine-learning": {
            "enabled": False,
        },
    },
    opts=pulumi.ResourceOptions(
        depends_on=[immich_namespace, db_secret],
        transforms=[add_skip_await_annotation, preserve_immich_server_selector],
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
