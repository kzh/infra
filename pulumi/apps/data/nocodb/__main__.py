import pulumi
import pulumi_kubernetes as k8s
import pulumi_random as random

config = pulumi.Config()
namespace = k8s.core.v1.Namespace(
    "namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=config.require("namespace"),
    ),
)

postgres = k8s.helm.v4.Chart(
    "postgresql",
    chart="oci://registry-1.docker.io/bitnamicharts/postgresql",
    version="16.3.2",
    values={
        "global": {
            "postgresql": {
                "auth": {
                    "database": "nocodb",
                    "username": "nocodb",
                    "password": "nocodb",
                },
            },
        },
        "primary": {
            "resourcesPreset": "none",
            "persistence": {
                "size": "40Gi",
            },
        },
    },
    namespace=namespace.metadata.name,
)

pvc = k8s.core.v1.PersistentVolumeClaim(
    "storage",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="nocodb",
        namespace=namespace.metadata.name,
    ),
    spec=k8s.core.v1.PersistentVolumeClaimSpecArgs(
        access_modes=["ReadWriteOnce"],
        resources=k8s.core.v1.ResourceRequirementsArgs(
            requests={"storage": "40Gi"},
        ),
        storage_class_name="rook-ceph-block",
    ),
)

secret = random.RandomPassword(
    "secret",
    length=32,
)

labels = {
    "app": "nocodb",
}

deployment = (
    k8s.apps.v1.Deployment(
        "deployment",
        metadata=k8s.meta.v1.ObjectMetaArgs(
            name="nocodb",
            namespace=namespace.metadata.name,
        ),
        spec=k8s.apps.v1.DeploymentSpecArgs(
            selector=k8s.meta.v1.LabelSelectorArgs(
                match_labels=labels,
            ),
            template=k8s.core.v1.PodTemplateSpecArgs(
                metadata=k8s.meta.v1.ObjectMetaArgs(
                    name="nocodb",
                    labels=labels,
                ),
                spec=k8s.core.v1.PodSpecArgs(
                    containers=[
                        k8s.core.v1.ContainerArgs(
                            name="nocodb",
                            image="nocodb/nocodb:0.258.7",
                            ports=[
                                k8s.core.v1.ContainerPortArgs(
                                    container_port=8080,
                                )
                            ],
                            volume_mounts=[
                                k8s.core.v1.VolumeMountArgs(
                                    name="nocodb",
                                    mount_path="/usr/app/data/",
                                )
                            ],
                            env=[
                                k8s.core.v1.EnvVarArgs(
                                    name="NC_AUTH_JWT_SECRET",
                                    value=secret.result,
                                ),
                                k8s.core.v1.EnvVarArgs(
                                    name="NC_DB",
                                    value="pg://postgresql-hl:5432?user=nocodb&password=nocodb&d=nocodb",
                                ),
                            ],
                        )
                    ],
                    volumes=[
                        k8s.core.v1.VolumeArgs(
                            name="nocodb",
                            persistent_volume_claim=k8s.core.v1.PersistentVolumeClaimVolumeSourceArgs(
                                claim_name=pvc.metadata.name,
                            ),
                        )
                    ],
                ),
            ),
        ),
    ),
)

service = k8s.core.v1.Service(
    "service",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="nocodb",
        namespace=namespace.metadata.name,
    ),
    spec=k8s.core.v1.ServiceSpecArgs(
        type="ClusterIP",
        selector=labels,
        ports=[
            k8s.core.v1.ServicePortArgs(
                port=8080,
            ),
        ],
    ),
)

ingress = k8s.networking.v1.Ingress(
    "ingress",
    metadata={
        "name": "nocodb",
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
                                    name=service.metadata["name"],
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
                hosts=["nocodb"],
            ),
        ],
    ),
)
