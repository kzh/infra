import pulumi
import pulumi_kubernetes as k8s

config = pulumi.Config()
ns = k8s.core.v1.Namespace(
    "namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=config.require("namespace"),
    ),
)

labels = {"app": "chroma"}

svc = k8s.core.v1.Service(
    "service",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="chroma",
        namespace=ns.metadata.name,
        annotations={
            "tailscale.com/expose": "true",
            "tailscale.com/hostname": "chroma",
        },
    ),
    spec=k8s.core.v1.ServiceSpecArgs(
        type="ClusterIP",
        selector=labels,
        ports=[
            k8s.core.v1.ServicePortArgs(
                port=8000,
                target_port=8000,
            )
        ],
    ),
)

sts = k8s.apps.v1.StatefulSet(
    "statefulset",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="chroma",
        namespace=ns.metadata.name,
    ),
    spec=k8s.apps.v1.StatefulSetSpecArgs(
        service_name=svc.metadata.name,
        selector=k8s.meta.v1.LabelSelectorArgs(
            match_labels=labels,
        ),
        template=k8s.core.v1.PodTemplateSpecArgs(
            metadata=k8s.meta.v1.ObjectMetaArgs(
                labels=labels,
            ),
            spec=k8s.core.v1.PodSpecArgs(
                containers=[
                    k8s.core.v1.ContainerArgs(
                        name="chroma",
                        image="ghcr.io/chroma-core/chroma:0.6.3",
                        ports=[
                            k8s.core.v1.ContainerPortArgs(
                                container_port=8000,
                            )
                        ],
                        volume_mounts=[
                            k8s.core.v1.VolumeMountArgs(
                                name="data",
                                mount_path="/chroma/chroma",
                            )
                        ],
                    ),
                ],
            ),
        ),
        volume_claim_templates=[
            k8s.core.v1.PersistentVolumeClaimArgs(
                metadata=k8s.meta.v1.ObjectMetaArgs(
                    name="data",
                ),
                spec=k8s.core.v1.PersistentVolumeClaimSpecArgs(
                    access_modes=["ReadWriteOnce"],
                    resources=k8s.core.v1.ResourceRequirementsArgs(
                        requests={"storage": "100Gi"}
                    ),
                ),
            )
        ],
    ),
)
