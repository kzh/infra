import pulumi
import pulumi_kubernetes as k8s

config = pulumi.Config()
chart = k8s.helm.v3.Release(
    "chart",
    chart="airbyte",
    namespace=config.require("namespace"),
    create_namespace=True,
    repository_opts=k8s.helm.v4.RepositoryOptsArgs(
        repo="https://airbytehq.github.io/helm-charts",
    ),
    version="1.3.0",
)

ingress = k8s.networking.v1.Ingress(
    "ingress",
    metadata={
        "name": "airbyte",
        "namespace": chart.namespace,
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
                                    name=chart.name.apply(
                                        lambda name: f"{name}-airbyte-webapp-svc"
                                    ),
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
                hosts=["airbyte"],
            ),
        ],
    ),
)
