import pulumi
import pulumi_kubernetes as k8s
import pulumi_random as random

CHART_VERSION = "0.0.85"
CONSOLE_HOSTNAME = "rustfs"
S3_HOSTNAME = "rustfs-s3"

config = pulumi.Config()
namespace_name = config.get("namespace") or "rustfs"
storage_class_name = config.get("storageClassName") or "local-path"

labels = {
    "app": "rustfs",
}

access_key = random.RandomPassword(
    "rustfs-access-key",
    length=20,
    special=False,
)

secret_key = random.RandomPassword(
    "rustfs-secret-key",
    length=40,
    special=False,
)

namespace = k8s.core.v1.Namespace(
    "rustfs-namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=namespace_name,
        labels=labels,
    ),
)

rustfs_chart = k8s.helm.v4.Chart(
    "rustfs",
    chart="rustfs",
    namespace=namespace.metadata.name,
    version=CHART_VERSION,
    repository_opts=k8s.helm.v4.RepositoryOptsArgs(
        repo="https://charts.rustfs.com",
    ),
    values={
        "fullnameOverride": "rustfs",
        "commonLabels": labels,
        "mode": {
            "standalone": {
                "enabled": True,
            },
            "distributed": {
                "enabled": False,
            },
        },
        "replicaCount": 1,
        "ingress": {
            "enabled": False,
        },
        "resources": {},
        "secret": {
            "rustfs": {
                "access_key": access_key.result,
                "secret_key": secret_key.result,
            },
        },
        "storageclass": {
            "name": storage_class_name,
        },
    },
    opts=pulumi.ResourceOptions(depends_on=[namespace]),
)

rustfs_console_service = k8s.core.v1.Service(
    "rustfs-console-service",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="rustfs-console",
        namespace=namespace.metadata.name,
        labels=labels,
    ),
    spec=k8s.core.v1.ServiceSpecArgs(
        type="ClusterIP",
        selector={
            "app.kubernetes.io/instance": "rustfs",
            "app.kubernetes.io/name": "rustfs",
        },
        ports=[
            k8s.core.v1.ServicePortArgs(
                name="console",
                port=9001,
                target_port=9001,
            ),
        ],
    ),
    opts=pulumi.ResourceOptions(depends_on=[rustfs_chart]),
)

rustfs_console_ingress = k8s.networking.v1.Ingress(
    "rustfs-console-ingress",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="rustfs",
        namespace=namespace.metadata.name,
        labels=labels,
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
                                    name="rustfs-console",
                                    port=k8s.networking.v1.ServiceBackendPortArgs(
                                        number=9001,
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
                hosts=[CONSOLE_HOSTNAME],
            ),
        ],
    ),
    opts=pulumi.ResourceOptions(depends_on=[rustfs_console_service]),
)

# The chart does not expose service annotations, so create a dedicated service
# for the S3 API that Tailscale can publish directly.
rustfs_s3_service = k8s.core.v1.Service(
    "rustfs-s3-service",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="rustfs-s3",
        namespace=namespace.metadata.name,
        labels=labels,
        annotations={
            "tailscale.com/expose": "true",
            "tailscale.com/hostname": S3_HOSTNAME,
        },
    ),
    spec=k8s.core.v1.ServiceSpecArgs(
        type="ClusterIP",
        selector={
            "app.kubernetes.io/instance": "rustfs",
            "app.kubernetes.io/name": "rustfs",
        },
        ports=[
            k8s.core.v1.ServicePortArgs(
                name="s3",
                port=9000,
                target_port=9000,
            ),
        ],
    ),
    opts=pulumi.ResourceOptions(depends_on=[rustfs_chart]),
)

pulumi.export("namespace", namespace.metadata.name)
pulumi.export("chart_version", CHART_VERSION)
pulumi.export("console_hostname", CONSOLE_HOSTNAME)
pulumi.export("s3_hostname", S3_HOSTNAME)
pulumi.export("access_key", pulumi.Output.secret(access_key.result))
pulumi.export("secret_key", pulumi.Output.secret(secret_key.result))
