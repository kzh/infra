import pulumi
import pulumi_kubernetes as k8s
import pulumi_postgresql as pg
import pulumi_random as random
from urllib.parse import quote


def bool_string(value: bool) -> str:
    return "true" if value else "false"


def build_postgres_url(values: list[object]) -> str:
    username, password, host, port = values
    return (
        f"postgresql://{quote(str(username), safe='')}:{quote(str(password), safe='')}@"
        f"{host}:{port}"
    )


def tailscale_ingress_opts(*deps: pulumi.Input[pulumi.Resource]):
    return pulumi.ResourceOptions(
        depends_on=list(deps),
        delete_before_replace=True,
        replace_on_changes=["spec"],
    )


config = pulumi.Config()

namespace_name = config.get("namespace") or "convexdb"
storage_class_name = config.get("storage_class_name") or "local-path"
storage_size = config.get("storage_size") or "10Gi"
instance_name = config.get("instance_name") or "convexdb"
postgres_stack_ref = config.get("postgres_stack_ref") or "kzh/postgresql/mx"
postgres_service_host = config.get("postgres_service_host")
postgres_db_name = config.get("postgres_db_name") or instance_name.replace("-", "_")
postgres_db_user = config.get("postgres_db_user") or instance_name.replace("-", "_")
postgres_sslmode = config.get("postgres_sslmode") or "disable"
postgres_ca_cert = config.require_secret("postgres_ca_cert")
configured_image_tag = config.get("image_tag")
image_tag = configured_image_tag or "6d7b35510d3501705b637964aab201d076893e72"
backend_image = config.get("backend_image") or f"ghcr.io/get-convex/convex-backend:{image_tag}"
dashboard_image = config.get("dashboard_image") or f"ghcr.io/get-convex/convex-dashboard:{image_tag}"
api_ingress_host = config.get("api_ingress_host") or "convexdb-api"
dashboard_ingress_host = config.get("dashboard_ingress_host") or "convexdb"
disable_metrics_endpoint = config.get_bool("disable_metrics_endpoint")
if disable_metrics_endpoint is None:
    disable_metrics_endpoint = True
load_monaco_internally = config.get_bool("load_monaco_internally")
if load_monaco_internally is None:
    load_monaco_internally = False

api_url = f"https://{api_ingress_host}"
site_url = f"{api_url}/http"
dashboard_url = f"https://{dashboard_ingress_host}"

labels = {
    "app": "convexdb",
}
backend_labels = {
    **labels,
    "component": "backend",
}
dashboard_labels = {
    **labels,
    "component": "dashboard",
}

namespace = k8s.core.v1.Namespace(
    "convexdb-namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=namespace_name,
        labels=labels,
    ),
)

postgres_stack = pulumi.StackReference(postgres_stack_ref)
postgres_service_host = postgres_service_host or postgres_stack.require_output("rw_service_fqdn")
postgres_provider_host = pulumi.Output.all(
    postgres_stack.require_output("ts_hostname"),
    postgres_stack.require_output("host"),
).apply(lambda values: values[0] or values[1])
postgres_port = postgres_stack.require_output("port").apply(lambda value: int(value) if value else 5432)

postgres_admin_provider = pg.Provider(
    "convexdb-postgres-admin",
    host=postgres_provider_host,
    port=postgres_port,
    username=postgres_stack.require_output("username"),
    password=postgres_stack.require_output("password"),
    database="postgres",
    sslmode=postgres_sslmode,
)

postgres_db_password = random.RandomPassword(
    "convexdb-postgres-password",
    length=32,
    special=False,
)

postgres_role = pg.Role(
    "convexdb-postgres-role",
    name=postgres_db_user,
    login=True,
    password=postgres_db_password.result,
    opts=pulumi.ResourceOptions(provider=postgres_admin_provider),
)

postgres_database = pg.Database(
    "convexdb-postgres-database",
    name=postgres_db_name,
    owner=postgres_role.name,
    opts=pulumi.ResourceOptions(provider=postgres_admin_provider, depends_on=[postgres_role]),
)

postgres_ca_secret = k8s.core.v1.Secret(
    "convexdb-postgres-ca-secret",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="convexdb-postgres-ca",
        namespace=namespace.metadata.name,
        labels=labels,
    ),
    string_data={
        "ca.crt": postgres_ca_cert,
    },
    type="Opaque",
    opts=pulumi.ResourceOptions(depends_on=[namespace]),
)

postgres_url = pulumi.Output.all(
    postgres_role.name,
    postgres_db_password.result,
    postgres_service_host,
    postgres_port,
).apply(build_postgres_url)

postgres_secret = k8s.core.v1.Secret(
    "convexdb-postgres-secret",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="convexdb-postgres",
        namespace=namespace.metadata.name,
        labels=labels,
    ),
    string_data={
        "POSTGRES_URL": pulumi.Output.secret(postgres_url),
    },
    type="Opaque",
    opts=pulumi.ResourceOptions(depends_on=[namespace, postgres_database]),
)

storage = k8s.core.v1.PersistentVolumeClaim(
    "convexdb-storage",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="convexdb-storage",
        namespace=namespace.metadata.name,
        labels=labels,
    ),
    spec=k8s.core.v1.PersistentVolumeClaimSpecArgs(
        access_modes=["ReadWriteOnce"],
        storage_class_name=storage_class_name,
        resources=k8s.core.v1.ResourceRequirementsArgs(
            requests={"storage": storage_size},
        ),
    ),
    opts=pulumi.ResourceOptions(depends_on=[namespace]),
)

backend_env = [
    k8s.core.v1.EnvVarArgs(
        name="CONVEX_CLOUD_ORIGIN",
        value=api_url,
    ),
    k8s.core.v1.EnvVarArgs(
        name="CONVEX_SITE_ORIGIN",
        value=site_url,
    ),
    k8s.core.v1.EnvVarArgs(
        name="INSTANCE_NAME",
        value=instance_name,
    ),
    k8s.core.v1.EnvVarArgs(
        name="DISABLE_METRICS_ENDPOINT",
        value=bool_string(disable_metrics_endpoint),
    ),
    k8s.core.v1.EnvVarArgs(
        name="PG_CA_FILE",
        value="/convex/certs/ca.crt",
    ),
    k8s.core.v1.EnvVarArgs(
        name="POSTGRES_URL",
        value_from=k8s.core.v1.EnvVarSourceArgs(
            secret_key_ref=k8s.core.v1.SecretKeySelectorArgs(
                name=postgres_secret.metadata.name,
                key="POSTGRES_URL",
            ),
        ),
    ),
]

dashboard_env = [
    k8s.core.v1.EnvVarArgs(
        name="NEXT_PUBLIC_DEPLOYMENT_URL",
        value=api_url,
    ),
]
if load_monaco_internally:
    dashboard_env.append(
        k8s.core.v1.EnvVarArgs(
            name="NEXT_PUBLIC_LOAD_MONACO_INTERNALLY",
            value="true",
        ),
    )

backend_deployment = k8s.apps.v1.Deployment(
    "convexdb-backend",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="convexdb-backend",
        namespace=namespace.metadata.name,
        labels=backend_labels,
    ),
    spec=k8s.apps.v1.DeploymentSpecArgs(
        replicas=1,
        strategy=k8s.apps.v1.DeploymentStrategyArgs(
            type="Recreate",
        ),
        selector=k8s.meta.v1.LabelSelectorArgs(
            match_labels=backend_labels,
        ),
        template=k8s.core.v1.PodTemplateSpecArgs(
            metadata=k8s.meta.v1.ObjectMetaArgs(
                labels=backend_labels,
            ),
            spec=k8s.core.v1.PodSpecArgs(
                termination_grace_period_seconds=10,
                containers=[
                    k8s.core.v1.ContainerArgs(
                        name="backend",
                        image=backend_image,
                        image_pull_policy="IfNotPresent",
                        env=backend_env,
                        ports=[
                            k8s.core.v1.ContainerPortArgs(
                                name="api",
                                container_port=3210,
                            ),
                            k8s.core.v1.ContainerPortArgs(
                                name="site",
                                container_port=3211,
                            ),
                        ],
                        readiness_probe=k8s.core.v1.ProbeArgs(
                            http_get=k8s.core.v1.HTTPGetActionArgs(
                                path="/version",
                                port=3210,
                            ),
                            initial_delay_seconds=5,
                            period_seconds=5,
                        ),
                        liveness_probe=k8s.core.v1.ProbeArgs(
                            http_get=k8s.core.v1.HTTPGetActionArgs(
                                path="/version",
                                port=3210,
                            ),
                            initial_delay_seconds=10,
                            period_seconds=10,
                            failure_threshold=6,
                        ),
                        startup_probe=k8s.core.v1.ProbeArgs(
                            http_get=k8s.core.v1.HTTPGetActionArgs(
                                path="/version",
                                port=3210,
                            ),
                            period_seconds=5,
                            failure_threshold=24,
                        ),
                        volume_mounts=[
                            k8s.core.v1.VolumeMountArgs(
                                name="data",
                                mount_path="/convex/data",
                            ),
                            k8s.core.v1.VolumeMountArgs(
                                name="postgres-ca",
                                mount_path="/convex/certs",
                                read_only=True,
                            ),
                        ],
                    ),
                ],
                volumes=[
                    k8s.core.v1.VolumeArgs(
                        name="data",
                        persistent_volume_claim=k8s.core.v1.PersistentVolumeClaimVolumeSourceArgs(
                            claim_name=storage.metadata.name,
                        ),
                    ),
                    k8s.core.v1.VolumeArgs(
                        name="postgres-ca",
                        secret=k8s.core.v1.SecretVolumeSourceArgs(
                            secret_name=postgres_ca_secret.metadata.name,
                            items=[
                                k8s.core.v1.KeyToPathArgs(
                                    key="ca.crt",
                                    path="ca.crt",
                                ),
                            ],
                        ),
                    ),
                ],
            ),
        ),
    ),
    opts=pulumi.ResourceOptions(depends_on=[storage, postgres_secret, postgres_ca_secret]),
)

backend_service = k8s.core.v1.Service(
    "convexdb-backend-service",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="convexdb-backend",
        namespace=namespace.metadata.name,
        labels=backend_labels,
    ),
    spec=k8s.core.v1.ServiceSpecArgs(
        type="ClusterIP",
        selector=backend_labels,
        ports=[
            k8s.core.v1.ServicePortArgs(
                name="api",
                port=3210,
                target_port=3210,
            ),
            k8s.core.v1.ServicePortArgs(
                name="site",
                port=3211,
                target_port=3211,
            ),
        ],
    ),
    opts=pulumi.ResourceOptions(depends_on=[backend_deployment]),
)

dashboard_deployment = k8s.apps.v1.Deployment(
    "convexdb-dashboard",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="convexdb-dashboard",
        namespace=namespace.metadata.name,
        labels=dashboard_labels,
    ),
    spec=k8s.apps.v1.DeploymentSpecArgs(
        replicas=1,
        selector=k8s.meta.v1.LabelSelectorArgs(
            match_labels=dashboard_labels,
        ),
        template=k8s.core.v1.PodTemplateSpecArgs(
            metadata=k8s.meta.v1.ObjectMetaArgs(
                labels=dashboard_labels,
            ),
            spec=k8s.core.v1.PodSpecArgs(
                termination_grace_period_seconds=10,
                containers=[
                    k8s.core.v1.ContainerArgs(
                        name="dashboard",
                        image=dashboard_image,
                        image_pull_policy="IfNotPresent",
                        env=dashboard_env,
                        ports=[
                            k8s.core.v1.ContainerPortArgs(
                                name="http",
                                container_port=6791,
                            ),
                        ],
                        readiness_probe=k8s.core.v1.ProbeArgs(
                            http_get=k8s.core.v1.HTTPGetActionArgs(
                                path="/",
                                port=6791,
                            ),
                            initial_delay_seconds=5,
                            period_seconds=5,
                        ),
                        liveness_probe=k8s.core.v1.ProbeArgs(
                            http_get=k8s.core.v1.HTTPGetActionArgs(
                                path="/",
                                port=6791,
                            ),
                            initial_delay_seconds=10,
                            period_seconds=10,
                            failure_threshold=6,
                        ),
                    ),
                ],
            ),
        ),
    ),
    opts=pulumi.ResourceOptions(depends_on=[backend_service]),
)

dashboard_service = k8s.core.v1.Service(
    "convexdb-dashboard-service",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="convexdb-dashboard",
        namespace=namespace.metadata.name,
        labels=dashboard_labels,
    ),
    spec=k8s.core.v1.ServiceSpecArgs(
        type="ClusterIP",
        selector=dashboard_labels,
        ports=[
            k8s.core.v1.ServicePortArgs(
                name="http",
                port=6791,
                target_port=6791,
            ),
        ],
    ),
    opts=pulumi.ResourceOptions(depends_on=[dashboard_deployment]),
)

api_ingress = k8s.networking.v1.Ingress(
    "convexdb-api-ingress",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="convexdb-api",
        namespace=namespace.metadata.name,
        labels=backend_labels,
    ),
    spec=k8s.networking.v1.IngressSpecArgs(
        ingress_class_name="tailscale",
        rules=[
            k8s.networking.v1.IngressRuleArgs(
                host=api_ingress_host,
                http=k8s.networking.v1.HTTPIngressRuleValueArgs(
                    paths=[
                        k8s.networking.v1.HTTPIngressPathArgs(
                            path="/",
                            path_type="Prefix",
                            backend=k8s.networking.v1.IngressBackendArgs(
                                service=k8s.networking.v1.IngressServiceBackendArgs(
                                    name=backend_service.metadata.name,
                                    port=k8s.networking.v1.ServiceBackendPortArgs(
                                        name="api",
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
                hosts=[api_ingress_host],
            ),
        ],
    ),
    opts=tailscale_ingress_opts(backend_service),
)

dashboard_ingress = k8s.networking.v1.Ingress(
    "convexdb-dashboard-ingress",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="convexdb-dashboard",
        namespace=namespace.metadata.name,
        labels=dashboard_labels,
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
                                service=k8s.networking.v1.IngressServiceBackendArgs(
                                    name=dashboard_service.metadata.name,
                                    port=k8s.networking.v1.ServiceBackendPortArgs(
                                        name="http",
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
                hosts=[dashboard_ingress_host],
            ),
        ],
    ),
    opts=tailscale_ingress_opts(dashboard_service),
)

pulumi.export("namespace", namespace.metadata.name)
pulumi.export("storage_class_name", storage_class_name)
pulumi.export("storage_size", storage_size)
pulumi.export("image_tag", configured_image_tag)
pulumi.export("backend_image", backend_image)
pulumi.export("dashboard_image", dashboard_image)
pulumi.export("postgres_stack_ref", postgres_stack_ref)
pulumi.export("postgres_db_name", postgres_db_name)
pulumi.export("postgres_db_user", postgres_role.name)
pulumi.export("api_url", api_url)
pulumi.export("dashboard_url", dashboard_url)
pulumi.export("backend_service", backend_service.metadata.name)
pulumi.export("dashboard_service", dashboard_service.metadata.name)
pulumi.export("pvc", storage.metadata.name)
pulumi.export(
    "admin_key_command",
    pulumi.Output.format(
        "kubectl exec -n {} deploy/{} -- /convex/generate_admin_key.sh",
        namespace.metadata.name,
        backend_deployment.metadata.name,
    ),
)
pulumi.export("api_ingress", api_ingress.metadata.name)
pulumi.export("dashboard_ingress", dashboard_ingress.metadata.name)
