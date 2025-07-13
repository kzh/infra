import pulumi
import pulumi_kubernetes as k8s

config = pulumi.Config()
namespace = k8s.core.v1.Namespace(
    "namespace", metadata={"name": config.require("namespace")}
)

chart = k8s.helm.v4.Chart(
    "chart",
    namespace=namespace.metadata["name"],
    chart="oci://registry-1.docker.io/bitnamicharts/spark",
    version="9.3.0",
    values={
        "worker": {
            "replicaCount": 1,
        },
        "service": {
            "annotations": {
                "tailscale.com/expose": "true",
                "tailscale.com/hostname": "spark-external",
            }
        },
    },
)

ingress = k8s.networking.v1.Ingress(
    "ingress",
    metadata={
        "name": "spark",
        "namespace": namespace.metadata["name"],
    },
    spec=k8s.networking.v1.IngressSpecArgs(
        ingress_class_name="tailscale",
        rules=[
            k8s.networking.v1.IngressRuleArgs(
                http=k8s.networking.v1.HTTPIngressRuleValueArgs(
                    paths=[
                        k8s.networking.v1.HTTPIngressPathArgs(
                            path="/",
                            path_type="Prefix",
                            backend=k8s.networking.v1.IngressBackendArgs(
                                service=k8s.networking.v1.IngressServiceBackendArgs(
                                    name="chart-spark-master-svc",
                                    port=k8s.networking.v1.ServiceBackendPortArgs(
                                        number=80,
                                    ),
                                ),
                            ),
                        ),
                    ],
                ),
            ),
        ],
        tls=[
            k8s.networking.v1.IngressTLSArgs(
                hosts=["spark"],
            ),
        ],
    ),
)
