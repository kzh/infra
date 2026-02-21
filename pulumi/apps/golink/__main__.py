import pulumi
import pulumi_kubernetes as k8s

config = pulumi.Config()

namespace_name = config.get("namespace") or "golink"
image = config.get("image") or "ghcr.io/tailscale/golink:main"
storage_size = config.get("storage_size") or "1Gi"
storage_class = config.get("storage_class")

labels = {
    "app": "golink",
}

golink_namespace = k8s.core.v1.Namespace(
    "golink-namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=namespace_name,
        labels=labels,
    ),
)

auth_secret = k8s.core.v1.Secret(
    "golink-auth-secret",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="golink-auth",
        namespace=golink_namespace.metadata.name,
        labels=labels,
    ),
    string_data={
        "TS_AUTHKEY": config.require_secret("TS_AUTHKEY"),
    },
)

pvc = k8s.core.v1.PersistentVolumeClaim(
    "golink-storage",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        namespace=golink_namespace.metadata.name,
        name="golink-storage",
        labels=labels,
    ),
    spec=k8s.core.v1.PersistentVolumeClaimSpecArgs(
        access_modes=["ReadWriteOnce"],
        resources=k8s.core.v1.ResourceRequirementsArgs(
            requests={"storage": storage_size},
        ),
        storage_class_name=storage_class,
    ),
)

deployment = k8s.apps.v1.Deployment(
    "golink-deployment",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        namespace=golink_namespace.metadata.name,
        name="golink",
        labels=labels,
    ),
    spec=k8s.apps.v1.DeploymentSpecArgs(
        replicas=1,
        strategy=k8s.apps.v1.DeploymentStrategyArgs(
            type="Recreate",
        ),
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
                        name="golink",
                        image=image,
                        image_pull_policy="IfNotPresent",
                        args=[
                            "--verbose",
                            "--sqlitedb",
                            "/home/nonroot/golink.db",
                            "--config-dir",
                            "/home/nonroot/tsnet-golink",
                        ],
                        env=[
                            k8s.core.v1.EnvVarArgs(
                                name="TS_AUTHKEY",
                                value_from=k8s.core.v1.EnvVarSourceArgs(
                                    secret_key_ref=k8s.core.v1.SecretKeySelectorArgs(
                                        name=auth_secret.metadata.name,
                                        key="TS_AUTHKEY",
                                    ),
                                ),
                            ),
                            k8s.core.v1.EnvVarArgs(
                                name="XDG_CONFIG_HOME",
                                value="/home/nonroot",
                            ),
                        ],
                        security_context=k8s.core.v1.SecurityContextArgs(
                            run_as_non_root=True,
                            run_as_user=65532,
                            run_as_group=65532,
                            allow_privilege_escalation=False,
                            capabilities=k8s.core.v1.CapabilitiesArgs(
                                drop=["ALL"],
                            ),
                        ),
                        volume_mounts=[
                            k8s.core.v1.VolumeMountArgs(
                                name="data",
                                mount_path="/home/nonroot",
                            ),
                        ],
                    ),
                ],
                volumes=[
                    k8s.core.v1.VolumeArgs(
                        name="data",
                        persistent_volume_claim=k8s.core.v1.PersistentVolumeClaimVolumeSourceArgs(
                            claim_name=pvc.metadata.name,
                        ),
                    ),
                ],
            ),
        ),
    ),
    opts=pulumi.ResourceOptions(depends_on=[golink_namespace, auth_secret, pvc]),
)

pulumi.export("namespace", golink_namespace.metadata.name)
pulumi.export("deployment", deployment.metadata.name)
pulumi.export("pvc", pvc.metadata.name)
pulumi.export("sqlitedb_path", "/home/nonroot/golink.db")
pulumi.export("tailscale_config_dir", "/home/nonroot/tsnet-golink")
