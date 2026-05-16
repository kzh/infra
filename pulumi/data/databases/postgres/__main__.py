import base64
from typing import Any

import pulumi_kubernetes as k8s
import pulumi_postgresql as pg
from pulumi_monitoring_crds.monitoring.v1 import PodMonitor

import pulumi

config = pulumi.Config()

ns_value = config.get(
    "namespace", "postgresql"
)  # plain string to avoid Output warnings
monitoring_release_label = config.get("monitoringReleaseLabel", "kube-prometheus-stack")
cnpg_cluster_name = config.get("clusterName", "postgresql-cluster")

# Manage the Namespace with a stable Pulumi name to avoid replacement
postgres_namespace = k8s.core.v1.Namespace(
    "postgresql",
    metadata=k8s.meta.v1.ObjectMetaArgs(name=ns_value),
)
pulumi.export("k8s_namespace", ns_value)


ts_hostname = config.get("ts_hostname", "postgresql")
pulumi.export("ts_hostname", ts_hostname)
pulumi.export("cnpg_cluster_name", cnpg_cluster_name)
pulumi.export("monitoring_release_label", monitoring_release_label)
rw_service_name = f"{cnpg_cluster_name}-rw"
rw_service_fqdn = f"{rw_service_name}.{ns_value}.svc.cluster.local"
ca_secret_name = f"{cnpg_cluster_name}-ca"

pulumi.export("rw_service_name", rw_service_name)
pulumi.export("rw_service_fqdn", rw_service_fqdn)
pulumi.export("ca_secret_name", ca_secret_name)


def add_wait_annotation(
    args: pulumi.ResourceTransformationArgs,
) -> pulumi.ResourceTransformationResult | None:
    # CNPG Cluster readiness is not captured by generic await logic.
    if (
        isinstance(args.props, dict)
        and args.props.get("apiVersion") == "postgresql.cnpg.io/v1"
        and args.props.get("kind") == "Cluster"
    ):
        props: dict[str, Any] = dict(args.props)
        metadata = dict(props.get("metadata") or {})
        annotations = dict(metadata.get("annotations") or {})
        annotations["pulumi.com/waitFor"] = (
            "jsonpath={.status.phase}=Cluster in healthy state"
        )
        metadata["annotations"] = annotations
        props["metadata"] = metadata
        return pulumi.ResourceTransformationResult(props=props, opts=args.opts)
    return None


pg_chart = k8s.helm.v4.Chart(
    "postgresql",
    chart="cluster",
    namespace=ns_value,
    repository_opts=k8s.helm.v4.RepositoryOptsArgs(
        repo="https://cloudnative-pg.github.io/charts",
    ),
    version="0.6.1",
    values={
        "version": {
            "postgresql": "18",
        },
        "cluster": {
            "instances": 1,
            "imageName": "tensorchord/cloudnative-vectorchord:18.3-1.1.1",
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
            "additionalLabels": {
                "release": monitoring_release_label,
            },
            "monitoring": {
                "enabled": True,
                "podMonitor": {
                    "enabled": False,
                },
                "prometheusRule": {
                    "enabled": True,
                    "excludeRules": [
                        "CNPGClusterLogicalReplicationErrorsCritical",
                        "CNPGClusterLogicalReplicationErrors",
                        "CNPGClusterLogicalReplicationLaggingCritical",
                        "CNPGClusterLogicalReplicationLagging",
                        "CNPGClusterLogicalReplicationStoppedCritical",
                        "CNPGClusterLogicalReplicationStopped",
                    ],
                },
                "instrumentation": {
                    "logicalReplication": False,
                },
            },
        },
    },
    opts=pulumi.ResourceOptions(
        depends_on=[postgres_namespace],
        transformations=[add_wait_annotation],
    ),
)

cluster_podmonitor = PodMonitor(
    "postgresql-cluster-podmonitor",
    metadata={
        "name": "postgresql-cluster",
        "namespace": ns_value,
        "labels": {
            "release": monitoring_release_label,
        },
    },
    spec={
        "namespaceSelector": {
            "matchNames": [ns_value],
        },
        "selector": {
            "matchLabels": {
                "cnpg.io/cluster": cnpg_cluster_name,
            },
        },
        "podMetricsEndpoints": [
            {
                "targetPort": 9187,
                "path": "/metrics",
                "interval": "30s",
            }
        ],
    },
    opts=pulumi.ResourceOptions(depends_on=[pg_chart]),
)

secret_id = f"{ns_value}/postgresql-cluster-superuser"

pg_secret = k8s.core.v1.Secret.get(
    "superuser-secret",
    secret_id,
    opts=pulumi.ResourceOptions(depends_on=[pg_chart]),
)

ca_secret = k8s.core.v1.Secret.get(
    "ca-secret",
    f"{ns_value}/{ca_secret_name}",
    opts=pulumi.ResourceOptions(depends_on=[pg_chart]),
)


def decoded_secret_field(secret: k8s.core.v1.Secret, field: str):
    return secret.data.apply(
        lambda data, field=field: base64.b64decode(data.get(field, "")).decode()
    )


def secret_decoded_secret_field(secret: k8s.core.v1.Secret, field: str):
    return pulumi.Output.secret(decoded_secret_field(secret, field))


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
    pulumi.export(key, secret_decoded_secret_field(pg_secret, field))

ca_cert_pem = secret_decoded_secret_field(ca_secret, "ca.crt")
pulumi.export("ca_cert_pem", ca_cert_pem)

# Optional: create application databases and ensure extensions
app_dbs = config.get_object("app_databases") or []  # e.g., ["immich", "stitch"]
extensions = config.get_object("extensions") or [
    "vector",
    "cube",
    "earthdistance",
    "vchord",
]
ordered_extensions = list(dict.fromkeys(extensions))
if "vchord" in ordered_extensions and "vector" in ordered_extensions:
    ordered_extensions.remove("vchord")
    ordered_extensions.insert(ordered_extensions.index("vector") + 1, "vchord")

pg_username = secret_decoded_secret_field(pg_secret, "username")
pg_password = secret_decoded_secret_field(pg_secret, "password")

# Admin provider (connects to maintenance DB 'postgres') using tailscale hostname
admin_provider = pg.Provider(
    "pg-admin",
    host=ts_hostname,
    port=5432,
    username=pg_username,
    password=pg_password,
    database="postgres",
    sslmode="disable",
)

for db_name in app_dbs:
    app_db = pg.Database(
        f"db-{db_name}",
        name=db_name,
        opts=pulumi.ResourceOptions(provider=admin_provider, depends_on=[pg_chart]),
    )

    # Provider scoped to the application database.
    app_provider = pg.Provider(
        f"pg-{db_name}",
        host=ts_hostname,
        port=5432,
        username=pg_username,
        password=pg_password,
        database=db_name,
        sslmode="disable",
    )

    prev_extension: pulumi.Resource = app_db
    for ext in ordered_extensions:
        ext_resource = pg.Extension(
            f"ext-{db_name}-{ext}",
            name=ext,
            schema="public",
            opts=pulumi.ResourceOptions(
                provider=app_provider, depends_on=[prev_extension]
            ),
        )
        prev_extension = ext_resource
