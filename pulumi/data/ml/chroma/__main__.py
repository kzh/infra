import pulumi
import pulumi_kubernetes as k8s

config = pulumi.Config()

image_version = config.get("image_version") or "1.5.0"
selector_version = config.get("selector_version") or "1.0.15"
storage_size = config.get("storage_size") or "100Gi"
storage_class = config.get("storage_class")
replicas = config.get_int("replicas") or 1
cpu_limit = config.get("cpu_limit") or "1000m"
memory_limit = config.get("memory_limit") or "2Gi"
cpu_request = config.get("cpu_request") or "100m"
memory_request = config.get("memory_request") or "512Mi"

chroma_namespace = k8s.core.v1.Namespace(
    "chroma-namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=config.require("namespace"),
    ),
)

labels = {
    "app": "chroma",
    "component": "vector-database",
    "version": selector_version.replace(".", "-"),
}

pod_labels = {
    **labels,
    "image-version": image_version.replace(".", "-"),
}

svc = k8s.core.v1.Service(
    "chroma-service",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="chroma-db",
        namespace=chroma_namespace.metadata.name,
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
    "chroma-statefulset",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="chroma-db",
        namespace=chroma_namespace.metadata.name,
    ),
    spec=k8s.apps.v1.StatefulSetSpecArgs(
        replicas=replicas,
        service_name=svc.metadata.name,
        selector=k8s.meta.v1.LabelSelectorArgs(
            match_labels=labels,
        ),
        template=k8s.core.v1.PodTemplateSpecArgs(
            metadata=k8s.meta.v1.ObjectMetaArgs(
                labels=pod_labels,
            ),
            spec=k8s.core.v1.PodSpecArgs(
                containers=[
                    k8s.core.v1.ContainerArgs(
                        name="chroma",
                        image=f"ghcr.io/chroma-core/chroma:{image_version}",
                        ports=[
                            k8s.core.v1.ContainerPortArgs(
                                container_port=8000,
                            )
                        ],
                        resources=k8s.core.v1.ResourceRequirementsArgs(
                            limits={
                                "cpu": cpu_limit,
                                "memory": memory_limit,
                            },
                            requests={
                                "cpu": cpu_request,
                                "memory": memory_request,
                            },
                        ),
                        liveness_probe=k8s.core.v1.ProbeArgs(
                            http_get=k8s.core.v1.HTTPGetActionArgs(
                                path="/api/v2/heartbeat",
                                port=8000,
                            ),
                            initial_delay_seconds=30,
                            period_seconds=10,
                            timeout_seconds=5,
                            failure_threshold=3,
                        ),
                        readiness_probe=k8s.core.v1.ProbeArgs(
                            http_get=k8s.core.v1.HTTPGetActionArgs(
                                path="/api/v2/heartbeat",
                                port=8000,
                            ),
                            initial_delay_seconds=10,
                            period_seconds=5,
                            timeout_seconds=3,
                            failure_threshold=3,
                        ),
                        startup_probe=k8s.core.v1.ProbeArgs(
                            http_get=k8s.core.v1.HTTPGetActionArgs(
                                path="/api/v2/heartbeat",
                                port=8000,
                            ),
                            initial_delay_seconds=10,
                            period_seconds=10,
                            timeout_seconds=5,
                            failure_threshold=30,
                        ),
                        security_context=k8s.core.v1.SecurityContextArgs(
                            run_as_non_root=True,
                            run_as_user=1000,
                            run_as_group=1000,
                            read_only_root_filesystem=False,
                            allow_privilege_escalation=False,
                            capabilities=k8s.core.v1.CapabilitiesArgs(
                                drop=["ALL"],
                            ),
                        ),
                        volume_mounts=[
                            k8s.core.v1.VolumeMountArgs(
                                name="data",
                                mount_path="/data",
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
                        requests={"storage": storage_size}
                    ),
                    storage_class_name=storage_class,
                ),
            )
        ],
    ),
)

pulumi.export("namespace", chroma_namespace.metadata.name)
pulumi.export("service_name", svc.metadata.name)
pulumi.export("service_endpoint", pulumi.Output.concat("http://", svc.metadata.name, ".", chroma_namespace.metadata.name, ".svc.cluster.local:8000"))
pulumi.export("tailscale_hostname", "chroma")
pulumi.export("image_version", image_version)
