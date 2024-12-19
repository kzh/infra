import pulumi
import pulumi_kubernetes as k8s

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
                    "database": "n8n",
                    "username": "n8n",
                    "password": "n8n",
                },
            },
        },
        "primary": {
            "resourcesPreset": "none",
            "persistence": {
                "size": "20Gi",
            },
        },
    },
    namespace=namespace.metadata.name,
)

labels = {
    "app": "n8n",
}

pvc = k8s.core.v1.PersistentVolumeClaim(
    "storage",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        namespace=namespace.metadata.name,
        name="n8n",
        labels=labels,
    ),
    spec=k8s.core.v1.PersistentVolumeClaimSpecArgs(
        access_modes=["ReadWriteOnce"],
        resources=k8s.core.v1.ResourceRequirementsArgs(
            requests={"storage": "4Gi"},
        ),
        storage_class_name="rook-ceph-block",
    ),
)

deployment = k8s.apps.v1.Deployment(
    "deployment",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        namespace=namespace.metadata.name,
        name="n8n",
        labels=labels,
    ),
    spec=k8s.apps.v1.DeploymentSpecArgs(
        selector=k8s.meta.v1.LabelSelectorArgs(
            match_labels=labels,
        ),
        replicas=1,
        template=k8s.core.v1.PodTemplateSpecArgs(
            metadata=k8s.meta.v1.ObjectMetaArgs(
                labels=labels,
            ),
            spec=k8s.core.v1.PodSpecArgs(
                init_containers=[
                    k8s.core.v1.ContainerArgs(
                        name="init",
                        image="busybox",
                        command=["sh", "-c", "chown 1000:1000 /data"],
                        volume_mounts=[
                            k8s.core.v1.VolumeMountArgs(
                                name="n8n",
                                mount_path="/data",
                            ),
                        ],
                    ),
                ],
                containers=[
                    k8s.core.v1.ContainerArgs(
                        name="n8n",
                        image="n8nio/n8n:latest",
                        env=[
                            k8s.core.v1.EnvVarArgs(
                                name="DB_TYPE",
                                value="postgresdb",
                            ),
                            k8s.core.v1.EnvVarArgs(
                                name="DB_POSTGRESDB_HOST",
                                value="postgresql-hl",
                            ),
                            k8s.core.v1.EnvVarArgs(
                                name="DB_POSTGRESDB_PORT",
                                value="5432",
                            ),
                            k8s.core.v1.EnvVarArgs(
                                name="DB_POSTGRESDB_DATABASE",
                                value="n8n",
                            ),
                            k8s.core.v1.EnvVarArgs(
                                name="DB_POSTGRESDB_USER",
                                value="n8n",
                            ),
                            k8s.core.v1.EnvVarArgs(
                                name="DB_POSTGRESDB_PASSWORD",
                                value="n8n",
                            ),
                            k8s.core.v1.EnvVarArgs(
                                name="N8N_PROTOCOL",
                                value="http",
                            ),
                            k8s.core.v1.EnvVarArgs(
                                name="N8N_PORT",
                                value="5678",
                            ),
                        ],
                        command=["/bin/sh"],
                        args=[
                            "-c",
                            "sleep 5; n8n start",
                        ],
                        ports=[
                            k8s.core.v1.ContainerPortArgs(
                                container_port=5678,
                            ),
                        ],
                        volume_mounts=[
                            k8s.core.v1.VolumeMountArgs(
                                name="n8n",
                                mount_path="/home/node/.n8n",
                            ),
                        ],
                    ),
                ],
                volumes=[
                    k8s.core.v1.VolumeArgs(
                        name="n8n",
                        persistent_volume_claim=k8s.core.v1.PersistentVolumeClaimVolumeSourceArgs(
                            claim_name=pvc.metadata.name,
                        ),
                    ),
                ],
            ),
        ),
    ),
)

service = k8s.core.v1.Service(
    "service",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        namespace=namespace.metadata.name,
        name="n8n",
        labels=labels,
    ),
    spec=k8s.core.v1.ServiceSpecArgs(
        type="ClusterIP",
        ports=[
            k8s.core.v1.ServicePortArgs(
                port=5678,
                target_port=5678,
            ),
        ],
        selector=labels,
    ),
)

ingress = k8s.networking.v1.Ingress(
    "ingress",
    metadata={
        "name": "n8n",
        "namespace": namespace.metadata["name"],
        "labels": labels,
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
                                    name="n8n",
                                    port=k8s.networking.v1.ServiceBackendPortArgs(
                                        number=5678,
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
                hosts=["n8n"],
            ),
        ],
    ),
)
