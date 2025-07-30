import pulumi
import pulumi_kubernetes as k8s
import base64


from typing import Any, cast


def add_cluster_wait(
    args: pulumi.ResourceTransformationArgs,
) -> pulumi.ResourceTransformationResult | None:
    if args.type_ == "kubernetes:postgresql.cnpg.io/v1:Cluster":
        props_input = args.props or {}
        if not isinstance(props_input, dict):
            return None
        props = cast(dict[str, Any], props_input)
        metadata = cast(dict[str, Any], props.setdefault("metadata", {}))
        annotations = cast(dict[str, Any], metadata.setdefault("annotations", {}))
        annotations["pulumi.com/waitFor"] = "condition=Ready"
        return pulumi.ResourceTransformationResult(props, args.opts)
    return None


config = pulumi.Config()

ns_value = config.get(
    "namespace", "postgresql"
)  # plain string to avoid Output warnings

postgres_namespace = k8s.core.v1.Namespace(
    "postgresql",
    metadata=k8s.meta.v1.ObjectMetaArgs(name=ns_value),
)
pulumi.export("k8s_namespace", postgres_namespace.metadata.name)


ts_hostname = config.get("ts_hostname", "postgresql")
pulumi.export("ts_hostname", ts_hostname)

pg_chart = k8s.helm.v4.Chart(
    "postgresql",
    chart="cluster",
    name="postgresql",
    namespace=ns_value,
    repository_opts=k8s.helm.v4.RepositoryOptsArgs(
        repo="https://cloudnative-pg.github.io/charts",
    ),
    version="0.3.1",
    values={
        "version": {
            "postgresql": "17",
        },
        "cluster": {
            "instances": 1,
            "imageName": "ghcr.io/tensorchord/cloudnative-vectorchord:17.5-0.4.3",
            "imagePullPolicy": "IfNotPresent",
            "postgresql": {
                "shared_preload_libraries": ["vchord.so"],
            },
            "bootstrap": {
                "initdb": {
                    "postInitSQL": ["CREATE EXTENSION IF NOT EXISTS vchord CASCADE;"],
                },
            },
            "services": {
                "disabledDefaultServices": ["r", "ro"],
                "additional": [
                    {
                        "selectorType": "rw",
                        "serviceTemplate": {
                            "metadata": {
                                "name": "postgresql-cluster-rw-ext",
                                "annotations": {
                                    "tailscale.com/expose": "true",
                                    "tailscale.com/hostname": ts_hostname,
                                },
                            },
                            "spec": {"type": "ClusterIP"},
                        },
                    }
                ],
            },
        },
    },
    opts=pulumi.ResourceOptions(transformations=[add_cluster_wait]),
)

secret_id = f"{ns_value}/postgresql-cluster-superuser"

pg_secret = k8s.core.v1.Secret.get(
    "superuser-secret",
    secret_id,
    opts=pulumi.ResourceOptions(depends_on=[pg_chart]),
)


field_map = {
    "dbname": "dbname",
    "jdbc_uri": "jdbc-uri",
    "port": "port",
    "uri": "uri",
    "user": "user",
    "host": "host",
    "pgpass": "pgpass",
    "username": "username",
    "password": "password",
}

for key, field in field_map.items():
    value = pg_secret.data.apply(
        lambda d, f=field: base64.b64decode(d.get(f, "")).decode()
    )
    pulumi.export(key, pulumi.Output.secret(value))
