import pulumi_kubernetes as k8s

import pulumi

config = pulumi.Config()

namespace_name = config.get("namespace", "flink")
operator_chart_version = config.get("operatorChartVersion", "1.14.0")
cluster_name = config.get("clusterName", "flink-session")
hostname = config.get("hostname", "flink")
flink_version = config.get("flinkVersion", "v2_2")
flink_image = config.get("image", "docker.io/library/flink:2.2.0-scala_2.12-java17")
jobmanager_memory = config.get("jobManagerMemory", "1024m")
jobmanager_cpu = float(config.get("jobManagerCpu", "0.5"))
taskmanager_memory = config.get("taskManagerMemory", "1024m")
taskmanager_cpu = float(config.get("taskManagerCpu", "0.5"))
taskmanager_replicas = config.get_int("taskManagerReplicas")
if taskmanager_replicas is None:
    taskmanager_replicas = 1
task_slots = config.get_int("taskSlots")
if task_slots is None:
    task_slots = 2
parallelism = config.get_int("parallelism")
if parallelism is None:
    parallelism = 1

labels = {
    "app.kubernetes.io/name": "flink",
    "app.kubernetes.io/part-of": "flink",
}

flink_namespace = k8s.core.v1.Namespace(
    "flink-namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=namespace_name,
        labels=labels,
    ),
)

flink_operator = k8s.helm.v3.Release(
    "flink-kubernetes-operator",
    chart="flink-kubernetes-operator",
    name="flink-kubernetes-operator",
    namespace=flink_namespace.metadata.name,
    repository_opts=k8s.helm.v3.RepositoryOptsArgs(
        repo="https://downloads.apache.org/flink/flink-kubernetes-operator-1.14.0/",
    ),
    version=operator_chart_version,
    values={
        "webhook": {
            "create": False,
        },
        "operatorPod": {
            "resources": {
                "requests": {
                    "cpu": "100m",
                    "memory": "512Mi",
                },
                "limits": {
                    "cpu": "500m",
                    "memory": "512Mi",
                },
            },
        },
    },
    opts=pulumi.ResourceOptions(depends_on=[flink_namespace]),
)

flink_session_cluster = k8s.apiextensions.CustomResource(
    "flink-session-cluster",
    api_version="flink.apache.org/v1beta1",
    kind="FlinkDeployment",
    metadata={
        "name": cluster_name,
        "namespace": namespace_name,
        "labels": labels,
    },
    spec={
        "image": flink_image,
        "imagePullPolicy": "IfNotPresent",
        "flinkVersion": flink_version,
        "serviceAccount": "flink",
        "flinkConfiguration": {
            "kubernetes.rest-service.exposed.type": "ClusterIP",
            "parallelism.default": str(parallelism),
            "taskmanager.numberOfTaskSlots": str(task_slots),
        },
        "jobManager": {
            "resource": {
                "memory": jobmanager_memory,
                "cpu": jobmanager_cpu,
            },
        },
        "taskManager": {
            "replicas": taskmanager_replicas,
            "resource": {
                "memory": taskmanager_memory,
                "cpu": taskmanager_cpu,
            },
        },
    },
    opts=pulumi.ResourceOptions(depends_on=[flink_operator]),
)

flink_ui_ingress = k8s.networking.v1.Ingress(
    "flink-ui-ingress",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="flink-ui",
        namespace=namespace_name,
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
                                    name=f"{cluster_name}-rest",
                                    port=k8s.networking.v1.ServiceBackendPortArgs(
                                        number=8081,
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
    opts=pulumi.ResourceOptions(depends_on=[flink_session_cluster]),
)

pulumi.export("namespace", flink_namespace.metadata.name)
pulumi.export("operatorChartVersion", operator_chart_version)
pulumi.export("clusterName", flink_session_cluster.metadata["name"])
pulumi.export("flinkVersion", flink_version)
pulumi.export("image", flink_image)
pulumi.export("restService", f"{cluster_name}-rest")
pulumi.export("hostname", hostname)
pulumi.export("url", pulumi.Output.format("https://{0}", hostname))
pulumi.export("taskManagerReplicas", taskmanager_replicas)
pulumi.export("taskSlots", task_slots)
pulumi.export("parallelism", parallelism)
pulumi.export("ingressName", flink_ui_ingress.metadata.name)
