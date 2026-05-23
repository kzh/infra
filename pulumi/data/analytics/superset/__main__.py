import pulumi_kubernetes as k8s
import pulumi_random as random
from infra_helpers.postgres import PostgresStack, create_database_owner

import pulumi

APP_NAME = "superset"
POSTGRES_PASSWORD_SECRET_NAME = "superset-postgres"

config = pulumi.Config()
hostname = config.get("hostname") or APP_NAME
trino_stack_ref = config.get("trinoStack") or "kzh/trino/mx"
postgres_stack_ref = config.get("postgresStack") or "kzh/postgresql/mx"
database_name = config.get("databaseName") or APP_NAME
database_user = config.get("databaseUser") or APP_NAME
storage_class_name = config.get("storageClassName") or "local-path"
redis_storage_size = config.get("redisStorageSize") or "2Gi"

trino_stack = pulumi.StackReference(trino_stack_ref)
postgres = PostgresStack(postgres_stack_ref)

trino_host = pulumi.Output.format(
    "{0}.{1}.svc.cluster.local",
    trino_stack.require_output("service_name"),
    trino_stack.require_output("namespace"),
)
trino_sqlalchemy_uri = pulumi.Output.format(
    "trino://superset@{0}:8080/tpch/tiny",
    trino_host,
)


def trino_catalog_default_schema(catalog: str) -> str:
    if catalog in {"tpch", "tpcds"}:
        return "tiny"
    if catalog.startswith("pg_"):
        return "public"
    if catalog in {"clickhouse", "memory"}:
        return "default"
    return ""


def trino_catalog_datasource_name(catalog: str) -> str:
    display_names = {
        "clickhouse": "ClickHouse",
        "iceberg": "Iceberg",
        "memory": "Memory",
        "tpcds": "TPCDS",
        "tpch": "TPCH",
    }

    if catalog.startswith("pg_"):
        postgres_display_names = {
            "convexdb": "ConvexDB",
            "mlflow": "MLflow",
            "n8n": "n8n",
        }
        raw_database_name = catalog.removeprefix("pg_")
        database_name = postgres_display_names.get(
            raw_database_name,
            raw_database_name.replace("_", " ").title(),
        )
        return f"Trino PostgreSQL {database_name}"

    return f"Trino {display_names.get(catalog, catalog.replace('_', ' ').title())}"


def trino_catalog_datasource_specs(catalogs: object) -> str:
    if not isinstance(catalogs, list):
        return ""

    specs = []
    for catalog in sorted(str(catalog) for catalog in catalogs if catalog != "system"):
        schema = trino_catalog_default_schema(catalog)
        uri_path = f"/{catalog}/{schema}" if schema else f"/{catalog}"
        specs.append(
            "|".join(
                [
                    catalog,
                    trino_catalog_datasource_name(catalog),
                    uri_path,
                ]
            )
        )
    return "\n".join(specs)


trino_catalogs = trino_stack.require_output("catalogs")
trino_datasource_specs = trino_catalogs.apply(trino_catalog_datasource_specs)
trino_datasource_names = trino_catalogs.apply(
    lambda catalogs: (
        [
            trino_catalog_datasource_name(str(catalog))
            for catalog in sorted(catalogs)
            if catalog != "system"
        ]
        if isinstance(catalogs, list)
        else []
    )
)
postgres_service_host = postgres.rw_service_fqdn
database_password = random.RandomPassword(
    "superset-database-password",
    length=32,
    special=False,
)

superset_namespace = k8s.core.v1.Namespace(
    "namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(name=config.require("namespace")),
)

superset_database_owner = create_database_owner(
    role_resource_name="superset-role",
    database_resource_name="superset-database",
    provider=postgres.admin_provider("pg-admin"),
    role_name=database_user,
    database_name=database_name,
    password=database_password.result,
)
superset_role = superset_database_owner.role
superset_database = superset_database_owner.database

postgres_secret = k8s.core.v1.Secret(
    "superset-postgres-secret",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=POSTGRES_PASSWORD_SECRET_NAME,
        namespace=superset_namespace.metadata.name,
        labels={
            "app.kubernetes.io/name": APP_NAME,
        },
    ),
    string_data={
        "username": superset_role.name,
        "password": database_password.result,
    },
    type="Opaque",
    opts=pulumi.ResourceOptions(depends_on=[superset_namespace, superset_database]),
)

secret = random.RandomBytes("secret", length=42)

bootstrap_script = """
#!/bin/sh
set -eu
SITE_PACKAGES=$(/app/.venv/bin/python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')
/usr/local/bin/python -m pip install --target "$SITE_PACKAGES" --upgrade --no-cache-dir --no-deps \\
    ijson \\
    psycopg2-binary \\
    sqlalchemy-drill \\
    trino \\
    sqlalchemy-trino \\
    clickhouse-connect \\
    tzlocal \\
    lz4 \\
    orjson
""".lstrip()

init_script = """
#!/bin/sh
set -eu

echo "Upgrading DB schema..."
superset db upgrade

echo "Initializing roles..."
superset init

echo "Creating admin user..."
superset fab create-admin \\
    --username admin \\
    --firstname Superset \\
    --lastname Admin \\
    --email admin@superset.com \\
    --password admin \\
    || true

echo "Provisioning Trino datasource..."
superset set-database-uri \\
    --database_name Trino \\
    --uri "$SUPERSET_TRINO_SQLALCHEMY_URI"

echo "Provisioning Trino catalog datasources..."
printf '%s\\n' "$SUPERSET_TRINO_DATASOURCES" | while IFS='|' read -r catalog database_name uri_path; do
    if [ -z "$catalog" ]; then
        continue
    fi

    superset set-database-uri \\
        --database_name "$database_name" \\
        --uri "trino://superset@${SUPERSET_TRINO_HOST}:8080${uri_path}"
done
""".lstrip()

superset_chart = k8s.helm.v3.Release(
    "chart",
    namespace=superset_namespace.metadata.name,
    chart="superset",
    repository_opts=k8s.helm.v3.RepositoryOptsArgs(
        repo="https://apache.github.io/superset"
    ),
    version="0.15.5",
    cleanup_on_fail=True,
    force_update=True,
    timeout=900,
    wait_for_jobs=True,
    values={
        "configOverrides": {
            "secret": secret.base64.apply(lambda b: f"SECRET_KEY = '{b}'"),
        },
        "extraEnv": {
            "SERVER_WORKER_AMOUNT": "2",
            "SERVER_THREADS_AMOUNT": "4",
            "SUPERSET_TRINO_HOST": trino_host,
            "SUPERSET_TRINO_SQLALCHEMY_URI": trino_sqlalchemy_uri,
            "SUPERSET_TRINO_DATASOURCES": trino_datasource_specs,
        },
        "ingress": {
            "enabled": True,
            "ingressClassName": "tailscale",
            "pathType": "Prefix",
            "hosts": [hostname],
            "tls": [
                {
                    "hosts": [hostname],
                    "secretName": None,
                }
            ],
        },
        "supersetNode": {
            "connections": {
                "db_host": postgres_service_host,
                "db_port": "5432",
                "db_user": superset_role.name,
                "db_pass": database_password.result,
                "db_name": superset_database.name,
            },
            "resources": {
                "requests": {
                    "cpu": "100m",
                    "memory": "256Mi",
                },
                "limits": {
                    "cpu": "500m",
                    "memory": "768Mi",
                },
            },
        },
        "supersetWorker": {
            "command": [
                "/bin/sh",
                "-c",
                (
                    ". {{ .Values.configMountPath }}/superset_bootstrap.sh; "
                    "celery --app=superset.tasks.celery_app:app worker "
                    "--concurrency=1 --prefetch-multiplier=1"
                ),
            ],
            "resources": {
                "requests": {
                    "cpu": "100m",
                    "memory": "512Mi",
                },
                "limits": {
                    "cpu": "500m",
                    "memory": "1Gi",
                },
            },
        },
        "bootstrapScript": bootstrap_script,
        "init": {
            "initscript": init_script,
        },
        "postgresql": {
            "enabled": False,
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
            "master": {
                "persistence": {
                    "enabled": True,
                    "storageClass": storage_class_name,
                    "size": redis_storage_size,
                },
                "resources": {
                    "requests": {
                        "cpu": "50m",
                        "memory": "128Mi",
                    },
                    "limits": {
                        "cpu": "250m",
                        "memory": "256Mi",
                    },
                },
            },
        },
    },
    opts=pulumi.ResourceOptions(
        depends_on=[
            postgres_secret,
            superset_database,
        ],
    ),
)

pulumi.export("namespace", superset_namespace.metadata.name)
pulumi.export("hostname", hostname)
pulumi.export("url", f"https://{hostname}")
pulumi.export("postgres_database", superset_database.name)
pulumi.export("postgres_secret", postgres_secret.metadata.name)
pulumi.export("postgres_stack", postgres_stack_ref)
pulumi.export("redis_persistence_enabled", True)
pulumi.export("redis_storage_size", redis_storage_size)
pulumi.export("trino_datasource", "Trino")
pulumi.export("trino_catalog_datasources", trino_datasource_names)
pulumi.export("trino_sqlalchemy_uri", trino_sqlalchemy_uri)
