import pulumi_kubernetes as k8s
import pulumi_postgresql as pg
import pulumi_random as random

import pulumi

config = pulumi.Config()

namespace_name = config.get("namespace", "airflow")
chart_version = config.get("chartVersion", "1.21.0")
hostname = config.get("hostname", "airflow")
executor = config.get("executor", "LocalExecutor")
postgres_stack_ref = config.get("postgresStack", "kzh/postgresql/mx")
database_name = config.get("databaseName", "airflow")
database_user = config.get("databaseUser", "airflow")
storage_class_name = config.get("storageClassName", "local-path")
triggerer_storage_size = config.get("triggererStorageSize", "5Gi")

labels = {
    "app.kubernetes.io/name": "airflow",
    "app.kubernetes.io/part-of": "airflow",
}

postgres_stack = pulumi.StackReference(postgres_stack_ref)
postgres_service_host = postgres_stack.require_output("rw_service_fqdn")

database_password = random.RandomPassword(
    "airflow-database-password",
    length=32,
    special=False,
)

admin_password = random.RandomPassword(
    "airflow-admin-password",
    length=24,
    special=False,
)

fernet_key_bytes = random.RandomBytes(
    "airflow-fernet-key-bytes",
    length=32,
)
fernet_key = pulumi.Output.secret(
    fernet_key_bytes.base64.apply(
        lambda value: value.replace("+", "-").replace("/", "_")
    )
)

api_secret_key = random.RandomPassword(
    "airflow-api-secret-key",
    length=64,
    special=False,
)

jwt_secret = random.RandomPassword(
    "airflow-jwt-secret",
    length=64,
    special=False,
)

namespace = k8s.core.v1.Namespace(
    "airflow-namespace",
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

airflow_role = pg.Role(
    "airflow-role",
    name=database_user,
    login=True,
    password=database_password.result,
    opts=pulumi.ResourceOptions(provider=admin_provider),
)

airflow_database = pg.Database(
    "airflow-database",
    name=database_name,
    owner=airflow_role.name,
    opts=pulumi.ResourceOptions(
        provider=admin_provider,
        depends_on=[airflow_role],
    ),
)

smoke_dag = k8s.core.v1.ConfigMap(
    "airflow-smoke-dag",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="airflow-smoke-dag",
        namespace=namespace.metadata.name,
        labels=labels,
    ),
    data={
        "homelab_smoke.py": """
from __future__ import annotations

from datetime import datetime

from airflow.decorators import dag, task


@dag(
    dag_id="homelab_smoke",
    schedule=None,
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["homelab"],
)
def homelab_smoke():
    @task
    def hello():
        print("hello from Airflow on mx0")

    hello()


homelab_smoke()
""".lstrip(),
    },
    opts=pulumi.ResourceOptions(depends_on=[namespace]),
)

airflow_chart = k8s.helm.v3.Release(
    "airflow",
    chart="airflow",
    name="airflow",
    namespace=namespace.metadata.name,
    repository_opts=k8s.helm.v3.RepositoryOptsArgs(
        repo="https://airflow.apache.org",
    ),
    version=chart_version,
    timeout=900,
    wait_for_jobs=True,
    values={
        "fullnameOverride": "airflow",
        "executor": executor,
        "allowPodLaunching": False,
        "allowJobLaunching": False,
        "webserver": {
            "enabled": False,
        },
        "apiServer": {
            "enabled": True,
            "replicas": 1,
            "service": {
                "type": "ClusterIP",
            },
        },
        "scheduler": {
            "replicas": 1,
        },
        "dagProcessor": {
            "replicas": 1,
        },
        "triggerer": {
            "enabled": True,
            "replicas": 1,
            "persistence": {
                "enabled": True,
                "size": triggerer_storage_size,
                "storageClassName": storage_class_name,
            },
        },
        "statsd": {
            "enabled": False,
        },
        "redis": {
            "enabled": False,
        },
        "postgresql": {
            "enabled": False,
        },
        "data": {
            "metadataConnection": {
                "user": airflow_role.name,
                "pass": database_password.result,
                "protocol": "postgresql",
                "host": postgres_service_host,
                "port": 5432,
                "db": airflow_database.name,
                "sslmode": "disable",
            },
        },
        "fernetKey": fernet_key,
        "apiSecretKey": api_secret_key.result,
        "jwtSecret": jwt_secret.result,
        "createUserJob": {
            "useHelmHooks": False,
            "defaultUser": {
                "role": "Admin",
                "username": "admin",
                "email": "admin@example.com",
                "firstName": "admin",
                "lastName": "user",
                "password": admin_password.result,
            },
        },
        "migrateDatabaseJob": {
            "useHelmHooks": False,
        },
        "ingress": {
            "apiServer": {
                "enabled": True,
                "ingressClassName": "tailscale",
                "path": "/",
                "pathType": "Prefix",
                "hosts": [
                    {
                        "name": hostname,
                        "tls": {
                            "enabled": True,
                        },
                    }
                ],
            },
        },
        "logs": {
            "persistence": {
                "enabled": False,
            },
        },
        "volumes": [
            {
                "name": "airflow-smoke-dag",
                "configMap": {
                    "name": smoke_dag.metadata.name,
                },
            }
        ],
        "volumeMounts": [
            {
                "name": "airflow-smoke-dag",
                "mountPath": "/opt/airflow/dags/homelab_smoke.py",
                "subPath": "homelab_smoke.py",
                "readOnly": True,
            }
        ],
    },
    opts=pulumi.ResourceOptions(
        depends_on=[namespace, airflow_database, smoke_dag],
        delete_before_replace=True,
    ),
)

pulumi.export("namespace", namespace.metadata.name)
pulumi.export("chartVersion", chart_version)
pulumi.export("airflowVersion", "3.2.0")
pulumi.export("hostname", hostname)
pulumi.export("executor", executor)
pulumi.export("database", airflow_database.name)
pulumi.export("adminUsername", "admin")
pulumi.export("adminPassword", admin_password.result)
pulumi.export("smokeDagId", "homelab_smoke")
pulumi.export("releaseName", airflow_chart.name)
