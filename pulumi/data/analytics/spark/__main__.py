import pulumi
import pulumi_kubernetes as k8s

CHART_VERSION = "2.4.0"
SPARK_VERSION = "4.0.1"

config = pulumi.Config()
namespace_name = config.require("namespace")
connect_name = config.get("connect_name") or "spark-connect"
connect_hostname = config.get("connect_hostname") or connect_name
ui_hostname = config.get("ui_hostname") or "spark"

labels = {
    "app": "spark-operator",
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

spark_connect = k8s.apiextensions.CustomResource(
    "spark-connect",
    api_version="sparkoperator.k8s.io/v1alpha1",
    kind="SparkConnect",
    metadata={
        "name": connect_name,
        "namespace": namespace_name,
        "labels": {
            "app": connect_name,
        },
    },
    spec={
        "sparkVersion": SPARK_VERSION,
        "image": f"docker.io/library/spark:{SPARK_VERSION}",
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
                    "containers": [
                        {
                            "name": "spark-kubernetes-driver",
                            "image": f"docker.io/library/spark:{SPARK_VERSION}",
                            "imagePullPolicy": "IfNotPresent",
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
                            "image": f"docker.io/library/spark:{SPARK_VERSION}",
                            "imagePullPolicy": "IfNotPresent",
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
    },
    opts=pulumi.ResourceOptions(depends_on=[spark_operator]),
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
        selector={
            "spark-role": "connect-server",
            "spark-version": SPARK_VERSION,
            "sparkoperator.k8s.io/connect-name": connect_name,
            "sparkoperator.k8s.io/launched-by-spark-operator": "true",
        },
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
                                    name=connect_name,
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
pulumi.export("spark_connect_name", spark_connect.metadata["name"])
pulumi.export("spark_connect_hostname", connect_hostname)
pulumi.export("spark_ui_hostname", ui_hostname)
