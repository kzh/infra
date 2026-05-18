import base64
from pathlib import Path

import pulumi_kubernetes as k8s
from infra_helpers.grafana import dashboard_config_maps
from pulumi_spark_operator_crds.sparkoperator.v1alpha1 import SparkConnect

import pulumi

CHART_VERSION = "2.5.0"
SPARK_VERSION = "4.1.1"
SPARK_JAVA_VERSION = "21"
ICEBERG_VERSION = "1.10.1"
ICEBERG_SPARK_RUNTIME = "4.0_2.13"
POSTGRESQL_JDBC_VERSION = "42.7.11"
AWS_DEFAULT_REGION = "us-east-1"
ICEBERG_PACKAGE = f"org.apache.iceberg:iceberg-spark-runtime-{ICEBERG_SPARK_RUNTIME}:{ICEBERG_VERSION}"
ICEBERG_AWS_BUNDLE_PACKAGE = f"org.apache.iceberg:iceberg-aws-bundle:{ICEBERG_VERSION}"
POSTGRESQL_JDBC_PACKAGE = f"org.postgresql:postgresql:{POSTGRESQL_JDBC_VERSION}"
ICEBERG_CATALOG_NAME = "trino_iceberg"
SPARK_ICEBERG_CREDENTIALS_SECRET_NAME = "spark-iceberg-credentials"
ICEBERG_WAREHOUSE_MOUNT_PATH = "/var/lib/spark/warehouse"
LEGACY_LOCAL_ICEBERG_WAREHOUSE_URI = f"file://{ICEBERG_WAREHOUSE_MOUNT_PATH}"
DEFAULT_SPARK_IMAGE = (
    "ghcr.io/kzh/spark:"
    f"{SPARK_VERSION}-iceberg{ICEBERG_VERSION}-lakehouse-java{SPARK_JAVA_VERSION}"
)

config = pulumi.Config()
namespace_name = config.require("namespace")
connect_name = config.get("connect_name") or "spark-connect"
connect_hostname = config.get("connect_hostname") or connect_name
ui_hostname = config.get("ui_hostname") or "spark"
spark_image = config.get("image") or DEFAULT_SPARK_IMAGE
warehouse_storage_size = config.get("warehouse_storage_size") or "20Gi"
warehouse_storage_class = config.get("warehouse_storage_class") or "local-path"
postgres_stack_ref = config.get("postgresStack") or "kzh/postgresql/mx"
rustfs_stack_ref = config.get("rustfsStack") or "kzh/rustfs/mx"
trino_stack_ref = config.get("trinoStack") or "kzh/trino/mx"
trino_namespace = config.get("trinoNamespace") or "trino"
trino_credentials_secret_name = (
    config.get("trinoCredentialsSecretName") or "trino-catalog-credentials"
)
iceberg_catalog_name = config.get("icebergCatalogName") or ICEBERG_CATALOG_NAME
monitoring_release_label = (
    config.get("monitoringReleaseLabel") or "kube-prometheus-stack"
)
dashboards_dir = Path(__file__).resolve().parent / "dashboards"
dashboard_files = [
    "spark-overview.json",
]

labels = {
    "app": "spark-operator",
}

connect_server_selector = {
    "spark-role": "connect-server",
    "spark-version": SPARK_VERSION,
    "sparkoperator.k8s.io/connect-name": connect_name,
    "sparkoperator.k8s.io/launched-by-spark-operator": "true",
}

postgres_stack = pulumi.StackReference(postgres_stack_ref)
rustfs_stack = pulumi.StackReference(rustfs_stack_ref)
trino_stack = pulumi.StackReference(trino_stack_ref)

postgres_service_host = postgres_stack.require_output("rw_service_fqdn")
rustfs_s3_endpoint_url = pulumi.Output.format(
    "http://{0}.{1}.svc.cluster.local:9000",
    rustfs_stack.require_output("s3_hostname"),
    rustfs_stack.require_output("namespace"),
)
iceberg_database = trino_stack.require_output("iceberg_database")
iceberg_warehouse = trino_stack.require_output("iceberg_warehouse")
iceberg_jdbc_catalog_name = trino_stack.require_output("iceberg_jdbc_catalog_name")

spark_namespace = k8s.core.v1.Namespace(
    "spark-namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=namespace_name,
        labels=labels,
    ),
)

spark_operator = k8s.helm.v4.Chart(
    "spark",
    namespace=spark_namespace.metadata.name,
    chart="spark-operator",
    version=CHART_VERSION,
    repository_opts=k8s.helm.v4.RepositoryOptsArgs(
        repo="https://kubeflow.github.io/spark-operator",
    ),
    values={
        "fullnameOverride": "spark-operator",
        "controller": {
            "replicas": 1,
        },
        "webhook": {
            "enable": True,
            "replicas": 1,
        },
        "spark": {
            "jobNamespaces": [namespace_name],
        },
        "prometheus": {
            "metrics": {
                "enable": True,
            },
            "podMonitor": {
                "create": True,
                "labels": {
                    "release": monitoring_release_label,
                },
            },
        },
    },
)

warehouse_pvc_spec_args = {
    "access_modes": ["ReadWriteOnce"],
    "resources": k8s.core.v1.ResourceRequirementsArgs(
        requests={"storage": warehouse_storage_size},
    ),
}
if warehouse_storage_class:
    warehouse_pvc_spec_args["storage_class_name"] = warehouse_storage_class

spark_warehouse = k8s.core.v1.PersistentVolumeClaim(
    "spark-warehouse",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="spark-warehouse",
        namespace=spark_namespace.metadata.name,
        labels={
            "app": connect_name,
        },
    ),
    spec=k8s.core.v1.PersistentVolumeClaimSpecArgs(**warehouse_pvc_spec_args),
)

trino_credentials = k8s.core.v1.Secret.get(
    "trino-catalog-credentials",
    f"{trino_namespace}/{trino_credentials_secret_name}",
)


def spark_iceberg_secret_data(args: list[object]) -> dict[str, str]:
    data = args[0]
    postgres_host = args[1]
    database_name = args[2]
    warehouse = args[3]
    s3_endpoint = args[4]
    assert isinstance(data, dict)
    jdbc_user = base64.b64decode(data["TRINO_ICEBERG_JDBC_USER"]).decode()
    jdbc_password = base64.b64decode(data["TRINO_ICEBERG_JDBC_PASSWORD"]).decode()
    spark_defaults = "\n".join(
        [
            "spark.sql.extensions org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions",
            f"spark.sql.catalog.{iceberg_catalog_name} org.apache.iceberg.spark.SparkCatalog",
            f"spark.sql.catalog.{iceberg_catalog_name}.type jdbc",
            (
                f"spark.sql.catalog.{iceberg_catalog_name}.uri "
                f"jdbc:postgresql://{postgres_host}:5432/{database_name}"
            ),
            f"spark.sql.catalog.{iceberg_catalog_name}.jdbc.user {jdbc_user}",
            f"spark.sql.catalog.{iceberg_catalog_name}.jdbc.password {jdbc_password}",
            f"spark.sql.catalog.{iceberg_catalog_name}.warehouse {warehouse}",
            (
                f"spark.sql.catalog.{iceberg_catalog_name}.io-impl "
                "org.apache.iceberg.aws.s3.S3FileIO"
            ),
            f"spark.sql.catalog.{iceberg_catalog_name}.s3.endpoint {s3_endpoint}",
            f"spark.sql.catalog.{iceberg_catalog_name}.s3.path-style-access true",
            f"spark.sql.catalog.{iceberg_catalog_name}.client.region {AWS_DEFAULT_REGION}",
            "",
        ]
    )

    return {
        "ICEBERG_JDBC_USER": data["TRINO_ICEBERG_JDBC_USER"],
        "ICEBERG_JDBC_PASSWORD": data["TRINO_ICEBERG_JDBC_PASSWORD"],
        "AWS_ACCESS_KEY_ID": data["TRINO_S3_ACCESS_KEY"],
        "AWS_SECRET_ACCESS_KEY": data["TRINO_S3_SECRET_KEY"],
        "spark-defaults.conf": base64.b64encode(spark_defaults.encode()).decode(),
    }


spark_iceberg_credentials = k8s.core.v1.Secret(
    "spark-iceberg-credentials",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=SPARK_ICEBERG_CREDENTIALS_SECRET_NAME,
        namespace=spark_namespace.metadata.name,
        labels={
            "app": connect_name,
        },
    ),
    type="Opaque",
    data=pulumi.Output.secret(
        pulumi.Output.all(
            trino_credentials.data,
            postgres_service_host,
            iceberg_database,
            iceberg_warehouse,
            rustfs_s3_endpoint_url,
        ).apply(spark_iceberg_secret_data)
    ),
    opts=pulumi.ResourceOptions(
        depends_on=[spark_namespace, trino_credentials],
        delete_before_replace=True,
    ),
)

warehouse_volume = {
    "name": "spark-warehouse",
    "persistentVolumeClaim": {
        "claimName": spark_warehouse.metadata.name,
    },
}
warehouse_volume_mount = {
    "name": "spark-warehouse",
    "mountPath": ICEBERG_WAREHOUSE_MOUNT_PATH,
}
spark_defaults_volume = {
    "name": "spark-defaults",
    "secret": {
        "secretName": spark_iceberg_credentials.metadata.name,
        "items": [
            {
                "key": "spark-defaults.conf",
                "path": "spark-defaults.conf",
            }
        ],
    },
}
spark_defaults_volume_mount = {
    "name": "spark-defaults",
    "mountPath": "/opt/spark/conf/spark-defaults.conf",
    "subPath": "spark-defaults.conf",
    "readOnly": True,
}

iceberg_secret_env = [
    {
        "name": "ICEBERG_JDBC_USER",
        "valueFrom": {
            "secretKeyRef": {
                "name": spark_iceberg_credentials.metadata.name,
                "key": "ICEBERG_JDBC_USER",
            },
        },
    },
    {
        "name": "ICEBERG_JDBC_PASSWORD",
        "valueFrom": {
            "secretKeyRef": {
                "name": spark_iceberg_credentials.metadata.name,
                "key": "ICEBERG_JDBC_PASSWORD",
            },
        },
    },
    {
        "name": "AWS_ACCESS_KEY_ID",
        "valueFrom": {
            "secretKeyRef": {
                "name": spark_iceberg_credentials.metadata.name,
                "key": "AWS_ACCESS_KEY_ID",
            },
        },
    },
    {
        "name": "AWS_SECRET_ACCESS_KEY",
        "valueFrom": {
            "secretKeyRef": {
                "name": spark_iceberg_credentials.metadata.name,
                "key": "AWS_SECRET_ACCESS_KEY",
            },
        },
    },
    {
        "name": "AWS_REGION",
        "value": AWS_DEFAULT_REGION,
    },
    {
        "name": "AWS_DEFAULT_REGION",
        "value": AWS_DEFAULT_REGION,
    },
]

spark_connect = SparkConnect(
    "spark-connect",
    metadata={
        "name": connect_name,
        "namespace": namespace_name,
        "labels": {
            "app": connect_name,
        },
    },
    spec={
        "sparkVersion": SPARK_VERSION,
        "image": spark_image,
        "sparkConf": {
            "spark.kubernetes.authenticate.driver.serviceAccountName": "spark-operator-spark",
        },
        "server": {
            "cores": 1,
            "memory": "1g",
            "service": {
                "metadata": {
                    "name": connect_name,
                },
                "spec": {
                    "type": "ClusterIP",
                    "ports": [
                        {
                            "name": "connect",
                            "port": 15002,
                            "targetPort": 15002,
                            "protocol": "TCP",
                        }
                    ],
                },
            },
            "template": {
                "metadata": {
                    "labels": {
                        "app": connect_name,
                        "version": SPARK_VERSION,
                    },
                },
                "spec": {
                    "serviceAccount": "spark-operator-spark",
                    "volumes": [warehouse_volume, spark_defaults_volume],
                    "containers": [
                        {
                            "name": "spark-kubernetes-driver",
                            "image": spark_image,
                            "imagePullPolicy": "IfNotPresent",
                            "env": iceberg_secret_env,
                            "volumeMounts": [
                                warehouse_volume_mount,
                                spark_defaults_volume_mount,
                            ],
                        }
                    ],
                    "securityContext": {
                        "allowPrivilegeEscalation": False,
                        "capabilities": {
                            "drop": ["ALL"],
                        },
                        "runAsGroup": 185,
                        "runAsNonRoot": True,
                        "runAsUser": 185,
                        "seccompProfile": {
                            "type": "RuntimeDefault",
                        },
                    },
                },
            },
        },
        "executor": {
            "instances": 1,
            "cores": 1,
            "memory": "1g",
            "template": {
                "metadata": {
                    "labels": {
                        "app": connect_name,
                        "version": SPARK_VERSION,
                    },
                },
                "spec": {
                    "containers": [
                        {
                            "name": "spark-kubernetes-executor",
                            "image": spark_image,
                            "imagePullPolicy": "IfNotPresent",
                            "env": iceberg_secret_env,
                            "volumeMounts": [warehouse_volume_mount],
                        }
                    ],
                    "volumes": [warehouse_volume],
                    "securityContext": {
                        "allowPrivilegeEscalation": False,
                        "capabilities": {
                            "drop": ["ALL"],
                        },
                        "runAsGroup": 185,
                        "runAsNonRoot": True,
                        "runAsUser": 185,
                        "seccompProfile": {
                            "type": "RuntimeDefault",
                        },
                    },
                },
            },
        },
    },
    opts=pulumi.ResourceOptions(
        depends_on=[spark_operator, spark_warehouse, spark_iceberg_credentials],
        delete_before_replace=True,
        replace_on_changes=["spec"],
    ),
)

spark_connect_endpoint_service = k8s.core.v1.Service(
    "spark-connect-endpoint-service",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=f"{connect_name}-endpoint",
        namespace=namespace_name,
        labels={
            "app": connect_name,
        },
        annotations={
            "tailscale.com/expose": "true",
            "tailscale.com/hostname": connect_hostname,
        },
    ),
    spec=k8s.core.v1.ServiceSpecArgs(
        type="ClusterIP",
        selector=connect_server_selector,
        ports=[
            k8s.core.v1.ServicePortArgs(
                name="connect",
                port=15002,
                target_port=15002,
                protocol="TCP",
            )
        ],
    ),
    opts=pulumi.ResourceOptions(depends_on=[spark_connect]),
)

spark_connect_ui_service = k8s.core.v1.Service(
    "spark-connect-ui-service",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=f"{connect_name}-ui",
        namespace=namespace_name,
        labels={
            "app": connect_name,
        },
    ),
    spec=k8s.core.v1.ServiceSpecArgs(
        type="ClusterIP",
        selector=connect_server_selector,
        ports=[
            k8s.core.v1.ServicePortArgs(
                name="web-ui",
                port=4040,
                target_port=4040,
                protocol="TCP",
            )
        ],
    ),
    opts=pulumi.ResourceOptions(depends_on=[spark_connect]),
)

spark_connect_ui_ingress = k8s.networking.v1.Ingress(
    "spark-connect-ui-ingress",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="spark-connect-ui",
        namespace=namespace_name,
        labels={
            "app": connect_name,
        },
    ),
    spec=k8s.networking.v1.IngressSpecArgs(
        ingress_class_name="tailscale",
        rules=[
            k8s.networking.v1.IngressRuleArgs(
                host=ui_hostname,
                http=k8s.networking.v1.HTTPIngressRuleValueArgs(
                    paths=[
                        k8s.networking.v1.HTTPIngressPathArgs(
                            path="/",
                            path_type="Prefix",
                            backend=k8s.networking.v1.IngressBackendArgs(
                                service=k8s.networking.v1.IngressServiceBackendArgs(
                                    name=spark_connect_ui_service.metadata.name,
                                    port=k8s.networking.v1.ServiceBackendPortArgs(
                                        number=4040,
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
                hosts=[ui_hostname],
            )
        ],
    ),
    opts=pulumi.ResourceOptions(depends_on=[spark_connect]),
)

dashboard_config_maps(
    name_prefix="spark-dashboard",
    namespace=spark_namespace.metadata.name,
    dashboards_dir=dashboards_dir,
    dashboard_files=dashboard_files,
    labels={"app": "spark-operator"},
    opts=pulumi.ResourceOptions(depends_on=[spark_operator]),
)

pulumi.export("namespace", spark_namespace.metadata.name)
pulumi.export("chart_version", CHART_VERSION)
pulumi.export("spark_image", spark_image)
pulumi.export("spark_connect_name", spark_connect.metadata["name"])
pulumi.export("spark_connect_hostname", connect_hostname)
pulumi.export("spark_ui_hostname", ui_hostname)
pulumi.export("iceberg_version", ICEBERG_VERSION)
pulumi.export("iceberg_package", ICEBERG_PACKAGE)
pulumi.export("iceberg_aws_bundle_package", ICEBERG_AWS_BUNDLE_PACKAGE)
pulumi.export("postgresql_jdbc_package", POSTGRESQL_JDBC_PACKAGE)
pulumi.export("iceberg_catalog", iceberg_catalog_name)
pulumi.export("iceberg_jdbc_catalog_name", iceberg_jdbc_catalog_name)
pulumi.export("iceberg_jdbc_database", iceberg_database)
pulumi.export("iceberg_warehouse", iceberg_warehouse)
pulumi.export("iceberg_s3_endpoint", rustfs_s3_endpoint_url)
pulumi.export("iceberg_credentials_secret", spark_iceberg_credentials.metadata.name)
pulumi.export("legacy_local_iceberg_warehouse", LEGACY_LOCAL_ICEBERG_WAREHOUSE_URI)
