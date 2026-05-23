import base64

import pulumi_kubernetes as k8s
import pulumi_random as random
from infra_helpers.postgres import PostgresStack, create_database_owner

import pulumi

config = pulumi.Config()

APP_NAME = "langfuse"
CHART = "langfuse"
CHART_REPOSITORY = "https://langfuse.github.io/langfuse-k8s"
AWS_DEFAULT_REGION = "us-east-1"

namespace_name = config.get("namespace") or APP_NAME
hostname = config.get("hostname") or APP_NAME
public_url = config.get("publicUrl") or f"https://{hostname}"
chart_version = config.get("chart_version") or "1.5.31"
service_name = config.get("service_name") or f"{APP_NAME}-web"
service_port = config.get_int("service_port") or 3000
redis_storage_size = config.get("redis_storage_size") or "2Gi"
storage_class_name = config.get("storage_class")

postgres_stack_ref = config.get("postgres_stack_ref") or "kzh/postgresql/mx"
postgres_admin_db = config.get("postgres_admin_db") or "postgres"
postgres_sslmode = config.get("postgres_sslmode") or "disable"
db_name = config.get("db_name") or APP_NAME
db_user = config.get("db_user") or APP_NAME

clickhouse_stack_ref = config.get("clickhouse_stack_ref") or "kzh/clickhouse/mx"
clickhouse_namespace = config.get("clickhouse_namespace") or "clickhouse"
clickhouse_service = config.get("clickhouse_service") or "clickhouse"
clickhouse_database = config.get("clickhouse_database") or APP_NAME
clickhouse_http_port = config.get_int("clickhouse_http_port") or 8123
clickhouse_native_port = config.get_int("clickhouse_native_port") or 9000
clickhouse_client_image = (
    config.get("clickhouse_client_image") or "clickhouse/clickhouse-server:26.4.2.10"
)

rustfs_stack_ref = config.get("rustfs_stack_ref") or "kzh/rustfs/mx"
s3_bucket = config.get("s3_bucket") or APP_NAME
s3_region = config.get("s3_region") or AWS_DEFAULT_REGION

sign_up_disabled = config.get_bool("sign_up_disabled")
telemetry_enabled = config.get_bool("telemetry_enabled")

if sign_up_disabled is None:
    sign_up_disabled = False
if telemetry_enabled is None:
    telemetry_enabled = False

internal_url = (
    f"http://{service_name}.{namespace_name}.svc.cluster.local:{service_port}"
)
clickhouse_host = f"{clickhouse_service}.{clickhouse_namespace}.svc.cluster.local"

labels = {
    "app": APP_NAME,
    "app.kubernetes.io/name": APP_NAME,
    "app.kubernetes.io/part-of": APP_NAME,
}


def persistence(size: str) -> dict[str, object]:
    values: dict[str, object] = {"size": size}
    if storage_class_name:
        values["storageClass"] = storage_class_name
    return values


def secret_data(value: pulumi.Input[object]) -> pulumi.Output[str]:
    return pulumi.Output.secret(
        pulumi.Output.from_input(value).apply(
            lambda raw: base64.b64encode(str(raw).encode("utf-8")).decode("ascii")
        )
    )


postgres = PostgresStack(postgres_stack_ref)
clickhouse_stack = pulumi.StackReference(clickhouse_stack_ref)
rustfs_stack = pulumi.StackReference(rustfs_stack_ref)

postgres_host = postgres.rw_service_fqdn
postgres_port = postgres.port.apply(lambda p: int(p) if p else 5432)
rustfs_s3_endpoint_url = pulumi.Output.format(
    "http://{0}.{1}.svc.cluster.local:9000",
    rustfs_stack.require_output("s3_hostname"),
    rustfs_stack.require_output("namespace"),
)

langfuse_salt = random.RandomBytes("langfuse-salt", length=32)
encryption_key = random.RandomBytes("langfuse-encryption-key", length=32)
nextauth_secret = random.RandomBytes("langfuse-nextauth-secret", length=32)
db_password = random.RandomPassword(
    "langfuse-db-password",
    length=40,
    special=False,
)
redis_password = random.RandomPassword(
    "langfuse-redis-password",
    length=40,
    special=False,
)

database_owner = create_database_owner(
    role_resource_name="langfuse-db-role",
    database_resource_name="langfuse-database",
    provider=postgres.admin_provider(
        "langfuse-pg-admin",
        database=postgres_admin_db,
        sslmode=postgres_sslmode,
        host=postgres.admin_host,
        port=postgres_port,
    ),
    role_name=db_user,
    database_name=db_name,
    password=db_password.result,
)
db_role = database_owner.role
database = database_owner.database

namespace = k8s.core.v1.Namespace(
    "langfuse-namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=namespace_name,
        labels=labels,
    ),
)

app_secret = k8s.core.v1.Secret(
    "langfuse-app-secrets",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="langfuse-app-secrets",
        namespace=namespace.metadata.name,
        labels=labels,
    ),
    type="Opaque",
    string_data={
        "salt": langfuse_salt.base64,
        "encryption-key": encryption_key.hex,
        "nextauth-secret": nextauth_secret.base64,
    },
    opts=pulumi.ResourceOptions(depends_on=[namespace]),
)

postgres_secret = k8s.core.v1.Secret(
    "langfuse-postgresql-external-auth",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="langfuse-postgresql-external-auth",
        namespace=namespace.metadata.name,
        labels=labels,
    ),
    type="Opaque",
    data={
        "password": secret_data(db_password.result),
    },
    opts=pulumi.ResourceOptions(depends_on=[namespace, database]),
)

redis_secret = k8s.core.v1.Secret(
    "langfuse-redis-auth",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="langfuse-redis-auth",
        namespace=namespace.metadata.name,
        labels=labels,
    ),
    type="Opaque",
    string_data={
        "password": redis_password.result,
    },
    opts=pulumi.ResourceOptions(depends_on=[namespace]),
)

clickhouse_secret = k8s.core.v1.Secret(
    "langfuse-clickhouse-external-auth",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="langfuse-clickhouse-external-auth",
        namespace=namespace.metadata.name,
        labels=labels,
    ),
    type="Opaque",
    data={
        "password": secret_data(
            clickhouse_stack.require_output("clickhouseAdminPassword")
        ),
    },
    opts=pulumi.ResourceOptions(depends_on=[namespace]),
)

s3_secret = k8s.core.v1.Secret(
    "langfuse-rustfs-auth",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="langfuse-rustfs-auth",
        namespace=namespace.metadata.name,
        labels=labels,
    ),
    type="Opaque",
    data={
        "access-key-id": secret_data(rustfs_stack.require_output("access_key")),
        "secret-access-key": secret_data(rustfs_stack.require_output("secret_key")),
    },
    opts=pulumi.ResourceOptions(depends_on=[namespace]),
)

clickhouse_database_job = k8s.batch.v1.Job(
    "langfuse-clickhouse-database",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="langfuse-clickhouse-database",
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
                        name="create-database",
                        image=clickhouse_client_image,
                        command=["sh", "-ceu"],
                        args=[
                            """
clickhouse-client \
  --host "$CLICKHOUSE_HOST" \
  --port "$CLICKHOUSE_PORT" \
  --user "$CLICKHOUSE_USER" \
  --password "$CLICKHOUSE_PASSWORD" \
  --query "CREATE DATABASE IF NOT EXISTS $CLICKHOUSE_DATABASE"
""".strip(),
                        ],
                        env=[
                            k8s.core.v1.EnvVarArgs(
                                name="CLICKHOUSE_HOST",
                                value=clickhouse_host,
                            ),
                            k8s.core.v1.EnvVarArgs(
                                name="CLICKHOUSE_PORT",
                                value=str(clickhouse_native_port),
                            ),
                            k8s.core.v1.EnvVarArgs(
                                name="CLICKHOUSE_USER",
                                value=clickhouse_stack.require_output(
                                    "clickhouseAdminUsername"
                                ),
                            ),
                            k8s.core.v1.EnvVarArgs(
                                name="CLICKHOUSE_DATABASE",
                                value=clickhouse_database,
                            ),
                            k8s.core.v1.EnvVarArgs(
                                name="CLICKHOUSE_PASSWORD",
                                value_from=k8s.core.v1.EnvVarSourceArgs(
                                    secret_key_ref=k8s.core.v1.SecretKeySelectorArgs(
                                        name=clickhouse_secret.metadata.name,
                                        key="password",
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
        depends_on=[namespace, clickhouse_secret],
        delete_before_replace=True,
    ),
)

s3_bucket_job = k8s.batch.v1.Job(
    "langfuse-s3-bucket",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="langfuse-s3-bucket",
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
                        name="create-bucket",
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
                                value=s3_bucket,
                            ),
                            k8s.core.v1.EnvVarArgs(
                                name="S3_ACCESS_KEY",
                                value_from=k8s.core.v1.EnvVarSourceArgs(
                                    secret_key_ref=k8s.core.v1.SecretKeySelectorArgs(
                                        name=s3_secret.metadata.name,
                                        key="access-key-id",
                                    ),
                                ),
                            ),
                            k8s.core.v1.EnvVarArgs(
                                name="S3_SECRET_KEY",
                                value_from=k8s.core.v1.EnvVarSourceArgs(
                                    secret_key_ref=k8s.core.v1.SecretKeySelectorArgs(
                                        name=s3_secret.metadata.name,
                                        key="secret-access-key",
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
        depends_on=[namespace, s3_secret],
        delete_before_replace=True,
    ),
)

chart_values: dict[str, object] = {
    "fullnameOverride": APP_NAME,
    "langfuse": {
        "features": {
            "telemetryEnabled": telemetry_enabled,
            "signUpDisabled": sign_up_disabled,
        },
        "nextauth": {
            "url": public_url,
            "secret": {
                "secretKeyRef": {
                    "name": app_secret.metadata.name,
                    "key": "nextauth-secret",
                },
            },
        },
        "salt": {
            "secretKeyRef": {
                "name": app_secret.metadata.name,
                "key": "salt",
            },
        },
        "encryptionKey": {
            "secretKeyRef": {
                "name": app_secret.metadata.name,
                "key": "encryption-key",
            },
        },
        "serviceAccount": {
            "create": True,
            "automountServiceAccountToken": False,
        },
        "ingress": {
            "enabled": False,
        },
        "image": {
            "pullPolicy": "IfNotPresent",
        },
        "web": {
            "service": {
                "type": "ClusterIP",
                "port": service_port,
            },
            "resources": {
                "requests": {
                    "cpu": "100m",
                    "memory": "384Mi",
                },
                "limits": {
                    "cpu": "1",
                    "memory": "1Gi",
                },
            },
        },
        "worker": {
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
    },
    "postgresql": {
        "deploy": False,
        "host": postgres_host,
        "port": postgres_port,
        "auth": {
            "username": db_role.name,
            "database": db_name,
            "existingSecret": postgres_secret.metadata.name,
            "secretKeys": {
                "userPasswordKey": "password",
                "adminPasswordKey": "password",
            },
        },
    },
    "redis": {
        "deploy": True,
        "auth": {
            "username": "default",
            "existingSecret": redis_secret.metadata.name,
            "existingSecretPasswordKey": "password",
            "database": 0,
        },
        "primary": {
            "persistence": persistence(redis_storage_size),
        },
    },
    "clickhouse": {
        "deploy": False,
        "host": clickhouse_host,
        "httpPort": clickhouse_http_port,
        "nativePort": clickhouse_native_port,
        "database": clickhouse_database,
        "auth": {
            "username": clickhouse_stack.require_output("clickhouseAdminUsername"),
            "existingSecret": clickhouse_secret.metadata.name,
            "existingSecretKey": "password",
        },
        "clusterEnabled": False,
    },
    "s3": {
        "deploy": False,
        "bucket": s3_bucket,
        "region": s3_region,
        "endpoint": rustfs_s3_endpoint_url,
        "forcePathStyle": True,
        "accessKeyId": {
            "secretKeyRef": {
                "name": s3_secret.metadata.name,
                "key": "access-key-id",
            },
        },
        "secretAccessKey": {
            "secretKeyRef": {
                "name": s3_secret.metadata.name,
                "key": "secret-access-key",
            },
        },
    },
}

release = k8s.helm.v3.Release(
    "langfuse",
    chart=CHART,
    name=APP_NAME,
    namespace=namespace.metadata.name,
    version=chart_version,
    repository_opts=k8s.helm.v3.RepositoryOptsArgs(
        repo=CHART_REPOSITORY,
    ),
    values=chart_values,
    timeout=900,
    wait_for_jobs=True,
    cleanup_on_fail=True,
    opts=pulumi.ResourceOptions(
        depends_on=[
            namespace,
            app_secret,
            postgres_secret,
            redis_secret,
            clickhouse_secret,
            s3_secret,
            database,
            clickhouse_database_job,
            s3_bucket_job,
        ],
    ),
)

ingress = k8s.networking.v1.Ingress(
    "langfuse-ingress",
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
                                    name=service_name,
                                    port=k8s.networking.v1.ServiceBackendPortArgs(
                                        number=service_port,
                                    ),
                                ),
                            ),
                        )
                    ],
                ),
            )
        ],
        tls=[
            k8s.networking.v1.IngressTLSArgs(
                hosts=[hostname],
            )
        ],
    ),
    opts=pulumi.ResourceOptions(depends_on=[release]),
)

pulumi.export("namespace", namespace.metadata.name)
pulumi.export("release", release.name)
pulumi.export("chart", CHART)
pulumi.export("chart_repository", CHART_REPOSITORY)
pulumi.export("chart_version", chart_version)
pulumi.export("service", service_name)
pulumi.export("service_port", service_port)
pulumi.export("ingress", ingress.metadata.name)
pulumi.export("hostname", hostname)
pulumi.export("url", public_url)
pulumi.export("internal_url", internal_url)
pulumi.export("sign_up_disabled", sign_up_disabled)
pulumi.export("telemetry_enabled", telemetry_enabled)
pulumi.export("app_secret", app_secret.metadata.name)
pulumi.export("postgres_secret", postgres_secret.metadata.name)
pulumi.export("redis_secret", redis_secret.metadata.name)
pulumi.export("clickhouse_secret", clickhouse_secret.metadata.name)
pulumi.export("s3_secret", s3_secret.metadata.name)
pulumi.export("database", database.name)
pulumi.export("postgres_stack", postgres_stack_ref)
pulumi.export("clickhouse_stack", clickhouse_stack_ref)
pulumi.export("clickhouse_database", clickhouse_database)
pulumi.export("rustfs_stack", rustfs_stack_ref)
pulumi.export("s3_bucket", s3_bucket)
pulumi.export("s3_endpoint", rustfs_s3_endpoint_url)
