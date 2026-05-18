from pathlib import Path

import pulumi_kubernetes as k8s
import pulumi_random as random
from infra_helpers.grafana import dashboard_config_maps
from infra_helpers.postgres import PostgresStack, create_database_owner
from pulumi_monitoring_crds.monitoring.v1 import ServiceMonitor

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
monitoring_release_label = config.get("monitoringReleaseLabel", "kube-prometheus-stack")
dashboards_dir = Path(__file__).resolve().parent / "dashboards"
dashboard_files = [
    "airflow-overview.json",
]

labels = {
    "app.kubernetes.io/name": "airflow",
    "app.kubernetes.io/part-of": "airflow",
}

postgres = PostgresStack(postgres_stack_ref)
postgres_service_host = postgres.rw_service_fqdn

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

airflow_database_owner = create_database_owner(
    role_resource_name="airflow-role",
    database_resource_name="airflow-database",
    provider=postgres.admin_provider("pg-admin"),
    role_name=database_user,
    database_name=database_name,
    password=database_password.result,
)
airflow_role = airflow_database_owner.role
airflow_database = airflow_database_owner.database

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
            "enabled": True,
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

airflow_statsd_servicemonitor = ServiceMonitor(
    "airflow-statsd-servicemonitor",
    metadata={
        "name": "airflow-statsd",
        "namespace": namespace_name,
        "labels": {
            "release": monitoring_release_label,
        },
    },
    spec={
        "selector": {
            "matchLabels": {
                "tier": "airflow",
                "component": "statsd",
                "release": "airflow",
            },
        },
        "namespaceSelector": {
            "matchNames": [namespace_name],
        },
        "endpoints": [
            {
                "port": "statsd-scrape",
                "path": "/metrics",
                "interval": "30s",
                "scheme": "http",
            },
        ],
    },
    opts=pulumi.ResourceOptions(depends_on=[airflow_chart]),
)

dashboard_config_maps(
    name_prefix="airflow-dashboard",
    namespace=namespace.metadata.name,
    dashboards_dir=dashboards_dir,
    dashboard_files=dashboard_files,
    labels={"app": "airflow"},
    opts=pulumi.ResourceOptions(depends_on=[airflow_statsd_servicemonitor]),
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
