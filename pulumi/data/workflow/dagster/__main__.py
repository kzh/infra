import pulumi_kubernetes as k8s
import pulumi_postgresql as pg
import pulumi_random as random

import pulumi

CHART_VERSION = "1.13.5"
DAGSTER_VERSION = "1.13.5"
POSTGRES_PASSWORD_SECRET_NAME = "dagster-postgresql-secret"
SMOKE_DEFINITIONS_CONFIG_MAP_NAME = "dagster-smoke-definitions"
SMOKE_DEFINITIONS_MOUNT_PATH = "/opt/dagster/smoke/definitions.py"

config = pulumi.Config()

namespace_name = config.get("namespace", "dagster")
chart_version = config.get("chartVersion", CHART_VERSION)
hostname = config.get("hostname", "dagster")
postgres_stack_ref = config.get("postgresStack", "kzh/postgresql/mx")
database_name = config.get("databaseName", "dagster")
database_user = config.get("databaseUser", "dagster")

labels = {
    "app.kubernetes.io/name": "dagster",
    "app.kubernetes.io/part-of": "dagster",
}

postgres_stack = pulumi.StackReference(postgres_stack_ref)
postgres_service_host = postgres_stack.require_output("rw_service_fqdn")

database_password = random.RandomPassword(
    "dagster-database-password",
    length=32,
    special=False,
)

namespace = k8s.core.v1.Namespace(
    "dagster-namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=namespace_name,
        labels=labels,
    ),
)

admin_provider = pg.Provider(
    "pg-admin",
    host=postgres_stack.require_output("ts_hostname"),
    port=5432,
    username=postgres_stack.require_output("username"),
    password=postgres_stack.require_output("password"),
    database="postgres",
    sslmode="disable",
)

dagster_role = pg.Role(
    "dagster-role",
    name=database_user,
    login=True,
    password=database_password.result,
    opts=pulumi.ResourceOptions(provider=admin_provider),
)

dagster_database = pg.Database(
    "dagster-database",
    name=database_name,
    owner=dagster_role.name,
    opts=pulumi.ResourceOptions(
        provider=admin_provider,
        depends_on=[dagster_role],
    ),
)

postgres_secret = k8s.core.v1.Secret(
    "dagster-postgres-secret",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=POSTGRES_PASSWORD_SECRET_NAME,
        namespace=namespace_name,
        labels=labels,
    ),
    string_data={
        "postgresql-password": database_password.result,
    },
    type="Opaque",
    opts=pulumi.ResourceOptions(depends_on=[namespace]),
)

smoke_definitions = k8s.core.v1.ConfigMap(
    "dagster-smoke-definitions",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=SMOKE_DEFINITIONS_CONFIG_MAP_NAME,
        namespace=namespace_name,
        labels=labels,
    ),
    data={
        "definitions.py": """
from dagster import Definitions, asset


@asset
def homelab_smoke() -> str:
    return "hello from Dagster on mx0"


defs = Definitions(assets=[homelab_smoke])
""".lstrip(),
    },
    opts=pulumi.ResourceOptions(depends_on=[namespace]),
)

dagster_chart = k8s.helm.v4.Chart(
    "dagster",
    chart="dagster",
    namespace=namespace_name,
    repository_opts=k8s.helm.v4.RepositoryOptsArgs(
        repo="https://dagster-io.github.io/helm",
    ),
    version=chart_version,
    values={
        "fullnameOverride": "dagster",
        "global": {
            "postgresqlSecretName": POSTGRES_PASSWORD_SECRET_NAME,
        },
        "postgresql": {
            "enabled": False,
            "postgresqlHost": postgres_service_host,
            "postgresqlUsername": database_user,
            "postgresqlDatabase": database_name,
            "service": {
                "port": 5432,
            },
        },
        "generatePostgresqlPasswordSecret": False,
        "dagsterWebserver": {
            "replicaCount": 1,
            "service": {
                "type": "ClusterIP",
                "port": 80,
            },
        },
        "dagsterDaemon": {
            "enabled": True,
            "runCoordinator": {
                "enabled": True,
            },
        },
        "runLauncher": {
            "type": "K8sRunLauncher",
            "config": {
                "k8sRunLauncher": {
                    "jobNamespace": namespace_name,
                    "loadInclusterConfig": True,
                },
            },
        },
        "dagster-user-deployments": {
            "enabled": True,
            "enableSubchart": True,
            "deployments": [
                {
                    "name": "homelab-smoke",
                    "image": {
                        "repository": "docker.io/dagster/dagster-celery-k8s",
                        "tag": chart_version,
                        "pullPolicy": "Always",
                    },
                    "dagsterApiGrpcArgs": [
                        "--python-file",
                        SMOKE_DEFINITIONS_MOUNT_PATH,
                    ],
                    "port": 3030,
                    "includeConfigInLaunchedRuns": {
                        "enabled": True,
                    },
                    "volumes": [
                        {
                            "name": "dagster-smoke-definitions",
                            "configMap": {
                                "name": SMOKE_DEFINITIONS_CONFIG_MAP_NAME,
                            },
                        }
                    ],
                    "volumeMounts": [
                        {
                            "name": "dagster-smoke-definitions",
                            "mountPath": SMOKE_DEFINITIONS_MOUNT_PATH,
                            "subPath": "definitions.py",
                            "readOnly": True,
                        }
                    ],
                },
            ],
        },
        "ingress": {
            "enabled": True,
            "ingressClassName": "tailscale",
            "dagsterWebserver": {
                "host": hostname,
                "path": "/",
                "pathType": "Prefix",
                "tls": {
                    "enabled": True,
                    "secretName": "",
                },
            },
        },
        "telemetry": {
            "enabled": False,
        },
    },
    opts=pulumi.ResourceOptions(
        depends_on=[namespace, dagster_database, postgres_secret, smoke_definitions],
    ),
)

pulumi.export("namespace", namespace.metadata.name)
pulumi.export("chartVersion", chart_version)
pulumi.export("dagsterVersion", DAGSTER_VERSION)
pulumi.export("hostname", hostname)
pulumi.export("url", pulumi.Output.format("https://{0}", hostname))
pulumi.export("database", dagster_database.name)
pulumi.export("databaseUser", dagster_role.name)
pulumi.export("databaseSecretName", POSTGRES_PASSWORD_SECRET_NAME)
pulumi.export("userCodeDeployment", "homelab-smoke")
pulumi.export("releaseName", "dagster")
