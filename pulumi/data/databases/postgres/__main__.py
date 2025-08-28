import base64

import pulumi_kubernetes as k8s
import pulumi_postgresql as pg
from infra_lib.k8s import add_wait_annotation, ensure_namespace, helm_chart

import pulumi

config = pulumi.Config()

ns_value = config.get("namespace", "postgresql")  # plain string to avoid Output warnings

# Manage the Namespace with a stable Pulumi name to avoid replacement
postgres_namespace = k8s.core.v1.Namespace(
    "postgresql",
    metadata=k8s.meta.v1.ObjectMetaArgs(name=ns_value),
)
pulumi.export("k8s_namespace", ns_value)


ts_hostname = config.get("ts_hostname", "postgresql")
pulumi.export("ts_hostname", ts_hostname)

pg_chart = helm_chart(
    "postgresql",
    chart="cluster",
    namespace=ns_value,
    repo="https://cloudnative-pg.github.io/charts",
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
    depends_on=[postgres_namespace],
    transformations=[add_wait_annotation],
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
    value = pg_secret.data.apply(lambda d, f=field: base64.b64decode(d.get(f, "")).decode())
    pulumi.export(key, pulumi.Output.secret(value))

# Optional: create application databases and ensure extensions
app_dbs = config.get_object("app_databases") or []  # e.g., ["immich", "stitch"]
extensions = config.get_object("extensions") or ["vector", "cube", "earthdistance"]

# Admin provider (connects to maintenance DB 'postgres') using tailscale hostname
admin_provider = pg.Provider(
    "pg-admin",
    host=ts_hostname,
    port=5432,
    username=pulumi.Output.secret(
        pg_secret.data.apply(lambda d: base64.b64decode(d.get("username", "")).decode())
    ),
    password=pulumi.Output.secret(
        pg_secret.data.apply(lambda d: base64.b64decode(d.get("password", "")).decode())
    ),
    database="postgres",
    sslmode="disable",
)

for db_name in app_dbs:
    # Provider scoped to the application database (database must already exist)
    app_provider = pg.Provider(
        f"pg-{db_name}",
        host=ts_hostname,
        port=5432,
        username=pulumi.Output.secret(
            pg_secret.data.apply(lambda d: base64.b64decode(d.get("username", "")).decode())
        ),
        password=pulumi.Output.secret(
            pg_secret.data.apply(lambda d: base64.b64decode(d.get("password", "")).decode())
        ),
        database=db_name,
        sslmode="disable",
    )

    # Ensure requested extensions in the app database
    for ext in extensions:
        pg.Extension(
            f"ext-{db_name}-{ext}",
            name=ext,
            schema="public",
            opts=pulumi.ResourceOptions(provider=app_provider, depends_on=[pg_chart]),
        )
