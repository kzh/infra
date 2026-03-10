import pulumi
import pulumi_kubernetes as k8s
import pulumi_postgresql as pg
import pulumi_random as random

CHART_VERSION = "1.8.1"
MLFLOW_IMAGE_VERSION = "3.7.0"
AWS_DEFAULT_REGION = "us-east-1"

config = pulumi.Config()
namespace_name = config.get("namespace") or "mlflow"
ingress_host = config.get("ingress_host") or "mlflow"
public_host = config.get("public_host") or ingress_host
db_name = config.get("db_name") or "mlflow"
db_user = config.get("db_user") or "mlflow"
bucket_name = config.get("bucket") or "mlflow"
postgres_stack_ref = config.get("postgres_stack_ref") or "kzh/postgresql/mx"
rustfs_stack_ref = config.get("rustfs_stack_ref") or "kzh/rustfs/mx"

labels = {
    "app": "mlflow",
}

postgres_stack = pulumi.StackReference(postgres_stack_ref)
rustfs_stack = pulumi.StackReference(rustfs_stack_ref)

postgres_service_host = pulumi.Output.format(
    "{0}-rw.{1}.svc.cluster.local",
    postgres_stack.require_output("cnpg_cluster_name"),
    postgres_stack.require_output("k8s_namespace"),
)
rustfs_s3_endpoint_url = pulumi.Output.format(
    "http://{0}.{1}.svc.cluster.local:9000",
    rustfs_stack.require_output("s3_hostname"),
    rustfs_stack.require_output("namespace"),
)

db_password = random.RandomPassword(
    "mlflow-db-password",
    length=32,
    special=False,
)

flask_secret_key = random.RandomPassword(
    "mlflow-flask-secret-key",
    length=64,
    special=False,
)

namespace = k8s.core.v1.Namespace(
    "mlflow-namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=namespace_name,
        labels=labels,
    ),
)


def force_mlflow_deployment_apply(
    args: pulumi.ResourceTransformationArgs,
) -> pulumi.ResourceTransformationResult | None:
    if args.type_ != "kubernetes:apps/v1:Deployment" or not isinstance(args.props, dict):
        return None

    props = dict(args.props)
    metadata = dict(props.get("metadata") or {})
    annotations = dict(metadata.get("annotations") or {})
    annotations["pulumi.com/patchForce"] = "true"
    metadata["annotations"] = annotations
    props["metadata"] = metadata
    return pulumi.ResourceTransformationResult(props=props, opts=args.opts)

admin_provider = pg.Provider(
    "pg-admin",
    host=postgres_stack.require_output("ts_hostname"),
    port=5432,
    username=postgres_stack.require_output("username"),
    password=postgres_stack.require_output("password"),
    database="postgres",
    sslmode="disable",
)

mlflow_role = pg.Role(
    "mlflow-role",
    name=db_user,
    login=True,
    password=db_password.result,
    opts=pulumi.ResourceOptions(provider=admin_provider),
)

mlflow_database = pg.Database(
    "mlflow-database",
    name=db_name,
    owner=mlflow_role.name,
    opts=pulumi.ResourceOptions(provider=admin_provider, depends_on=[mlflow_role]),
)

database_secret = k8s.core.v1.Secret(
    "mlflow-database-secret",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="mlflow-database",
        namespace=namespace.metadata.name,
        labels=labels,
    ),
    string_data={
        "username": mlflow_role.name,
        "password": db_password.result,
    },
    type="Opaque",
    opts=pulumi.ResourceOptions(depends_on=[namespace, mlflow_database]),
)

s3_secret = k8s.core.v1.Secret(
    "mlflow-s3-secret",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="mlflow-artifacts-s3",
        namespace=namespace.metadata.name,
        labels=labels,
    ),
    string_data={
        "AWS_ACCESS_KEY_ID": rustfs_stack.require_output("access_key"),
        "AWS_SECRET_ACCESS_KEY": rustfs_stack.require_output("secret_key"),
    },
    type="Opaque",
    opts=pulumi.ResourceOptions(depends_on=[namespace]),
)

flask_secret = k8s.core.v1.Secret(
    "mlflow-flask-server-secret-key",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="mlflow-flask-server-secret-key",
        namespace=namespace.metadata.name,
        labels=labels,
    ),
    string_data={
        "MLFLOW_FLASK_SERVER_SECRET_KEY": flask_secret_key.result,
    },
    type="Opaque",
    opts=pulumi.ResourceOptions(depends_on=[namespace]),
)

bucket_job = k8s.batch.v1.Job(
    "mlflow-artifact-bucket",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="mlflow-create-bucket",
        namespace=namespace.metadata.name,
        labels=labels,
        annotations={
            "pulumi.com/waitFor": "jsonpath={.status.succeeded}=1",
        },
    ),
    spec=k8s.batch.v1.JobSpecArgs(
        backoff_limit=4,
        template=k8s.core.v1.PodTemplateSpecArgs(
            metadata=k8s.meta.v1.ObjectMetaArgs(
                labels=labels,
            ),
            spec=k8s.core.v1.PodSpecArgs(
                restart_policy="OnFailure",
                containers=[
                    k8s.core.v1.ContainerArgs(
                        name="create-bucket",
                        image=f"burakince/mlflow:{MLFLOW_IMAGE_VERSION}",
                        command=["python", "-c"],
                        args=[
                            """
import os
import boto3
from botocore.exceptions import ClientError

s3 = boto3.client(
    "s3",
    endpoint_url=os.environ["S3_ENDPOINT_URL"],
    aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
    aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
    region_name=os.environ["AWS_DEFAULT_REGION"],
)

bucket = os.environ["BUCKET_NAME"]
try:
    s3.head_bucket(Bucket=bucket)
except ClientError as exc:
    error_code = exc.response.get("Error", {}).get("Code", "")
    status_code = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)
    if status_code == 404 or error_code in {"404", "NoSuchBucket", "NotFound"}:
        s3.create_bucket(Bucket=bucket)
    else:
        raise
""".strip(),
                        ],
                        env=[
                            k8s.core.v1.EnvVarArgs(
                                name="S3_ENDPOINT_URL",
                                value=rustfs_s3_endpoint_url,
                            ),
                            k8s.core.v1.EnvVarArgs(
                                name="AWS_DEFAULT_REGION",
                                value=AWS_DEFAULT_REGION,
                            ),
                            k8s.core.v1.EnvVarArgs(
                                name="BUCKET_NAME",
                                value=bucket_name,
                            ),
                            k8s.core.v1.EnvVarArgs(
                                name="AWS_ACCESS_KEY_ID",
                                value_from=k8s.core.v1.EnvVarSourceArgs(
                                    secret_key_ref=k8s.core.v1.SecretKeySelectorArgs(
                                        name=s3_secret.metadata.name,
                                        key="AWS_ACCESS_KEY_ID",
                                    ),
                                ),
                            ),
                            k8s.core.v1.EnvVarArgs(
                                name="AWS_SECRET_ACCESS_KEY",
                                value_from=k8s.core.v1.EnvVarSourceArgs(
                                    secret_key_ref=k8s.core.v1.SecretKeySelectorArgs(
                                        name=s3_secret.metadata.name,
                                        key="AWS_SECRET_ACCESS_KEY",
                                    ),
                                ),
                            ),
                        ],
                    ),
                ],
            ),
        ),
    ),
    opts=pulumi.ResourceOptions(depends_on=[namespace, s3_secret]),
)

mlflow_chart = k8s.helm.v4.Chart(
    "mlflow",
    chart="mlflow",
    namespace=namespace.metadata.name,
    version=CHART_VERSION,
    repository_opts=k8s.helm.v4.RepositoryOptsArgs(
        repo="https://community-charts.github.io/helm-charts",
    ),
    values={
        "fullnameOverride": "mlflow",
        "postgresql": {
            "enabled": False,
        },
        "mysql": {
            "enabled": False,
        },
        "backendStore": {
            "databaseMigration": True,
            "databaseConnectionCheck": True,
            "postgres": {
                "enabled": True,
                "host": postgres_service_host,
                "port": 5432,
                "database": db_name,
            },
            "existingDatabaseSecret": {
                "name": database_secret.metadata.name,
                "usernameKey": "username",
                "passwordKey": "password",
            },
        },
        "artifactRoot": {
            "proxiedArtifactStorage": True,
            "s3": {
                "enabled": True,
                "bucket": bucket_name,
                "existingSecret": {
                    "name": s3_secret.metadata.name,
                    "keyOfAccessKeyId": "AWS_ACCESS_KEY_ID",
                    "keyOfSecretAccessKey": "AWS_SECRET_ACCESS_KEY",
                },
            },
        },
        "extraEnvVars": {
            "MLFLOW_S3_ENDPOINT_URL": rustfs_s3_endpoint_url,
            "AWS_DEFAULT_REGION": AWS_DEFAULT_REGION,
        },
        "extraArgs": {
            "allowedHosts": f"{public_host},localhost:*",
            "corsAllowedOrigins": f"https://{public_host}",
        },
        "log": {
            "enabled": False,
        },
        "ingress": {
            "enabled": True,
            "className": "tailscale",
            "hosts": [
                {
                    "host": ingress_host,
                    "paths": [
                        {
                            "path": "/",
                            "pathType": "Prefix",
                        }
                    ],
                }
            ],
            "tls": [
                {
                    "hosts": [ingress_host],
                }
            ],
        },
        "resources": {},
    },
    opts=pulumi.ResourceOptions(
        depends_on=[
            namespace,
            mlflow_database,
            database_secret,
            s3_secret,
            flask_secret,
            bucket_job,
        ],
        transformations=[force_mlflow_deployment_apply],
    ),
)

pulumi.export("namespace", namespace.metadata.name)
pulumi.export("chart_version", CHART_VERSION)
pulumi.export("ingress_host", ingress_host)
pulumi.export("bucket", bucket_name)
pulumi.export("database", mlflow_database.name)
pulumi.export("database_secret_name", database_secret.metadata.name)
