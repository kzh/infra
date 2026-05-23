import pulumi_kubernetes as k8s
import pulumi_random as random

import pulumi

APP_NAME = "cliproxyapi"
APP_PORT = 8317
IMAGE = "eceasy/cli-proxy-api:v7.1.15"
SECRET_NAME = "cliproxyapi-seed"
AUTH_PVC_NAME = "cliproxyapi-auth"
CONFIG_KEY = "config.yaml"
CODEX_AUTH_KEY = "codex-auth.json"

config = pulumi.Config()

namespace_name = config.get("namespace") or APP_NAME
hostname = config.get("hostname") or APP_NAME
image = config.get("image") or IMAGE
storage_size = config.get("storageSize") or "1Gi"
storage_class_name = config.get("storageClassName")
codex_auth_json = config.require_secret("codexAuthJson")

labels = {
    "app": APP_NAME,
    "app.kubernetes.io/name": APP_NAME,
    "app.kubernetes.io/part-of": APP_NAME,
}


api_key_suffix = random.RandomPassword(
    "cliproxyapi-api-key-suffix",
    length=40,
    special=False,
)
api_key = pulumi.Output.concat("sk-", api_key_suffix.result)

management_key_suffix = random.RandomPassword(
    "cliproxyapi-management-key-suffix",
    length=48,
    special=False,
)
management_key = management_key_suffix.result

proxy_config = pulumi.Output.concat(
    """
host: ""
port: 8317
tls:
  enable: false
  cert: ""
  key: ""
remote-management:
  allow-remote: true
  secret-key: """,
    management_key_suffix.bcrypt_hash,
    """
  disable-control-panel: false
  panel-github-repository: "https://github.com/router-for-me/Cli-Proxy-API-Management-Center"
auth-dir: "/root/.cli-proxy-api"
api-keys:
  - """,
    api_key,
    """
debug: false
pprof:
  enable: false
  addr: "127.0.0.1:8316"
logging-to-file: false
usage-statistics-enabled: false
redis-usage-queue-retention-seconds: 60
proxy-url: ""
force-model-prefix: false
passthrough-headers: false
request-retry: 3
max-retry-credentials: 0
max-retry-interval: 30
disable-cooling: false
quota-exceeded:
  switch-project: true
  switch-preview-model: true
  antigravity-credits: true
routing:
  strategy: "round-robin"
ws-auth: false
enable-gemini-cli-endpoint: false
nonstream-keepalive-interval: 0
""",
)

namespace = k8s.core.v1.Namespace(
    "cliproxyapi-namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=namespace_name,
        labels=labels,
    ),
)

seed_secret = k8s.core.v1.Secret(
    "cliproxyapi-seed",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=SECRET_NAME,
        namespace=namespace.metadata.name,
        labels=labels,
    ),
    type="Opaque",
    string_data={
        CONFIG_KEY: proxy_config,
        CODEX_AUTH_KEY: codex_auth_json,
    },
    opts=pulumi.ResourceOptions(
        delete_before_replace=True,
        depends_on=[namespace],
    ),
)

auth_storage_spec_args = {
    "access_modes": ["ReadWriteOnce"],
    "resources": k8s.core.v1.ResourceRequirementsArgs(
        requests={"storage": storage_size},
    ),
}
if storage_class_name:
    auth_storage_spec_args["storage_class_name"] = storage_class_name

auth_storage = k8s.core.v1.PersistentVolumeClaim(
    "cliproxyapi-auth",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=AUTH_PVC_NAME,
        namespace=namespace.metadata.name,
        labels=labels,
    ),
    spec=k8s.core.v1.PersistentVolumeClaimSpecArgs(**auth_storage_spec_args),
    opts=pulumi.ResourceOptions(depends_on=[namespace]),
)

deployment = k8s.apps.v1.Deployment(
    "cliproxyapi",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=APP_NAME,
        namespace=namespace.metadata.name,
        labels=labels,
        annotations={
            "infra.kzh.dev/config-secret": SECRET_NAME,
        },
    ),
    spec=k8s.apps.v1.DeploymentSpecArgs(
        replicas=1,
        strategy=k8s.apps.v1.DeploymentStrategyArgs(type="Recreate"),
        selector=k8s.meta.v1.LabelSelectorArgs(match_labels={"app": APP_NAME}),
        template=k8s.core.v1.PodTemplateSpecArgs(
            metadata=k8s.meta.v1.ObjectMetaArgs(
                labels=labels,
                annotations={
                    "infra.kzh.dev/config-generation": "management-ui-enabled-v1",
                },
            ),
            spec=k8s.core.v1.PodSpecArgs(
                init_containers=[
                    k8s.core.v1.ContainerArgs(
                        name="seed-codex-auth",
                        image="busybox:1.37",
                        command=["sh", "-ec"],
                        args=[
                            (
                                "for f in /auth-seed/*; do "
                                '[ -f "$f" ] || continue; '
                                'name="$(basename "$f")"; '
                                f'[ "$name" = "{CONFIG_KEY}" ] && continue; '
                                'dest="/auth/$name"; '
                                '[ -f "$dest" ] || cp "$f" "$dest"; '
                                "done; "
                                "chmod 600 /auth/* || true"
                            )
                        ],
                        volume_mounts=[
                            k8s.core.v1.VolumeMountArgs(
                                name="auth-seed",
                                mount_path="/auth-seed",
                                read_only=True,
                            ),
                            k8s.core.v1.VolumeMountArgs(
                                name="auth",
                                mount_path="/auth",
                            ),
                        ],
                    )
                ],
                containers=[
                    k8s.core.v1.ContainerArgs(
                        name=APP_NAME,
                        image=image,
                        image_pull_policy="IfNotPresent",
                        command=["/CLIProxyAPI/CLIProxyAPI"],
                        args=["-config", "/CLIProxyAPI/config.yaml"],
                        ports=[
                            k8s.core.v1.ContainerPortArgs(
                                name="http",
                                container_port=APP_PORT,
                            )
                        ],
                        readiness_probe=k8s.core.v1.ProbeArgs(
                            tcp_socket=k8s.core.v1.TCPSocketActionArgs(
                                port="http",
                            ),
                            initial_delay_seconds=5,
                            period_seconds=10,
                        ),
                        liveness_probe=k8s.core.v1.ProbeArgs(
                            tcp_socket=k8s.core.v1.TCPSocketActionArgs(
                                port="http",
                            ),
                            initial_delay_seconds=15,
                            period_seconds=20,
                        ),
                        resources=k8s.core.v1.ResourceRequirementsArgs(
                            requests={
                                "cpu": "100m",
                                "memory": "128Mi",
                            },
                            limits={
                                "cpu": "1",
                                "memory": "512Mi",
                            },
                        ),
                        volume_mounts=[
                            k8s.core.v1.VolumeMountArgs(
                                name="config",
                                mount_path="/CLIProxyAPI/config.yaml",
                                sub_path=CONFIG_KEY,
                                read_only=True,
                            ),
                            k8s.core.v1.VolumeMountArgs(
                                name="auth",
                                mount_path="/root/.cli-proxy-api",
                            ),
                        ],
                    )
                ],
                volumes=[
                    k8s.core.v1.VolumeArgs(
                        name="config",
                        secret=k8s.core.v1.SecretVolumeSourceArgs(
                            secret_name=SECRET_NAME,
                            items=[
                                k8s.core.v1.KeyToPathArgs(
                                    key=CONFIG_KEY,
                                    path=CONFIG_KEY,
                                )
                            ],
                        ),
                    ),
                    k8s.core.v1.VolumeArgs(
                        name="auth-seed",
                        secret=k8s.core.v1.SecretVolumeSourceArgs(
                            secret_name=SECRET_NAME,
                        ),
                    ),
                    k8s.core.v1.VolumeArgs(
                        name="auth",
                        persistent_volume_claim=k8s.core.v1.PersistentVolumeClaimVolumeSourceArgs(
                            claim_name=auth_storage.metadata.name,
                        ),
                    ),
                ],
            ),
        ),
    ),
    opts=pulumi.ResourceOptions(depends_on=[auth_storage]),
)

service = k8s.core.v1.Service(
    "cliproxyapi",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=APP_NAME,
        namespace=namespace.metadata.name,
        labels=labels,
    ),
    spec=k8s.core.v1.ServiceSpecArgs(
        type="ClusterIP",
        selector={"app": APP_NAME},
        ports=[
            k8s.core.v1.ServicePortArgs(
                name="http",
                port=APP_PORT,
                target_port="http",
            )
        ],
    ),
    opts=pulumi.ResourceOptions(depends_on=[deployment]),
)

ingress = k8s.networking.v1.Ingress(
    "cliproxyapi-ingress",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=APP_NAME,
        namespace=namespace.metadata.name,
        labels=labels,
        annotations={
            "pulumi.com/skipAwait": "true",
            "pulumi.com/patchForce": "true",
        },
    ),
    spec=k8s.networking.v1.IngressSpecArgs(
        ingress_class_name="tailscale",
        rules=[
            k8s.networking.v1.IngressRuleArgs(
                host=hostname,
                http=k8s.networking.v1.HTTPIngressRuleValueArgs(
                    paths=[
                        k8s.networking.v1.HTTPIngressPathArgs(
                            path="/",
                            path_type="Prefix",
                            backend=k8s.networking.v1.IngressBackendArgs(
                                service=k8s.networking.v1.IngressServiceBackendArgs(
                                    name=service.metadata.name,
                                    port=k8s.networking.v1.ServiceBackendPortArgs(
                                        number=APP_PORT,
                                    ),
                                ),
                            ),
                        )
                    ],
                ),
            )
        ],
        tls=[
            k8s.networking.v1.IngressTLSArgs(
                hosts=[hostname],
            )
        ],
    ),
    opts=pulumi.ResourceOptions(depends_on=[service]),
)

pulumi.export("namespace", namespace.metadata.name)
pulumi.export("service", service.metadata.name)
pulumi.export("port", APP_PORT)
pulumi.export("hostname", hostname)
pulumi.export("url", f"https://{hostname}")
pulumi.export(
    "openaiBaseUrl",
    f"http://{APP_NAME}.{namespace_name}.svc.cluster.local:{APP_PORT}/v1",
)
pulumi.export("ingress", ingress.metadata.name)
pulumi.export("image", image)
pulumi.export("authPvc", auth_storage.metadata.name)
pulumi.export("secretName", seed_secret.metadata.name)
pulumi.export("apiKey", api_key)
pulumi.export("managementKey", management_key)
