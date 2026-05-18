import pulumi_kubernetes as k8s
from pulumi_spark_operator_crds.sparkoperator.v1alpha1 import SparkConnect

import pulumi

CHART_VERSION = "2.5.0"
SPARK_VERSION = "4.1.1"
SPARK_JAVA_VERSION = "21"
ICEBERG_VERSION = "1.10.1"
ICEBERG_SPARK_RUNTIME = "4.0_2.13"
ICEBERG_PACKAGE = f"org.apache.iceberg:iceberg-spark-runtime-{ICEBERG_SPARK_RUNTIME}:{ICEBERG_VERSION}"
ICEBERG_CATALOG_NAME = "local"
ICEBERG_WAREHOUSE_MOUNT_PATH = "/var/lib/spark/warehouse"
ICEBERG_WAREHOUSE_URI = f"file://{ICEBERG_WAREHOUSE_MOUNT_PATH}"
DEFAULT_SPARK_IMAGE = (
    "ghcr.io/kzh/spark:"
    f"{SPARK_VERSION}-iceberg{ICEBERG_VERSION}-java{SPARK_JAVA_VERSION}"
)

config = pulumi.Config()
namespace_name = config.require("namespace")
connect_name = config.get("connect_name") or "spark-connect"
connect_hostname = config.get("connect_hostname") or connect_name
ui_hostname = config.get("ui_hostname") or "spark"
spark_image = config.get("image") or DEFAULT_SPARK_IMAGE
warehouse_storage_size = config.get("warehouse_storage_size") or "20Gi"
warehouse_storage_class = config.get("warehouse_storage_class") or "local-path"

labels = {
    "app": "spark-operator",
}

connect_server_selector = {
    "spark-role": "connect-server",
    "spark-version": SPARK_VERSION,
    "sparkoperator.k8s.io/connect-name": connect_name,
    "sparkoperator.k8s.io/launched-by-spark-operator": "true",
}

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
            "spark.sql.extensions": "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions",
            f"spark.sql.catalog.{ICEBERG_CATALOG_NAME}": "org.apache.iceberg.spark.SparkCatalog",
            f"spark.sql.catalog.{ICEBERG_CATALOG_NAME}.type": "hadoop",
            f"spark.sql.catalog.{ICEBERG_CATALOG_NAME}.warehouse": ICEBERG_WAREHOUSE_URI,
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
                    "volumes": [warehouse_volume],
                    "containers": [
                        {
                            "name": "spark-kubernetes-driver",
                            "image": spark_image,
                            "imagePullPolicy": "IfNotPresent",
                            "volumeMounts": [warehouse_volume_mount],
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
    opts=pulumi.ResourceOptions(depends_on=[spark_operator, spark_warehouse]),
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

pulumi.export("namespace", spark_namespace.metadata.name)
pulumi.export("chart_version", CHART_VERSION)
pulumi.export("spark_image", spark_image)
pulumi.export("spark_connect_name", spark_connect.metadata["name"])
pulumi.export("spark_connect_hostname", connect_hostname)
pulumi.export("spark_ui_hostname", ui_hostname)
pulumi.export("iceberg_version", ICEBERG_VERSION)
pulumi.export("iceberg_package", ICEBERG_PACKAGE)
pulumi.export("iceberg_catalog", ICEBERG_CATALOG_NAME)
pulumi.export("iceberg_warehouse", ICEBERG_WAREHOUSE_URI)
