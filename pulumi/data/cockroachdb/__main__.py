import pulumi
import pulumi_kubernetes as k8s

config = pulumi.Config()
namespace = k8s.core.v1.Namespace(
    "namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=config.require("namespace"),
    ),
)

chart = k8s.helm.v4.Chart(
    "cockroachdb",
    chart="cockroachdb",
    repository_opts=k8s.helm.v4.RepositoryOptsArgs(
        repo="https://charts.cockroachdb.com",
    ),
    namespace=namespace.metadata.name,
    version="15.0.1",
    values={
        "image": {"repository": "cockroachdb/cockroach", "tag": "v24.3.1"},
        "conf": {"single-node": True, "max-sql-memory": "8G", "cache": "8G"},
        "statefulset": {"replicas": 1},
        "tls": {"enabled": False},
        "service": {
            "public": {
                "annotations": {
                    "tailscale.com/expose": "true",
                    "tailscale.com/hostname": "cockroachdb-public",
                }
            }
        },
        "storage": {"persistentVolume": {"size": "100Gi"}},
    },
)

ingress = k8s.networking.v1.Ingress(
    "ingress",
    metadata={
        "name": "cockroachdb",
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
                                    name="cockroachdb-public",
                                    port=k8s.networking.v1.ServiceBackendPortArgs(
                                        number=8080,
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
                hosts=["cockroach"],
            ),
        ],
    ),
)
