"""A Kubernetes Python Pulumi program"""

import pulumi_kubernetes.apps.v1 as appsv1
import pulumi_kubernetes.core.v1 as corev1
import pulumi_kubernetes.meta.v1 as metav1
from pulumi import Config

app_labels = {"app": "golink"}

namespace = corev1.Namespace(
    "namespace",
    metadata=metav1.ObjectMetaArgs(name="golink"),
)

pvc = corev1.PersistentVolumeClaim(
    "storage",
    metadata=metav1.ObjectMetaArgs(name="golink", namespace=namespace.metadata.name),
    spec=corev1.PersistentVolumeClaimSpecArgs(
        access_modes=["ReadWriteOnce"],
        storage_class_name="rook-ceph-block",
        resources=corev1.VolumeResourceRequirementsArgs(
            requests={"storage": "2Gi"},
        ),
    ),
)

config = Config()

secret = corev1.Secret(
    "tailscale-auth",
    metadata=metav1.ObjectMetaArgs(name="golink", namespace=namespace.metadata.name),
    string_data={"TS_AUTHKEY": config.get_secret(key="TS_AUTHKEY")},
)

deployment = appsv1.Deployment(
    "deployment",
    metadata=metav1.ObjectMetaArgs(
        name="golink", namespace=namespace.metadata.name, labels=app_labels
    ),
    spec=appsv1.DeploymentSpecArgs(
        selector=metav1.LabelSelectorArgs(match_labels=app_labels),
        replicas=1,
        template=corev1.PodTemplateSpecArgs(
            metadata=metav1.ObjectMetaArgs(labels=app_labels),
            spec=corev1.PodSpecArgs(
                containers=[
                    corev1.ContainerArgs(
                        name="golink",
                        image="ghcr.io/tailscale/golink:main",
                        volume_mounts=(
                            corev1.VolumeMountArgs(
                                name="storage",
                                mount_path="/home/nonroot/",
                            ),
                        ),
                        env=[
                            corev1.EnvVarArgs(
                                name="TS_AUTHKEY",
                                value_from=corev1.EnvVarSourceArgs(
                                    secret_key_ref=corev1.SecretKeySelectorArgs(
                                        name=secret.metadata.name,
                                        key="TS_AUTHKEY",
                                    )
                                ),
                            )
                        ],
                    )
                ],
                security_context=corev1.PodSecurityContextArgs(
                    fs_group=65532,
                    run_as_user=65532,
                    run_as_group=65532,
                ),
                volumes=[
                    corev1.VolumeArgs(
                        name="storage",
                        persistent_volume_claim=corev1.PersistentVolumeClaimVolumeSourceArgs(
                            claim_name=pvc.metadata.name,
                        ),
                    )
                ],
            ),
        ),
    ),
)
