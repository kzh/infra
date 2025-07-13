import pulumi
import pulumi_kubernetes as k8s
import pulumi_random as random

namespace = k8s.core.v1.Namespace(
    "namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="airflow",
    ),
)

secret = random.RandomPassword(
    "password",
    length=16,
    special=True,
    lower=True,
    upper=True,
    numeric=True,
)

chart = k8s.helm.v4.Chart(
    "airflow",
    chart="airflow",
    repository_opts=k8s.helm.v4.RepositoryOptsArgs(
        repo="https://airflow.apache.org",
    ),
    version="1.18.0",
    values={
        "createUserJob": {
            "useHelmHooks": False,
            "applyCustomEnv": False,
        },
        "migrateDatabaseJob": {
            "useHelmHooks": False,
            "applyCustomEnv": False,
        },
        "webserverSecretKey": secret.result,
    },
    namespace=namespace.metadata.apply(lambda m: m.name),
)

ingress = k8s.networking.v1.Ingress(
    "ingress",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="airflow",
        namespace=namespace.metadata.apply(lambda m: m.name),
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
                                    name="airflow-webserver",
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
                hosts=["airflow"],
            ),
        ],
    ),
)