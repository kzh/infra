from pathlib import Path

import pulumi_kubernetes as k8s
import pulumi_random as random
from infra_helpers.grafana import dashboard_config_maps
from infra_helpers.postgres import PostgresStack, create_database_owner

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
dashboards_dir = Path(__file__).resolve().parent / "dashboards"
dashboard_files = [
    "dagster-overview.json",
]

labels = {
    "app.kubernetes.io/name": "dagster",
    "app.kubernetes.io/part-of": "dagster",
}

postgres = PostgresStack(postgres_stack_ref)
postgres_service_host = postgres.rw_service_fqdn

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

dagster_database_owner = create_database_owner(
    role_resource_name="dagster-role",
    database_resource_name="dagster-database",
    provider=postgres.admin_provider("pg-admin"),
    role_name=database_user,
    database_name=database_name,
    password=database_password.result,
)
dagster_role = dagster_database_owner.role
dagster_database = dagster_database_owner.database

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

dashboard_config_maps(
    name_prefix="dagster-dashboard",
    namespace=namespace.metadata.name,
    dashboards_dir=dashboards_dir,
    dashboard_files=dashboard_files,
    labels=labels,
    opts=pulumi.ResourceOptions(depends_on=[dagster_chart]),
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
