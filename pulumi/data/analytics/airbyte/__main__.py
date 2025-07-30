import pulumi
import pulumi_kubernetes as k8s

config = pulumi.Config()

airbyte_namespace = k8s.core.v1.Namespace(
    "airbyte-namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=config.require("namespace"),
    ),
)

airbyte_chart = k8s.helm.v4.Chart(
    "airbyte",
    chart="airbyte",
    namespace=airbyte_namespace.metadata.name,
    repository_opts=k8s.helm.v4.RepositoryOptsArgs(
        repo="https://airbytehq.github.io/helm-charts",
    ),
    version="1.3.0",
    opts=pulumi.ResourceOptions(depends_on=[airbyte_namespace]),
)

airbyte_ingress = k8s.networking.v1.Ingress(
    "airbyte-ingress",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="airbyte",
        namespace=config.require("namespace"),
    ),
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
                                    name="airbyte-airbyte-webapp-svc",
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
    opts=pulumi.ResourceOptions(depends_on=[airbyte_chart]),
)
