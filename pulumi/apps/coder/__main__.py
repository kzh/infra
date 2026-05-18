from urllib.parse import quote

import pulumi_kubernetes as k8s
import pulumi_postgresql as pg
from infra_helpers.postgres import PostgresStack

import pulumi

config = pulumi.Config()


def get_bool_config(name: str, default: bool) -> bool:
    value = config.get_bool(name)
    return default if value is None else value


def get_string_map_config(name: str) -> dict[str, str]:
    raw_value = config.get_object(name)
    if not isinstance(raw_value, dict):
        return {}
    return {str(key): str(value) for key, value in raw_value.items()}


namespace_name = config.get("namespace") or "coder"
postgres_stack = config.require("postgres_stack")
db_name = config.get("db_name") or "coder"
create_database = get_bool_config("create_database", True)
postgres_admin_db = config.get("postgres_admin_db") or "postgres"
postgres_sslmode = config.get("postgres_sslmode") or "disable"

coder_chart_version = config.get("coder_chart_version") or "2.33.3"
service_type = config.get("service_type") or "ClusterIP"
workspace_namespace = config.get("workspace_namespace") or "coder-workspaces"
workspace_create_namespace = get_bool_config("workspace_create_namespace", True)
workspace_enable_perms = get_bool_config("workspace_enable_perms", True)
workspace_enable_deployments = get_bool_config("workspace_enable_deployments", True)

ingress_enabled = get_bool_config("ingress_enabled", True)
ingress_class_name = config.get("ingress_class_name") or "tailscale"
ingress_host = config.get("ingress_host") or "coder"
ingress_wildcard_host = config.get("ingress_wildcard_host") or ""
ingress_tls_enabled = get_bool_config("ingress_tls_enabled", False)
ingress_tls_secret_name = config.get("ingress_tls_secret_name") or ""
ingress_tls_wildcard_secret_name = config.get("ingress_tls_wildcard_secret_name") or ""

access_url = config.get("access_url")
if not access_url and ingress_enabled and ingress_host:
    access_url = f"https://{ingress_host}"

disable_default_github_auth = get_bool_config("disable_default_github_auth", True)
service_annotations = get_string_map_config("service_annotations")
ingress_annotations = get_string_map_config("ingress_annotations")

labels = {
    "app": "coder",
}


coder_namespace = k8s.core.v1.Namespace(
    "coder-namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=namespace_name,
        labels=labels,
    ),
)

postgres = PostgresStack(postgres_stack)
pg_host = postgres.rw_service_fqdn
pg_port = postgres.port.apply(lambda p: int(p) if p else 5432)
pg_username = postgres.username
pg_password = postgres.password

coder_database = None
if create_database:
    admin_provider = postgres.admin_provider(
        "coder-pg-admin",
        database=postgres_admin_db,
        sslmode=postgres_sslmode,
        host=postgres.admin_host,
        port=pg_port,
    )
    coder_database = pg.Database(
        "coder-database",
        name=db_name,
        opts=pulumi.ResourceOptions(provider=admin_provider),
    )


def build_connection_url(values: list[object]) -> str:
    username, password, host, port, database, sslmode = values
    return (
        f"postgres://{quote(str(username), safe='')}:{quote(str(password), safe='')}@"
        f"{host}:{port}/{quote(str(database), safe='')}?sslmode={sslmode}"
    )


pg_connection_url = pulumi.Output.all(
    pg_username,
    pg_password,
    pg_host,
    pg_port,
    pulumi.Output.from_input(db_name),
    pulumi.Output.from_input(postgres_sslmode),
).apply(build_connection_url)

coder_db_url_secret = k8s.core.v1.Secret(
    "coder-db-url",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="coder-db-url",
        namespace=coder_namespace.metadata.name,
        labels=labels,
    ),
    string_data={
        "url": pulumi.Output.secret(pg_connection_url),
    },
    type="Opaque",
)

coder_env: list[dict[str, object]] = [
    {
        "name": "CODER_PG_CONNECTION_URL",
        "valueFrom": {
            "secretKeyRef": {
                "name": coder_db_url_secret.metadata.name,
                "key": "url",
            },
        },
    },
]

if disable_default_github_auth:
    coder_env.append(
        {
            "name": "CODER_OAUTH2_GITHUB_DEFAULT_PROVIDER_ENABLE",
            "value": "false",
        }
    )

if access_url:
    coder_env.append(
        {
            "name": "CODER_ACCESS_URL",
            "value": access_url,
        }
    )

service_values: dict[str, object] = {
    "type": service_type,
}

if service_annotations:
    service_values["annotations"] = service_annotations

workspace_namespaces_values: list[dict[str, object]] = []
if (
    workspace_enable_perms
    and workspace_namespace
    and workspace_namespace != namespace_name
):
    workspace_namespaces_values.append(
        {
            "name": workspace_namespace,
            "workspacePerms": workspace_enable_perms,
            "enableDeployments": workspace_enable_deployments,
        }
    )

chart_values: dict[str, object] = {
    "coder": {
        "env": coder_env,
        "service": service_values,
        "serviceAccount": {
            # Keep workspace permissions in the control-plane namespace and optionally
            # grant them in additional namespaces via workspaceNamespaces.
            "workspacePerms": workspace_enable_perms,
            "enableDeployments": workspace_enable_deployments,
            "workspaceNamespaces": workspace_namespaces_values,
        },
        # Manage ingress separately to avoid Helm await issues with Tailscale ingress status.
        "ingress": {
            "enable": False,
        },
    }
}

if access_url:
    chart_values["coder"]["envUseClusterAccessURL"] = False

dependencies: list[pulumi.Resource] = [coder_namespace, coder_db_url_secret]
if coder_database:
    dependencies.append(coder_database)

if (
    workspace_create_namespace
    and workspace_enable_perms
    and workspace_namespace
    and workspace_namespace != namespace_name
):
    workspace_ns_resource = k8s.core.v1.Namespace(
        "coder-workspaces-namespace",
        metadata=k8s.meta.v1.ObjectMetaArgs(
            name=workspace_namespace,
            labels={"app": "coder-workspaces"},
        ),
    )
    dependencies.append(workspace_ns_resource)

coder_chart = k8s.helm.v4.Chart(
    "coder",
    chart="coder",
    version=coder_chart_version,
    namespace=coder_namespace.metadata.name,
    repository_opts=k8s.helm.v4.RepositoryOptsArgs(
        repo="https://helm.coder.com/v2",
    ),
    values=chart_values,
    opts=pulumi.ResourceOptions(depends_on=dependencies),
)

coder_ingress = None
if ingress_enabled:
    ingress_resource_annotations: dict[str, str] = {
        "pulumi.com/skipAwait": "true",
        "pulumi.com/patchForce": "true",
    }
    ingress_resource_annotations.update(ingress_annotations)

    if ingress_class_name == "tailscale":
        tailscale_host_label = ingress_host.split(".", 1)[0]
        ingress_spec = k8s.networking.v1.IngressSpecArgs(
            ingress_class_name=ingress_class_name,
            default_backend=k8s.networking.v1.IngressBackendArgs(
                service=k8s.networking.v1.IngressServiceBackendArgs(
                    name="coder",
                    port=k8s.networking.v1.ServiceBackendPortArgs(number=80),
                )
            ),
            tls=[
                k8s.networking.v1.IngressTLSArgs(
                    hosts=[tailscale_host_label],
                )
            ],
        )
    else:
        ingress_rules = [
            k8s.networking.v1.IngressRuleArgs(
                host=ingress_host,
                http=k8s.networking.v1.HTTPIngressRuleValueArgs(
                    paths=[
                        k8s.networking.v1.HTTPIngressPathArgs(
                            path="/",
                            path_type="Prefix",
                            backend=k8s.networking.v1.IngressBackendArgs(
                                service=k8s.networking.v1.IngressServiceBackendArgs(
                                    name="coder",
                                    port=k8s.networking.v1.ServiceBackendPortArgs(
                                        number=80
                                    ),
                                )
                            ),
                        )
                    ],
                ),
            )
        ]

        if ingress_wildcard_host:
            ingress_rules.append(
                k8s.networking.v1.IngressRuleArgs(
                    host=ingress_wildcard_host,
                    http=k8s.networking.v1.HTTPIngressRuleValueArgs(
                        paths=[
                            k8s.networking.v1.HTTPIngressPathArgs(
                                path="/",
                                path_type="Prefix",
                                backend=k8s.networking.v1.IngressBackendArgs(
                                    service=k8s.networking.v1.IngressServiceBackendArgs(
                                        name="coder",
                                        port=k8s.networking.v1.ServiceBackendPortArgs(
                                            number=80
                                        ),
                                    )
                                ),
                            )
                        ],
                    ),
                )
            )

        ingress_tls = []
        if ingress_tls_enabled and ingress_tls_secret_name:
            ingress_tls.append(
                k8s.networking.v1.IngressTLSArgs(
                    hosts=[ingress_host],
                    secret_name=ingress_tls_secret_name,
                )
            )
        if (
            ingress_tls_enabled
            and ingress_wildcard_host
            and ingress_tls_wildcard_secret_name
        ):
            ingress_tls.append(
                k8s.networking.v1.IngressTLSArgs(
                    hosts=[ingress_wildcard_host],
                    secret_name=ingress_tls_wildcard_secret_name,
                )
            )

        ingress_spec = k8s.networking.v1.IngressSpecArgs(
            ingress_class_name=ingress_class_name,
            rules=ingress_rules,
            tls=ingress_tls,
        )

    coder_ingress = k8s.networking.v1.Ingress(
        "coder-ingress",
        metadata=k8s.meta.v1.ObjectMetaArgs(
            name="coder",
            namespace=coder_namespace.metadata.name,
            labels=labels,
            annotations=ingress_resource_annotations,
        ),
        spec=ingress_spec,
        opts=pulumi.ResourceOptions(depends_on=[coder_chart]),
    )

pulumi.export("namespace", coder_namespace.metadata.name)
pulumi.export("coder_service_type", service_type)
pulumi.export("ingress_enabled", ingress_enabled)
pulumi.export("ingress_host", ingress_host)
pulumi.export("access_url", access_url)
pulumi.export("db_name", db_name)
pulumi.export("db_secret_name", coder_db_url_secret.metadata.name)
pulumi.export("helm_chart_version", coder_chart_version)
pulumi.export("helm_release", coder_chart.urn)
pulumi.export("workspace_namespace", workspace_namespace)
pulumi.export(
    "ingress_resource",
    coder_ingress.metadata.name if coder_ingress else pulumi.Output.from_input(""),
)
