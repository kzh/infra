from shlex import quote

import pulumi_kubernetes as k8s
import pulumi_postgresql as pg
import pulumi_random as random
from infra_helpers.postgres import PostgresStack

import pulumi

APP_NAME = "marimo"
APP_VERSION = "0.23.6"
IMAGE = (
    "ghcr.io/marimo-team/marimo"
    "@sha256:e3d9ae9d6e30f19f09a23d2db294b132aa9042d9727c9bbffcc53c165c2f82b0"
)
PORT = 8080
POSTGRES_SCHEMA = "public"
POSTGRES_READER_USER = "marimo_reader"
MARIMO_CONFIG_SUBPATH = ".config/marimo"

config = pulumi.Config()

namespace_name = config.get("namespace") or APP_NAME
hostname = config.get("hostname") or APP_NAME
public_host = config.get("public_host") or hostname
public_url = (config.get("public_url") or f"https://{public_host}").rstrip("/")
storage_size = config.get("storageSize") or "20Gi"
storage_class_name = config.get("storageClass")
postgres_stack_ref = config.get("postgresStack") or "kzh/postgresql/mx"
trino_stack_ref = config.get("trinoStack") or "kzh/trino/mx"
clickhouse_stack_ref = config.get("clickhouseStack") or "kzh/clickhouse/mx"
rustfs_stack_ref = config.get("rustfsStack") or "kzh/rustfs/mx"
spark_stack_ref = config.get("sparkStack") or "kzh/spark/mx"
kafka_stack_ref = config.get("kafkaStack") or "kzh/kafka/mx"
mlflow_stack_ref = config.get("mlflowStack") or "kzh/mlflow/mx"
postgres_databases = config.get_object(
    "postgresDatabases",
    [
        "airflow",
        "app",
        "coder",
        "convexdb",
        "immich",
        "mlflow",
        "n8n",
        "postgres",
        "stitch",
        "temporal",
        "temporal_visibility",
    ],
)

labels = {
    "app": APP_NAME,
    "app.kubernetes.io/name": APP_NAME,
    "app.kubernetes.io/part-of": "analytics",
}

postgres = PostgresStack(postgres_stack_ref)
trino_stack = pulumi.StackReference(trino_stack_ref)
clickhouse_stack = pulumi.StackReference(clickhouse_stack_ref)
rustfs_stack = pulumi.StackReference(rustfs_stack_ref)
spark_stack = pulumi.StackReference(spark_stack_ref)
kafka_stack = pulumi.StackReference(kafka_stack_ref)
mlflow_stack = pulumi.StackReference(mlflow_stack_ref)

postgres_service_host = postgres.rw_service_fqdn
postgres_port = postgres.port.apply(lambda p: int(p) if p else 5432)

admin_provider = postgres.admin_provider(
    "pg-admin",
    database="postgres",
    sslmode="disable",
    host=postgres.admin_host,
    port=postgres_port,
)

postgres_reader_password = random.RandomPassword(
    "marimo-postgres-reader-password",
    length=32,
    special=False,
)

token_password = random.RandomPassword(
    "marimo-token-password",
    length=32,
    special=False,
)

postgres_reader_role = pg.Role(
    "marimo-postgres-reader-role",
    name=POSTGRES_READER_USER,
    login=True,
    password=postgres_reader_password.result,
    opts=pulumi.ResourceOptions(provider=admin_provider),
)

postgres_reader_grants: list[pulumi.Resource] = []
for database_name in postgres_databases:
    database_grant = pg.Grant(
        f"marimo-reader-{database_name}-connect",
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
        f"marimo-reader-{database_name}-schema",
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
        f"marimo-reader-{database_name}-tables",
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
        f"marimo-reader-{database_name}-sequences",
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

trino_host = pulumi.Output.format(
    "{0}.{1}.svc.cluster.local",
    trino_stack.require_output("service_name"),
    trino_stack.require_output("namespace"),
)
trino_uri = pulumi.Output.format("http://{0}:8080", trino_host)
trino_sqlalchemy_uri = pulumi.Output.format(
    "trino://marimo@{0}:8080/tpch/tiny",
    trino_host,
)
trino_catalogs = trino_stack.require_output("catalogs").apply(
    lambda catalogs: ",".join(catalogs)
)

clickhouse_http_url = "http://clickhouse.clickhouse.svc.cluster.local:8123"
spark_remote = pulumi.Output.format(
    "sc://{0}.{1}.svc.cluster.local:15002",
    spark_stack.require_output("spark_connect_hostname"),
    spark_stack.require_output("namespace"),
)
spark_ui_url = "http://spark-connect-ui.spark.svc.cluster.local:4040"
rustfs_endpoint_url = pulumi.Output.format(
    "http://{0}.{1}.svc.cluster.local:9000",
    rustfs_stack.require_output("s3_hostname"),
    rustfs_stack.require_output("namespace"),
)
kafka_bootstrap_servers = pulumi.Output.format(
    "{0}-kafka-bootstrap.{1}.svc.cluster.local:9092",
    kafka_stack.require_output("clusterName"),
    kafka_stack.require_output("namespace"),
)
mlflow_tracking_uri = pulumi.Output.format(
    "http://mlflow.{0}.svc.cluster.local",
    mlflow_stack.require_output("namespace"),
)

namespace = k8s.core.v1.Namespace(
    "marimo-namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=namespace_name,
        labels=labels,
    ),
)

workspace_pvc_args = {
    "access_modes": ["ReadWriteOnce"],
    "resources": k8s.core.v1.ResourceRequirementsArgs(
        requests={"storage": storage_size},
    ),
}
if storage_class_name:
    workspace_pvc_args["storage_class_name"] = storage_class_name

workspace = k8s.core.v1.PersistentVolumeClaim(
    "marimo-workspace",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="marimo-workspace",
        namespace=namespace.metadata.name,
        labels=labels,
    ),
    spec=k8s.core.v1.PersistentVolumeClaimSpecArgs(**workspace_pvc_args),
    opts=pulumi.ResourceOptions(
        depends_on=[namespace],
        delete_before_replace=True,
    ),
)

environment = k8s.core.v1.ConfigMap(
    "marimo-environment",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="marimo-environment",
        namespace=namespace.metadata.name,
        labels=labels,
    ),
    data={
        "TRINO_HOST": trino_host,
        "TRINO_PORT": "8080",
        "TRINO_URI": trino_uri,
        "TRINO_SQLALCHEMY_URI": trino_sqlalchemy_uri,
        "TRINO_CATALOGS": trino_catalogs,
        "CLICKHOUSE_HTTP_URL": clickhouse_http_url,
        "CLICKHOUSE_HOST": "clickhouse.clickhouse.svc.cluster.local",
        "CLICKHOUSE_HTTP_PORT": "8123",
        "SPARK_REMOTE": spark_remote,
        "SPARK_UI_URL": spark_ui_url,
        "RUSTFS_ENDPOINT_URL": rustfs_endpoint_url,
        "S3_ENDPOINT_URL": rustfs_endpoint_url,
        "AWS_DEFAULT_REGION": "us-east-1",
        "KAFKA_BOOTSTRAP_SERVERS": kafka_bootstrap_servers,
        "MLFLOW_TRACKING_URI": mlflow_tracking_uri,
        "POSTGRES_HOST": postgres_service_host,
        "POSTGRES_PORT": "5432",
        "POSTGRES_USER": postgres_reader_role.name,
        "POSTGRES_DATABASES": ",".join(postgres_databases),
        "POSTGRES_SCHEMA": POSTGRES_SCHEMA,
    },
    opts=pulumi.ResourceOptions(depends_on=[namespace, postgres_reader_role]),
)

secrets = k8s.core.v1.Secret(
    "marimo-secrets",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="marimo-secrets",
        namespace=namespace.metadata.name,
        labels=labels,
    ),
    string_data={
        "MARIMO_TOKEN_PASSWORD": token_password.result,
        "POSTGRES_PASSWORD": postgres_reader_password.result,
        "CLICKHOUSE_USER": clickhouse_stack.require_output("clickhouseAdminUsername"),
        "CLICKHOUSE_PASSWORD": clickhouse_stack.require_output(
            "clickhouseAdminPassword"
        ),
        "AWS_ACCESS_KEY_ID": rustfs_stack.require_output("access_key"),
        "AWS_SECRET_ACCESS_KEY": rustfs_stack.require_output("secret_key"),
    },
    type="Opaque",
    opts=pulumi.ResourceOptions(
        depends_on=[namespace, postgres_reader_role, *postgres_reader_grants],
    ),
)

welcome_notebook = k8s.core.v1.ConfigMap(
    "marimo-welcome-notebook",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="marimo-welcome-notebook",
        namespace=namespace.metadata.name,
        labels=labels,
    ),
    data={
        "welcome.py": r"""import marimo

__generated_with = "0.23.6"
app = marimo.App(width="medium")


@app.cell
def _():
    import os
    import marimo as mo

    mo.md("# Homelab analytics workspace")
    return mo, os


@app.cell
def _(mo, os):
    non_secret = [
        "TRINO_URI",
        "TRINO_SQLALCHEMY_URI",
        "TRINO_CATALOGS",
        "SPARK_REMOTE",
        "SPARK_UI_URL",
        "MLFLOW_TRACKING_URI",
        "KAFKA_BOOTSTRAP_SERVERS",
        "RUSTFS_ENDPOINT_URL",
        "CLICKHOUSE_HTTP_URL",
        "POSTGRES_HOST",
        "POSTGRES_DATABASES",
    ]
    secret = [
        "POSTGRES_PASSWORD",
        "CLICKHOUSE_USER",
        "CLICKHOUSE_PASSWORD",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
    ]
    rows = [
        {"name": name, "value": os.environ.get(name, "")}
        for name in non_secret
    ]
    rows.extend(
        {"name": name, "value": "set" if os.environ.get(name) else "missing"}
        for name in secret
    )
    mo.ui.table(rows)
    return


if __name__ == "__main__":
    app.run()
""",
    },
    opts=pulumi.ResourceOptions(depends_on=[namespace]),
)

allowed_origins = list(dict.fromkeys([public_url, f"https://{hostname}"]))
allowed_origin_args = " \\\n".join(
    f"    --allow-origins {quote(origin)}" for origin in allowed_origins
)

install_and_start = f"""
set -eu
cd /workspace
if [ ! -x /workspace/.venv/bin/marimo ]; then
    uv venv /workspace/.venv
fi
uv pip install --python /workspace/.venv/bin/python --upgrade \\
    marimo=={APP_VERSION} \\
    trino \\
    sqlalchemy-trino \\
    clickhouse-connect \\
    psycopg2-binary \\
    boto3 \\
    mlflow \\
    'pyspark[connect]' \\
    kafka-python \\
    pandas \\
    pyarrow \\
    polars \\
    duckdb
exec /workspace/.venv/bin/marimo edit \\
    --headless \\
    --host 0.0.0.0 \\
    --port {PORT} \\
    --proxy {quote(public_url)} \\
{allowed_origin_args} \\
    --token-password-file /var/run/marimo/MARIMO_TOKEN_PASSWORD \\
    --skip-update-check
""".strip()

deployment = k8s.apps.v1.Deployment(
    "marimo",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=APP_NAME,
        namespace=namespace.metadata.name,
        labels=labels,
    ),
    spec=k8s.apps.v1.DeploymentSpecArgs(
        replicas=1,
        strategy=k8s.apps.v1.DeploymentStrategyArgs(type="Recreate"),
        selector=k8s.meta.v1.LabelSelectorArgs(match_labels={"app": APP_NAME}),
        template=k8s.core.v1.PodTemplateSpecArgs(
            metadata=k8s.meta.v1.ObjectMetaArgs(labels=labels),
            spec=k8s.core.v1.PodSpecArgs(
                security_context=k8s.core.v1.PodSecurityContextArgs(fs_group=1000),
                init_containers=[
                    k8s.core.v1.ContainerArgs(
                        name="seed-workspace",
                        image=IMAGE,
                        command=["/bin/sh", "-ceu"],
                        args=[
                            "mkdir -p /workspace && "
                            "if [ ! -f /workspace/welcome.py ]; then "
                            "cp /bootstrap/welcome.py /workspace/welcome.py; "
                            "fi; "
                            f"mkdir -p /workspace/{MARIMO_CONFIG_SUBPATH} && "
                            f"if [ ! -f /workspace/{MARIMO_CONFIG_SUBPATH}/marimo.toml ]; then "
                            f"printf '[package_management]\\nmanager = \"uv\"\\n' > "
                            f"/workspace/{MARIMO_CONFIG_SUBPATH}/marimo.toml; "
                            "fi"
                        ],
                        volume_mounts=[
                            k8s.core.v1.VolumeMountArgs(
                                name="workspace",
                                mount_path="/workspace",
                            ),
                            k8s.core.v1.VolumeMountArgs(
                                name="welcome-notebook",
                                mount_path="/bootstrap",
                                read_only=True,
                            ),
                        ],
                    )
                ],
                containers=[
                    k8s.core.v1.ContainerArgs(
                        name=APP_NAME,
                        image=IMAGE,
                        image_pull_policy="IfNotPresent",
                        working_dir="/workspace",
                        command=["/bin/sh", "-ceu"],
                        args=[install_and_start],
                        ports=[
                            k8s.core.v1.ContainerPortArgs(
                                name="http",
                                container_port=PORT,
                            )
                        ],
                        env_from=[
                            k8s.core.v1.EnvFromSourceArgs(
                                config_map_ref=k8s.core.v1.ConfigMapEnvSourceArgs(
                                    name=environment.metadata.name,
                                ),
                            ),
                            k8s.core.v1.EnvFromSourceArgs(
                                secret_ref=k8s.core.v1.SecretEnvSourceArgs(
                                    name=secrets.metadata.name,
                                ),
                            ),
                        ],
                        volume_mounts=[
                            k8s.core.v1.VolumeMountArgs(
                                name="workspace",
                                mount_path="/workspace",
                            ),
                            k8s.core.v1.VolumeMountArgs(
                                name="workspace",
                                mount_path="/home/appuser/.config/marimo",
                                sub_path=MARIMO_CONFIG_SUBPATH,
                            ),
                            k8s.core.v1.VolumeMountArgs(
                                name="marimo-token",
                                mount_path="/var/run/marimo",
                                read_only=True,
                            ),
                        ],
                        readiness_probe=k8s.core.v1.ProbeArgs(
                            tcp_socket=k8s.core.v1.TCPSocketActionArgs(port=PORT),
                            initial_delay_seconds=10,
                            period_seconds=10,
                        ),
                        liveness_probe=k8s.core.v1.ProbeArgs(
                            tcp_socket=k8s.core.v1.TCPSocketActionArgs(port=PORT),
                            initial_delay_seconds=60,
                            period_seconds=30,
                        ),
                        resources=k8s.core.v1.ResourceRequirementsArgs(
                            requests={
                                "cpu": "100m",
                                "memory": "256Mi",
                            },
                            limits={
                                "cpu": "1",
                                "memory": "2Gi",
                            },
                        ),
                    )
                ],
                volumes=[
                    k8s.core.v1.VolumeArgs(
                        name="workspace",
                        persistent_volume_claim=k8s.core.v1.PersistentVolumeClaimVolumeSourceArgs(
                            claim_name=workspace.metadata.name,
                        ),
                    ),
                    k8s.core.v1.VolumeArgs(
                        name="welcome-notebook",
                        config_map=k8s.core.v1.ConfigMapVolumeSourceArgs(
                            name=welcome_notebook.metadata.name,
                        ),
                    ),
                    k8s.core.v1.VolumeArgs(
                        name="marimo-token",
                        secret=k8s.core.v1.SecretVolumeSourceArgs(
                            secret_name=secrets.metadata.name,
                            items=[
                                k8s.core.v1.KeyToPathArgs(
                                    key="MARIMO_TOKEN_PASSWORD",
                                    path="MARIMO_TOKEN_PASSWORD",
                                )
                            ],
                        ),
                    ),
                ],
            ),
        ),
    ),
    opts=pulumi.ResourceOptions(
        depends_on=[
            namespace,
            workspace,
            environment,
            secrets,
            welcome_notebook,
        ],
        delete_before_replace=True,
    ),
)

service = k8s.core.v1.Service(
    "marimo",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=APP_NAME,
        namespace=namespace.metadata.name,
        labels=labels,
    ),
    spec=k8s.core.v1.ServiceSpecArgs(
        type="ClusterIP",
        selector={"app": APP_NAME},
        ports=[
            k8s.core.v1.ServicePortArgs(
                name="http",
                port=PORT,
                target_port=PORT,
            )
        ],
    ),
    opts=pulumi.ResourceOptions(depends_on=[deployment]),
)

ingress = k8s.networking.v1.Ingress(
    "marimo",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=APP_NAME,
        namespace=namespace.metadata.name,
        labels=labels,
    ),
    spec=k8s.networking.v1.IngressSpecArgs(
        ingress_class_name="tailscale",
        rules=[
            k8s.networking.v1.IngressRuleArgs(
                host=hostname,
                http=k8s.networking.v1.HTTPIngressRuleValueArgs(
                    paths=[
                        k8s.networking.v1.HTTPIngressPathArgs(
                            path="/",
                            path_type="Prefix",
                            backend=k8s.networking.v1.IngressBackendArgs(
                                service=k8s.networking.v1.IngressServiceBackendArgs(
                                    name=service.metadata.name,
                                    port=k8s.networking.v1.ServiceBackendPortArgs(
                                        number=PORT,
                                    ),
                                ),
                            ),
                        )
                    ],
                ),
            )
        ],
        tls=[k8s.networking.v1.IngressTLSArgs(hosts=[hostname])],
    ),
    opts=pulumi.ResourceOptions(depends_on=[service]),
)

pulumi.export("namespace", namespace.metadata.name)
pulumi.export("hostname", hostname)
pulumi.export("public_host", public_host)
pulumi.export("url", public_url)
pulumi.export("service", service.metadata.name)
pulumi.export("workspace_pvc", workspace.metadata.name)
pulumi.export("token_secret", secrets.metadata.name)
pulumi.export("token", pulumi.Output.secret(token_password.result))
pulumi.export("image", IMAGE)
pulumi.export("marimo_version", APP_VERSION)
pulumi.export("trino_uri", trino_uri)
pulumi.export("trino_catalogs", trino_stack.require_output("catalogs"))
pulumi.export("spark_remote", spark_remote)
pulumi.export("mlflow_tracking_uri", mlflow_tracking_uri)
pulumi.export("kafka_bootstrap_servers", kafka_bootstrap_servers)
