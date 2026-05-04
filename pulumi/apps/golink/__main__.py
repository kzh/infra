import base64

import pulumi_kubernetes as k8s
import pulumi_tailscale as tailscale

import pulumi

config = pulumi.Config()

namespace_name = config.get("namespace") or "golink"
image = config.get("image") or "ghcr.io/tailscale/golink:main"
storage_size = config.get("storage_size") or "1Gi"
storage_class = config.get("storage_class")
tailnet_key_tags = config.get_object("tailnet_key_tags") or ["tag:golink"]
tailnet_key_expiry_seconds = (
    config.get_int("tailnet_key_expiry_seconds") or 60 * 60 * 24 * 90
)
tailnet_key_reusable = config.get_bool("tailnet_key_reusable")
tailnet_key_ephemeral = config.get_bool("tailnet_key_ephemeral")
tailnet_key_preauthorized = config.get_bool("tailnet_key_preauthorized")

if tailnet_key_reusable is None:
    tailnet_key_reusable = True
if tailnet_key_ephemeral is None:
    tailnet_key_ephemeral = False
if tailnet_key_preauthorized is None:
    tailnet_key_preauthorized = True
if not isinstance(tailnet_key_tags, list) or not all(
    isinstance(tag, str) for tag in tailnet_key_tags
):
    raise ValueError("golink:tailnet_key_tags must be a list of tag strings")

labels = {
    "app": "golink",
}

tailnet_key = tailscale.TailnetKey(
    "golink-tailnet-key",
    reusable=tailnet_key_reusable,
    ephemeral=tailnet_key_ephemeral,
    preauthorized=tailnet_key_preauthorized,
    expiry=tailnet_key_expiry_seconds,
    recreate_if_invalid="always",
    description="golink",
    tags=tailnet_key_tags,
)
tailnet_auth_key = pulumi.Output.secret(tailnet_key.key).apply(
    lambda key: base64.b64encode(key.encode()).decode()
)

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
    data={
        "TS_AUTHKEY": tailnet_auth_key,
    },
    opts=pulumi.ResourceOptions(delete_before_replace=True),
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
                security_context=k8s.core.v1.PodSecurityContextArgs(
                    fs_group=65532,
                ),
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
    opts=pulumi.ResourceOptions(
        delete_before_replace=True,
        depends_on=[golink_namespace, auth_secret, pvc],
    ),
)

pulumi.export("namespace", golink_namespace.metadata.name)
pulumi.export("deployment", deployment.metadata.name)
pulumi.export("pvc", pvc.metadata.name)
pulumi.export("sqlitedb_path", "/home/nonroot/golink.db")
pulumi.export("tailscale_config_dir", "/home/nonroot/tsnet-golink")
pulumi.export("tailnet_key_id", tailnet_key.id)
pulumi.export("tailnet_key_expires_at", tailnet_key.expires_at)
pulumi.export("tailnet_key_tags", tailnet_key.tags)
