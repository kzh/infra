import base64
import json

import pulumi_kubernetes as k8s

import pulumi

config = pulumi.Config()

HERMES_SOURCE_COMMIT = "d5775fe98870f4d7ba7cf322bd05283533079aa3"
CODEX_CLI_VERSION = "0.130.0"
DEFAULT_IMAGE = (
    "ghcr.io/kzh/hermes-agent"
    "@sha256:ef20eeb6d938f3e1e384d021c87f16dfa9d2c2e1fcb7d930b6d07bbdff01505b"
)


def bool_config(name: str, default: bool) -> bool:
    value = config.get_bool(name)
    return default if value is None else value


namespace_name = config.get("namespace") or "hermes"
image = config.get("image") or DEFAULT_IMAGE
storage_size = config.get("storage_size") or "20Gi"
storage_class_name = config.get("storage_class")
dashboard_enabled = bool_config("dashboard_enabled", True)
dashboard_ingress_enabled = bool_config("dashboard_ingress_enabled", False)
dashboard_host = config.get("dashboard_host") or (
    "0.0.0.0" if dashboard_ingress_enabled else "127.0.0.1"
)
dashboard_ingress_host = config.get("dashboard_ingress_host") or "hermes"
dashboard_port = config.get_int("dashboard_port") or 9119
gateway_port = config.get_int("gateway_port") or 8642
hermes_uid = config.get_int("uid") or 10000
hermes_gid = config.get_int("gid") or 10000
ghcr_username = None
ghcr_token = None
if image.startswith("ghcr.io/kzh/"):
    ghcr_username = config.require("ghcr_username")
    ghcr_token = config.require_secret("ghcr_token")

selector_labels = {
    "app": "hermes",
}
labels = {
    **selector_labels,
    "app.kubernetes.io/name": "hermes-agent",
    "app.kubernetes.io/part-of": "hermes",
}

hermes_namespace = k8s.core.v1.Namespace(
    "hermes-namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=namespace_name,
        labels=labels,
    ),
)

pvc_spec_args = {
    "access_modes": ["ReadWriteOnce"],
    "resources": k8s.core.v1.ResourceRequirementsArgs(
        requests={"storage": storage_size},
    ),
}
if storage_class_name:
    pvc_spec_args["storage_class_name"] = storage_class_name

pvc = k8s.core.v1.PersistentVolumeClaim(
    "hermes-data",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="hermes-data",
        namespace=hermes_namespace.metadata.name,
        labels=labels,
    ),
    spec=k8s.core.v1.PersistentVolumeClaimSpecArgs(**pvc_spec_args),
)

ghcr_pull_secret = None
image_pull_secrets = None
if ghcr_username is not None and ghcr_token is not None:
    docker_config_json = ghcr_token.apply(
        lambda token: json.dumps(
            {
                "auths": {
                    "ghcr.io": {
                        "username": ghcr_username,
                        "password": token,
                        "auth": base64.b64encode(
                            f"{ghcr_username}:{token}".encode()
                        ).decode(),
                    }
                }
            },
            separators=(",", ":"),
        )
    )

    ghcr_pull_secret = k8s.core.v1.Secret(
        "hermes-ghcr-pull-secret",
        metadata=k8s.meta.v1.ObjectMetaArgs(
            name="hermes-ghcr-pull",
            namespace=hermes_namespace.metadata.name,
            labels=labels,
        ),
        string_data={
            ".dockerconfigjson": docker_config_json,
        },
        type="kubernetes.io/dockerconfigjson",
    )
    image_pull_secrets = [
        k8s.core.v1.LocalObjectReferenceArgs(name=ghcr_pull_secret.metadata.name)
    ]

common_env = [
    k8s.core.v1.EnvVarArgs(name="HOME", value="/opt/data"),
    k8s.core.v1.EnvVarArgs(name="HERMES_HOME", value="/opt/data"),
    k8s.core.v1.EnvVarArgs(name="HERMES_HEADLESS", value="1"),
    k8s.core.v1.EnvVarArgs(name="CODEX_HOME", value="/opt/data/.codex"),
    k8s.core.v1.EnvVarArgs(name="HERMES_UID", value=str(hermes_uid)),
    k8s.core.v1.EnvVarArgs(name="HERMES_GID", value=str(hermes_gid)),
]

containers = [
    k8s.core.v1.ContainerArgs(
        name="gateway",
        image=image,
        image_pull_policy="IfNotPresent",
        args=[
            "bash",
            "-lc",
            "exec /opt/hermes/.venv/bin/hermes gateway run",
        ],
        env=common_env,
        ports=[
            k8s.core.v1.ContainerPortArgs(
                name="gateway",
                container_port=gateway_port,
            ),
        ],
        resources=k8s.core.v1.ResourceRequirementsArgs(
            requests={
                "cpu": "500m",
                "memory": "1Gi",
            },
            limits={
                "cpu": "2",
                "memory": "4Gi",
            },
        ),
        volume_mounts=[
            k8s.core.v1.VolumeMountArgs(
                name="data",
                mount_path="/opt/data",
            ),
        ],
    ),
]

if dashboard_enabled:
    dashboard_args = [
        "dashboard",
        "--host",
        dashboard_host,
        "--port",
        str(dashboard_port),
        "--no-open",
    ]
    if dashboard_ingress_enabled:
        dashboard_args.append("--insecure")

    containers.append(
        k8s.core.v1.ContainerArgs(
            name="dashboard",
            image=image,
            image_pull_policy="IfNotPresent",
            args=dashboard_args,
            env=common_env,
            ports=[
                k8s.core.v1.ContainerPortArgs(
                    name="dashboard",
                    container_port=dashboard_port,
                ),
            ],
            resources=k8s.core.v1.ResourceRequirementsArgs(
                requests={
                    "cpu": "100m",
                    "memory": "256Mi",
                },
                limits={
                    "cpu": "500m",
                    "memory": "1Gi",
                },
            ),
            volume_mounts=[
                k8s.core.v1.VolumeMountArgs(
                    name="data",
                    mount_path="/opt/data",
                ),
            ],
        )
    )

deployment = k8s.apps.v1.Deployment(
    "hermes-deployment",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="hermes",
        namespace=hermes_namespace.metadata.name,
        labels=labels,
    ),
    spec=k8s.apps.v1.DeploymentSpecArgs(
        replicas=1,
        strategy=k8s.apps.v1.DeploymentStrategyArgs(
            type="Recreate",
        ),
        selector=k8s.meta.v1.LabelSelectorArgs(
            match_labels=selector_labels,
        ),
        template=k8s.core.v1.PodTemplateSpecArgs(
            metadata=k8s.meta.v1.ObjectMetaArgs(
                labels=labels,
                annotations={
                    "hermes.k8s.kevin/source-commit": HERMES_SOURCE_COMMIT,
                    "hermes.k8s.kevin/codex-cli-version": CODEX_CLI_VERSION,
                },
            ),
            spec=k8s.core.v1.PodSpecArgs(
                automount_service_account_token=False,
                security_context=k8s.core.v1.PodSecurityContextArgs(
                    fs_group=hermes_gid,
                    fs_group_change_policy="OnRootMismatch",
                ),
                image_pull_secrets=image_pull_secrets,
                containers=containers,
                volumes=[
                    k8s.core.v1.VolumeArgs(
                        name="data",
                        persistent_volume_claim=(
                            k8s.core.v1.PersistentVolumeClaimVolumeSourceArgs(
                                claim_name=pvc.metadata.name,
                            )
                        ),
                    ),
                ],
            ),
        ),
    ),
    opts=pulumi.ResourceOptions(
        delete_before_replace=True,
        depends_on=[
            resource
            for resource in [
                hermes_namespace,
                pvc,
                ghcr_pull_secret,
            ]
            if resource is not None
        ],
    ),
)

dashboard_service = None
dashboard_ingress = None
if dashboard_enabled and dashboard_ingress_enabled:
    dashboard_service = k8s.core.v1.Service(
        "hermes-dashboard-service",
        metadata=k8s.meta.v1.ObjectMetaArgs(
            name="hermes-dashboard",
            namespace=hermes_namespace.metadata.name,
            labels=labels,
        ),
        spec=k8s.core.v1.ServiceSpecArgs(
            type="ClusterIP",
            selector=selector_labels,
            ports=[
                k8s.core.v1.ServicePortArgs(
                    name="http",
                    port=dashboard_port,
                    target_port=dashboard_port,
                ),
            ],
        ),
        opts=pulumi.ResourceOptions(depends_on=[deployment]),
    )

    dashboard_ingress = k8s.networking.v1.Ingress(
        "hermes-dashboard-ingress",
        metadata=k8s.meta.v1.ObjectMetaArgs(
            name="hermes-dashboard",
            namespace=hermes_namespace.metadata.name,
            labels=labels,
        ),
        spec=k8s.networking.v1.IngressSpecArgs(
            ingress_class_name="tailscale",
            rules=[
                k8s.networking.v1.IngressRuleArgs(
                    host=dashboard_ingress_host,
                    http=k8s.networking.v1.HTTPIngressRuleValueArgs(
                        paths=[
                            k8s.networking.v1.HTTPIngressPathArgs(
                                path="/",
                                path_type="Prefix",
                                backend=k8s.networking.v1.IngressBackendArgs(
                                    service=(
                                        k8s.networking.v1.IngressServiceBackendArgs(
                                            name=dashboard_service.metadata.name,
                                            port=(
                                                k8s.networking.v1.ServiceBackendPortArgs(
                                                    number=dashboard_port,
                                                )
                                            ),
                                        )
                                    ),
                                ),
                            ),
                        ],
                    ),
                ),
            ],
            tls=[
                k8s.networking.v1.IngressTLSArgs(
                    hosts=[dashboard_ingress_host],
                ),
            ],
        ),
        opts=pulumi.ResourceOptions(depends_on=[dashboard_service]),
    )

pulumi.export("namespace", hermes_namespace.metadata.name)
pulumi.export("deployment", deployment.metadata.name)
pulumi.export("pvc", pvc.metadata.name)
pulumi.export("image", image)
if ghcr_pull_secret is not None:
    pulumi.export("image_pull_secret", ghcr_pull_secret.metadata.name)
pulumi.export("hermes_home", "/opt/data")
pulumi.export("codex_home", "/opt/data/.codex")
pulumi.export(
    "model_setup_command",
    f"kubectl -n {namespace_name} exec -it deploy/hermes -c dashboard -- hermes model",
)
pulumi.export(
    "codex_login_command",
    (
        f"kubectl -n {namespace_name} exec -it deploy/hermes -c gateway -- "
        "gosu hermes codex login --device-auth"
    ),
)
pulumi.export(
    "codex_login_status_command",
    (
        f"kubectl -n {namespace_name} exec -it deploy/hermes -c gateway -- "
        "gosu hermes codex login status"
    ),
)
pulumi.export(
    "codex_version_command",
    f"kubectl -n {namespace_name} exec -it deploy/hermes -c gateway -- gosu hermes codex --version",
)
pulumi.export(
    "provider_test_command",
    (
        f"kubectl -n {namespace_name} exec -it deploy/hermes -c dashboard -- "
        'hermes -z "Reply with OK if the provider works."'
    ),
)
if dashboard_enabled:
    pulumi.export(
        "dashboard_port_forward_command",
        f"kubectl -n {namespace_name} port-forward deploy/hermes {dashboard_port}:{dashboard_port}",
    )
if dashboard_ingress is not None:
    pulumi.export("dashboard_ingress", dashboard_ingress.metadata.name)
    pulumi.export("dashboard_ingress_host", dashboard_ingress_host)
    pulumi.export("dashboard_service", dashboard_service.metadata.name)
