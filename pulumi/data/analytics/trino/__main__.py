import pulumi_kubernetes as k8s
import pulumi_postgresql as pg
import pulumi_random as random
from infra_helpers.postgres import PostgresStack, create_database_owner

import pulumi

CHART_VERSION = "1.42.2"
TRINO_VERSION = "480"
AWS_DEFAULT_REGION = "us-east-1"
POSTGRES_READER_USER = "trino_reader"
TRINO_CREDENTIALS_SECRET_NAME = "trino-catalog-credentials"
ICEBERG_WAREHOUSE_PREFIX = "warehouse"
POSTGRES_SCHEMA = "public"
TRINO_SELECTOR = {
    "app.kubernetes.io/component": "coordinator",
    "app.kubernetes.io/instance": "trino",
    "app.kubernetes.io/name": "trino",
}

config = pulumi.Config()

namespace_name = config.get("namespace", "trino")
hostname = config.get("hostname", "trino")
postgres_stack_ref = config.get("postgresStack", "kzh/postgresql/mx")
clickhouse_stack_ref = config.get("clickhouseStack", "kzh/clickhouse/mx")
rustfs_stack_ref = config.get("rustfsStack", "kzh/rustfs/mx")
postgres_databases = config.get_object(
    "postgresDatabases",
    [
        "airflow",
        "app",
        "coder",
        "convexdb",
        "immich",
        "litellm",
        "mlflow",
        "n8n",
        "postgres",
        "stitch",
        "temporal",
        "temporal_visibility",
    ],
)
iceberg_database_name = config.get("icebergDatabaseName", "trino_iceberg")
iceberg_database_user = config.get("icebergDatabaseUser", "trino_iceberg")
iceberg_catalog_name = config.get("icebergCatalogName", "trino_iceberg")
iceberg_bucket = config.get("icebergBucket", "trino-iceberg")
iceberg_warehouse = f"s3://{iceberg_bucket}/{ICEBERG_WAREHOUSE_PREFIX}"

labels = {
    "app.kubernetes.io/name": "trino",
    "app.kubernetes.io/part-of": "analytics",
}


postgres_stack = PostgresStack(postgres_stack_ref)
clickhouse_stack = pulumi.StackReference(clickhouse_stack_ref)
rustfs_stack = pulumi.StackReference(rustfs_stack_ref)

postgres_service_host = postgres_stack.rw_service_fqdn
rustfs_s3_endpoint_url = pulumi.Output.format(
    "http://{0}.{1}.svc.cluster.local:9000",
    rustfs_stack.require_output("s3_hostname"),
    rustfs_stack.require_output("namespace"),
)

namespace = k8s.core.v1.Namespace(
    "trino-namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=namespace_name,
        labels=labels,
    ),
)

admin_provider = postgres_stack.admin_provider(
    "pg-admin",
    database="postgres",
    sslmode="disable",
)

postgres_reader_password = random.RandomPassword(
    "postgres-reader-password",
    length=32,
    special=False,
)

iceberg_database_password = random.RandomPassword(
    "iceberg-database-password",
    length=32,
    special=False,
)

postgres_reader_role = pg.Role(
    "postgres-reader-role",
    name=POSTGRES_READER_USER,
    login=True,
    password=postgres_reader_password.result,
    opts=pulumi.ResourceOptions(provider=admin_provider),
)

iceberg_database_owner = create_database_owner(
    role_resource_name="iceberg-role",
    database_resource_name="iceberg-database",
    provider=admin_provider,
    role_name=iceberg_database_user,
    database_name=iceberg_database_name,
    password=iceberg_database_password.result,
)
iceberg_role = iceberg_database_owner.role
iceberg_database = iceberg_database_owner.database

postgres_reader_grants: list[pulumi.Resource] = []
for database_name in postgres_databases:
    database_grant = pg.Grant(
        f"postgres-reader-{database_name}-connect",
        database=database_name,
        object_type="database",
        privileges=["CONNECT"],
        role=postgres_reader_role.name,
        opts=pulumi.ResourceOptions(
            provider=admin_provider,
            depends_on=[postgres_reader_role],
        ),
    )

    schema_grant = pg.Grant(
        f"postgres-reader-{database_name}-schema",
        database=database_name,
        object_type="schema",
        privileges=["USAGE"],
        role=postgres_reader_role.name,
        schema=POSTGRES_SCHEMA,
        opts=pulumi.ResourceOptions(
            provider=admin_provider,
            depends_on=[database_grant],
        ),
    )

    table_grant = pg.Grant(
        f"postgres-reader-{database_name}-tables",
        database=database_name,
        object_type="table",
        privileges=["SELECT"],
        role=postgres_reader_role.name,
        schema=POSTGRES_SCHEMA,
        opts=pulumi.ResourceOptions(
            provider=admin_provider,
            depends_on=[schema_grant],
        ),
    )

    sequence_grant = pg.Grant(
        f"postgres-reader-{database_name}-sequences",
        database=database_name,
        object_type="sequence",
        privileges=["SELECT", "USAGE"],
        role=postgres_reader_role.name,
        schema=POSTGRES_SCHEMA,
        opts=pulumi.ResourceOptions(
            provider=admin_provider,
            depends_on=[schema_grant],
        ),
    )

    postgres_reader_grants.extend(
        [database_grant, schema_grant, table_grant, sequence_grant]
    )

catalog_credentials = k8s.core.v1.Secret(
    "trino-catalog-credentials",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=TRINO_CREDENTIALS_SECRET_NAME,
        namespace=namespace.metadata.name,
        labels=labels,
    ),
    type="Opaque",
    string_data={
        "TRINO_POSTGRES_USER": postgres_reader_role.name,
        "TRINO_POSTGRES_PASSWORD": postgres_reader_password.result,
        "TRINO_CLICKHOUSE_USER": clickhouse_stack.require_output(
            "clickhouseAdminUsername"
        ),
        "TRINO_CLICKHOUSE_PASSWORD": clickhouse_stack.require_output(
            "clickhouseAdminPassword"
        ),
        "TRINO_ICEBERG_JDBC_USER": iceberg_role.name,
        "TRINO_ICEBERG_JDBC_PASSWORD": iceberg_database_password.result,
        "TRINO_S3_ACCESS_KEY": rustfs_stack.require_output("access_key"),
        "TRINO_S3_SECRET_KEY": rustfs_stack.require_output("secret_key"),
    },
    opts=pulumi.ResourceOptions(
        depends_on=[
            namespace,
            postgres_reader_role,
            iceberg_role,
        ],
    ),
)

iceberg_catalog_tables_job = k8s.batch.v1.Job(
    "trino-iceberg-catalog-tables",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="trino-iceberg-catalog-tables",
        namespace=namespace.metadata.name,
        labels=labels,
        annotations={
            "pulumi.com/waitFor": "jsonpath={.status.succeeded}=1",
        },
    ),
    spec=k8s.batch.v1.JobSpecArgs(
        backoff_limit=4,
        ttl_seconds_after_finished=86400,
        template=k8s.core.v1.PodTemplateSpecArgs(
            metadata=k8s.meta.v1.ObjectMetaArgs(labels=labels),
            spec=k8s.core.v1.PodSpecArgs(
                restart_policy="OnFailure",
                containers=[
                    k8s.core.v1.ContainerArgs(
                        name="create-iceberg-catalog-tables",
                        image="docker.io/library/postgres:18-alpine",
                        command=["sh", "-ceu"],
                        args=[
                            """
psql -v ON_ERROR_STOP=1 <<'SQL'
CREATE TABLE IF NOT EXISTS iceberg_tables (
    catalog_name VARCHAR(255) NOT NULL,
    table_namespace VARCHAR(255) NOT NULL,
    table_name VARCHAR(255) NOT NULL,
    metadata_location VARCHAR(1000),
    previous_metadata_location VARCHAR(1000),
    PRIMARY KEY (catalog_name, table_namespace, table_name)
);

ALTER TABLE iceberg_tables
    ADD COLUMN IF NOT EXISTS iceberg_type VARCHAR(5);

CREATE TABLE IF NOT EXISTS iceberg_namespace_properties (
    catalog_name VARCHAR(255) NOT NULL,
    namespace VARCHAR(255) NOT NULL,
    property_key VARCHAR(255),
    property_value VARCHAR(1000),
    PRIMARY KEY (catalog_name, namespace, property_key)
);
SQL
""".strip(),
                        ],
                        env=[
                            k8s.core.v1.EnvVarArgs(
                                name="PGHOST",
                                value=postgres_service_host,
                            ),
                            k8s.core.v1.EnvVarArgs(
                                name="PGPORT",
                                value="5432",
                            ),
                            k8s.core.v1.EnvVarArgs(
                                name="PGDATABASE",
                                value=iceberg_database.name,
                            ),
                            k8s.core.v1.EnvVarArgs(
                                name="PGUSER",
                                value=iceberg_role.name,
                            ),
                            k8s.core.v1.EnvVarArgs(
                                name="PGPASSWORD",
                                value_from=k8s.core.v1.EnvVarSourceArgs(
                                    secret_key_ref=k8s.core.v1.SecretKeySelectorArgs(
                                        name=TRINO_CREDENTIALS_SECRET_NAME,
                                        key="TRINO_ICEBERG_JDBC_PASSWORD",
                                    ),
                                ),
                            ),
                        ],
                    )
                ],
            ),
        ),
    ),
    opts=pulumi.ResourceOptions(
        depends_on=[
            namespace,
            catalog_credentials,
            iceberg_database,
        ],
        delete_before_replace=True,
    ),
)

iceberg_bucket_job = k8s.batch.v1.Job(
    "trino-iceberg-bucket",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="trino-iceberg-bucket",
        namespace=namespace.metadata.name,
        labels=labels,
        annotations={
            "pulumi.com/waitFor": "jsonpath={.status.succeeded}=1",
        },
    ),
    spec=k8s.batch.v1.JobSpecArgs(
        backoff_limit=4,
        ttl_seconds_after_finished=86400,
        template=k8s.core.v1.PodTemplateSpecArgs(
            metadata=k8s.meta.v1.ObjectMetaArgs(labels=labels),
            spec=k8s.core.v1.PodSpecArgs(
                restart_policy="OnFailure",
                containers=[
                    k8s.core.v1.ContainerArgs(
                        name="create-iceberg-bucket",
                        image="quay.io/minio/mc:latest",
                        command=["sh", "-ceu"],
                        args=[
                            """
mc alias set rustfs "$S3_ENDPOINT_URL" "$S3_ACCESS_KEY" "$S3_SECRET_KEY"
mc mb --ignore-existing "rustfs/$S3_BUCKET"
""".strip(),
                        ],
                        env=[
                            k8s.core.v1.EnvVarArgs(
                                name="S3_ENDPOINT_URL",
                                value=rustfs_s3_endpoint_url,
                            ),
                            k8s.core.v1.EnvVarArgs(
                                name="S3_BUCKET",
                                value=iceberg_bucket,
                            ),
                            k8s.core.v1.EnvVarArgs(
                                name="S3_ACCESS_KEY",
                                value_from=k8s.core.v1.EnvVarSourceArgs(
                                    secret_key_ref=k8s.core.v1.SecretKeySelectorArgs(
                                        name=TRINO_CREDENTIALS_SECRET_NAME,
                                        key="TRINO_S3_ACCESS_KEY",
                                    ),
                                ),
                            ),
                            k8s.core.v1.EnvVarArgs(
                                name="S3_SECRET_KEY",
                                value_from=k8s.core.v1.EnvVarSourceArgs(
                                    secret_key_ref=k8s.core.v1.SecretKeySelectorArgs(
                                        name=TRINO_CREDENTIALS_SECRET_NAME,
                                        key="TRINO_S3_SECRET_KEY",
                                    ),
                                ),
                            ),
                        ],
                    )
                ],
            ),
        ),
    ),
    opts=pulumi.ResourceOptions(
        depends_on=[namespace, catalog_credentials],
        delete_before_replace=True,
    ),
)


def postgres_catalog(database_name: str) -> pulumi.Output[str]:
    return pulumi.Output.concat(
        "connector.name=postgresql\n",
        "connection-url=jdbc:postgresql://",
        postgres_service_host,
        ":5432/",
        database_name,
        "\n",
        "connection-user=${ENV:TRINO_POSTGRES_USER}\n",
        "connection-password=${ENV:TRINO_POSTGRES_PASSWORD}\n",
    )


catalogs: dict[str, pulumi.Input[str]] = {
    "tpch": "connector.name=tpch\ntpch.splits-per-node=4\n",
    "tpcds": "connector.name=tpcds\ntpcds.splits-per-node=4\n",
    "memory": "connector.name=memory\nmemory.max-data-per-node=128MB\n",
    "clickhouse": (
        "connector.name=clickhouse\n"
        "connection-url=jdbc:clickhouse:http://clickhouse.clickhouse.svc.cluster.local:8123/default?compress=0\n"
        "connection-user=${ENV:TRINO_CLICKHOUSE_USER}\n"
        "connection-password=${ENV:TRINO_CLICKHOUSE_PASSWORD}\n"
    ),
    "iceberg": pulumi.Output.concat(
        "connector.name=iceberg\n",
        "iceberg.catalog.type=jdbc\n",
        "iceberg.jdbc-catalog.catalog-name=",
        iceberg_catalog_name,
        "\n",
        "iceberg.jdbc-catalog.driver-class=org.postgresql.Driver\n",
        "iceberg.jdbc-catalog.connection-url=jdbc:postgresql://",
        postgres_service_host,
        ":5432/",
        iceberg_database.name,
        "\n",
        "iceberg.jdbc-catalog.connection-user=${ENV:TRINO_ICEBERG_JDBC_USER}\n",
        "iceberg.jdbc-catalog.connection-password=${ENV:TRINO_ICEBERG_JDBC_PASSWORD}\n",
        "iceberg.jdbc-catalog.default-warehouse-dir=",
        iceberg_warehouse,
        "\n",
        "fs.native-s3.enabled=true\n",
        "s3.endpoint=",
        rustfs_s3_endpoint_url,
        "\n",
        "s3.region=",
        AWS_DEFAULT_REGION,
        "\n",
        "s3.path-style-access=true\n",
        "s3.aws-access-key=${ENV:TRINO_S3_ACCESS_KEY}\n",
        "s3.aws-secret-key=${ENV:TRINO_S3_SECRET_KEY}\n",
    ),
}

for database_name in postgres_databases:
    catalogs[f"pg_{database_name}"] = postgres_catalog(database_name)

trino_chart = k8s.helm.v4.Chart(
    "trino",
    chart="trino",
    namespace=namespace.metadata.name,
    version=CHART_VERSION,
    repository_opts=k8s.helm.v4.RepositoryOptsArgs(
        repo="https://trinodb.github.io/charts",
    ),
    values={
        "fullnameOverride": "trino",
        "server": {
            "workers": 1,
            "config": {
                "query": {
                    "maxMemory": "2GB",
                },
            },
        },
        "coordinator": {
            "jvm": {
                "maxHeapSize": "2G",
            },
            "config": {
                "query": {
                    "maxMemoryPerNode": "1GB",
                },
            },
        },
        "worker": {
            "jvm": {
                "maxHeapSize": "2G",
            },
            "config": {
                "query": {
                    "maxMemoryPerNode": "1GB",
                },
            },
        },
        "service": {
            "type": "ClusterIP",
            "port": 8080,
        },
        "catalogs": catalogs,
        "envFrom": [
            {
                "secretRef": {
                    "name": TRINO_CREDENTIALS_SECRET_NAME,
                },
            }
        ],
        "serviceAccount": {
            "create": True,
            "name": "trino",
        },
        "resources": {},
    },
    opts=pulumi.ResourceOptions(
        depends_on=[
            namespace,
            catalog_credentials,
            iceberg_catalog_tables_job,
            iceberg_bucket_job,
            *postgres_reader_grants,
        ],
    ),
)

trino_tailscale_service = k8s.core.v1.Service(
    "trino-tailscale-service",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="trino-tailscale",
        namespace=namespace.metadata.name,
        labels=labels,
        annotations={
            "tailscale.com/expose": "true",
            "tailscale.com/hostname": hostname,
        },
    ),
    spec=k8s.core.v1.ServiceSpecArgs(
        type="ClusterIP",
        selector=TRINO_SELECTOR,
        ports=[
            k8s.core.v1.ServicePortArgs(
                name="http",
                port=8080,
                target_port="http",
                protocol="TCP",
            )
        ],
    ),
    opts=pulumi.ResourceOptions(depends_on=[trino_chart]),
)

catalog_names = sorted(catalogs.keys())
postgres_catalog_names = sorted(
    catalog_name for catalog_name in catalog_names if catalog_name.startswith("pg_")
)

pulumi.export("namespace", namespace.metadata.name)
pulumi.export("chart_version", CHART_VERSION)
pulumi.export("trino_version", TRINO_VERSION)
pulumi.export("hostname", hostname)
pulumi.export("url", pulumi.Output.format("http://{0}:8080", hostname))
pulumi.export("release_name", "trino")
pulumi.export("service_name", "trino")
pulumi.export("tailscale_service_name", trino_tailscale_service.metadata.name)
pulumi.export("catalogs", catalog_names)
pulumi.export("postgres_catalogs", postgres_catalog_names)
pulumi.export("iceberg_catalog", "iceberg")
pulumi.export("iceberg_jdbc_catalog_name", iceberg_catalog_name)
pulumi.export("iceberg_database", iceberg_database.name)
pulumi.export("iceberg_bucket", iceberg_bucket)
pulumi.export("iceberg_warehouse", iceberg_warehouse)
pulumi.export("credentials_secret", catalog_credentials.metadata.name)
