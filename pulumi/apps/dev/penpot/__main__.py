import pulumi
import pulumi_kubernetes as k8s
import pulumi_postgresql as pg

config = pulumi.Config()

namespace_name = config.require("namespace")
postgres_stack = config.require("postgres_stack")
db_name = config.get("db_name") or "penpot"
ingress_host = config.get("ingress_host") or "penpot"
public_uri = config.get("public_uri") or f"https://{ingress_host}"
create_database = config.get_bool("create_database")
postgres_admin_db = config.get("postgres_admin_db") or "postgres"
postgres_sslmode = config.get("postgres_sslmode") or "disable"

penpot_chart_version = config.get("penpot_chart_version") or "0.35.0"
valkey_chart_version = config.get("valkey_chart_version") or "0.9.3"
valkey_service_name = config.get("valkey_service_name") or "penpot-valkey"
valkey_port = config.get_int("valkey_port") or 6379
valkey_persistence_size = config.get("valkey_persistence_size") or "8Gi"
valkey_storage_class = config.get("valkey_storage_class")
api_secret_key = config.get_secret("api_secret_key")
mcp_enabled = config.get_bool("mcp_enabled")
mcp_server_image = config.require("mcp_server_image")
mcp_plugin_image = config.require("mcp_plugin_image")
mcp_http_host = config.get("mcp_http_host") or "penpot-mcp"
mcp_ws_host = config.get("mcp_ws_host") or "penpot-mcp-ws"
mcp_plugin_host = config.get("mcp_plugin_host") or "penpot-mcp-plugin"

labels = {
    "app": "penpot",
}

penpot_namespace = k8s.core.v1.Namespace(
    "penpot-namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=namespace_name,
        labels=labels,
    ),
)

pgref = pulumi.StackReference(postgres_stack)
pg_namespace = pgref.get_output("k8s_namespace")
pg_host = pg_namespace.apply(lambda ns: f"postgresql-cluster-rw.{ns}.svc.cluster.local")
pg_port = pgref.get_output("port").apply(lambda p: int(p) if p else 5432)
pg_username = pgref.get_output("username")
pg_password = pgref.get_output("password")
pg_ts_hostname = pgref.get_output("ts_hostname")
pg_provider_host = pulumi.Output.all(pg_ts_hostname, pgref.get_output("host")).apply(
    lambda t: t[0] or t[1]
)

valkey_host = f"{valkey_service_name}.{namespace_name}.svc.cluster.local"

valkey_data_storage = {
    "enabled": True,
    "requestedSize": valkey_persistence_size,
}
if valkey_storage_class:
    valkey_data_storage["className"] = valkey_storage_class

valkey_chart = k8s.helm.v4.Chart(
    "penpot-valkey",
    chart="valkey",
    version=valkey_chart_version,
    namespace=penpot_namespace.metadata.name,
    repository_opts=k8s.helm.v4.RepositoryOptsArgs(
        repo="https://valkey.io/valkey-helm/",
    ),
    values={
        "fullnameOverride": valkey_service_name,
        "auth": {
            "enabled": False,
        },
        "service": {
            "port": valkey_port,
        },
        "dataStorage": valkey_data_storage,
    },
    opts=pulumi.ResourceOptions(depends_on=[penpot_namespace]),
)

if create_database is None:
    create_database = True

penpot_database = None
if create_database:
    admin_provider = pg.Provider(
        "penpot-pg-admin",
        host=pg_provider_host,
        port=pg_port,
        username=pg_username,
        password=pg_password,
        database=postgres_admin_db,
        sslmode=postgres_sslmode,
    )
    penpot_database = pg.Database(
        "penpot-database",
        name=db_name,
        opts=pulumi.ResourceOptions(provider=admin_provider),
    )

penpot_config = {
    "publicUri": public_uri,
    "postgresql": {
        "host": pg_host,
        "port": pg_port,
        "database": db_name,
        "username": pg_username,
        "password": pg_password,
    },
    "redis": {
        "host": valkey_host,
        "port": valkey_port,
        "database": "0",
    },
}
if api_secret_key is not None:
    penpot_config["apiSecretKey"] = api_secret_key

penpot_dependencies = [penpot_namespace, valkey_chart]
if penpot_database:
    penpot_dependencies.append(penpot_database)

penpot_chart = k8s.helm.v4.Chart(
    "penpot",
    chart="penpot",
    version=penpot_chart_version,
    namespace=penpot_namespace.metadata.name,
    repository_opts=k8s.helm.v4.RepositoryOptsArgs(
        repo="https://helm.penpot.app",
    ),
    values={
        "global": {
            "postgresqlEnabled": False,
            "valkeyEnabled": False,
            "redisEnabled": False,
        },
        "config": penpot_config,
        "ingress": {
            "enabled": True,
            "className": "tailscale",
            "hosts": [ingress_host],
            "tls": [
                {
                    "hosts": [ingress_host],
                }
            ],
        },
    },
    opts=pulumi.ResourceOptions(depends_on=penpot_dependencies),
)

if mcp_enabled is None:
    mcp_enabled = True

mcp_server_deployment = None
mcp_server_service = None
mcp_ingress = None
mcp_ws_ingress = None
mcp_plugin_deployment = None
mcp_plugin_service = None
mcp_plugin_ingress = None

if mcp_enabled:
    mcp_server_labels = {
        "app": "penpot-mcp",
        "component": "server",
    }
    mcp_plugin_labels = {
        "app": "penpot-mcp",
        "component": "plugin",
    }

    mcp_server_deployment = k8s.apps.v1.Deployment(
        "penpot-mcp-server",
        metadata=k8s.meta.v1.ObjectMetaArgs(
            name="penpot-mcp-server",
            namespace=penpot_namespace.metadata.name,
            labels=mcp_server_labels,
        ),
        spec=k8s.apps.v1.DeploymentSpecArgs(
            replicas=1,
            selector=k8s.meta.v1.LabelSelectorArgs(
                match_labels=mcp_server_labels,
            ),
            template=k8s.core.v1.PodTemplateSpecArgs(
                metadata=k8s.meta.v1.ObjectMetaArgs(
                    labels=mcp_server_labels,
                ),
                spec=k8s.core.v1.PodSpecArgs(
                    containers=[
                        k8s.core.v1.ContainerArgs(
                            name="mcp-server",
                            image=mcp_server_image,
                            image_pull_policy="IfNotPresent",
                            env=[
                                k8s.core.v1.EnvVarArgs(name="PENPOT_MCP_SERVER_HOST", value="0.0.0.0"),
                                k8s.core.v1.EnvVarArgs(name="PENPOT_MCP_SERVER_PORT", value="4401"),
                                k8s.core.v1.EnvVarArgs(name="PENPOT_MCP_WEBSOCKET_PORT", value="4402"),
                                k8s.core.v1.EnvVarArgs(name="PENPOT_MCP_REPL_PORT", value="4403"),
                                k8s.core.v1.EnvVarArgs(name="PENPOT_MCP_REMOTE_MODE", value="true"),
                            ],
                            ports=[
                                k8s.core.v1.ContainerPortArgs(
                                    name="http",
                                    container_port=4401,
                                ),
                                k8s.core.v1.ContainerPortArgs(
                                    name="ws",
                                    container_port=4402,
                                ),
                            ],
                            readiness_probe=k8s.core.v1.ProbeArgs(
                                tcp_socket=k8s.core.v1.TCPSocketActionArgs(
                                    port=4401,
                                ),
                                initial_delay_seconds=5,
                                period_seconds=5,
                            ),
                            liveness_probe=k8s.core.v1.ProbeArgs(
                                tcp_socket=k8s.core.v1.TCPSocketActionArgs(
                                    port=4401,
                                ),
                                initial_delay_seconds=20,
                                period_seconds=10,
                            ),
                        ),
                    ],
                ),
            ),
        ),
        opts=pulumi.ResourceOptions(depends_on=[penpot_namespace, penpot_chart]),
    )

    mcp_server_service = k8s.core.v1.Service(
        "penpot-mcp-server-service",
        metadata=k8s.meta.v1.ObjectMetaArgs(
            name="penpot-mcp-server",
            namespace=penpot_namespace.metadata.name,
            labels=mcp_server_labels,
        ),
        spec=k8s.core.v1.ServiceSpecArgs(
            selector=mcp_server_labels,
            ports=[
                k8s.core.v1.ServicePortArgs(
                    name="http",
                    port=4401,
                    target_port=4401,
                ),
                k8s.core.v1.ServicePortArgs(
                    name="ws",
                    port=4402,
                    target_port=4402,
                ),
            ],
            type="ClusterIP",
        ),
        opts=pulumi.ResourceOptions(depends_on=[mcp_server_deployment]),
    )

    mcp_ingress = k8s.networking.v1.Ingress(
        "penpot-mcp-ingress",
        metadata=k8s.meta.v1.ObjectMetaArgs(
            name="penpot-mcp",
            namespace=penpot_namespace.metadata.name,
            labels=mcp_server_labels,
        ),
        spec=k8s.networking.v1.IngressSpecArgs(
            ingress_class_name="tailscale",
            rules=[
                k8s.networking.v1.IngressRuleArgs(
                    host=mcp_http_host,
                    http=k8s.networking.v1.HTTPIngressRuleValueArgs(
                        paths=[
                            k8s.networking.v1.HTTPIngressPathArgs(
                                path="/",
                                path_type="Prefix",
                                backend=k8s.networking.v1.IngressBackendArgs(
                                    service=k8s.networking.v1.IngressServiceBackendArgs(
                                        name=mcp_server_service.metadata.name,
                                        port=k8s.networking.v1.ServiceBackendPortArgs(
                                            number=4401,
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
                    hosts=[mcp_http_host],
                ),
            ],
        ),
        opts=pulumi.ResourceOptions(depends_on=[mcp_server_service]),
    )

    mcp_ws_ingress = k8s.networking.v1.Ingress(
        "penpot-mcp-ws-ingress",
        metadata=k8s.meta.v1.ObjectMetaArgs(
            name="penpot-mcp-ws",
            namespace=penpot_namespace.metadata.name,
            labels=mcp_server_labels,
        ),
        spec=k8s.networking.v1.IngressSpecArgs(
            ingress_class_name="tailscale",
            rules=[
                k8s.networking.v1.IngressRuleArgs(
                    host=mcp_ws_host,
                    http=k8s.networking.v1.HTTPIngressRuleValueArgs(
                        paths=[
                            k8s.networking.v1.HTTPIngressPathArgs(
                                path="/",
                                path_type="Prefix",
                                backend=k8s.networking.v1.IngressBackendArgs(
                                    service=k8s.networking.v1.IngressServiceBackendArgs(
                                        name=mcp_server_service.metadata.name,
                                        port=k8s.networking.v1.ServiceBackendPortArgs(
                                            number=4402,
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
                    hosts=[mcp_ws_host],
                ),
            ],
        ),
        opts=pulumi.ResourceOptions(depends_on=[mcp_server_service]),
    )

    mcp_plugin_deployment = k8s.apps.v1.Deployment(
        "penpot-mcp-plugin",
        metadata=k8s.meta.v1.ObjectMetaArgs(
            name="penpot-mcp-plugin",
            namespace=penpot_namespace.metadata.name,
            labels=mcp_plugin_labels,
        ),
        spec=k8s.apps.v1.DeploymentSpecArgs(
            replicas=1,
            selector=k8s.meta.v1.LabelSelectorArgs(
                match_labels=mcp_plugin_labels,
            ),
            template=k8s.core.v1.PodTemplateSpecArgs(
                metadata=k8s.meta.v1.ObjectMetaArgs(
                    labels=mcp_plugin_labels,
                ),
                spec=k8s.core.v1.PodSpecArgs(
                    containers=[
                        k8s.core.v1.ContainerArgs(
                            name="mcp-plugin",
                            image=mcp_plugin_image,
                            image_pull_policy="IfNotPresent",
                            ports=[
                                k8s.core.v1.ContainerPortArgs(
                                    name="http",
                                    container_port=80,
                                ),
                            ],
                            readiness_probe=k8s.core.v1.ProbeArgs(
                                http_get=k8s.core.v1.HTTPGetActionArgs(
                                    path="/manifest.json",
                                    port=80,
                                ),
                                initial_delay_seconds=5,
                                period_seconds=5,
                            ),
                            liveness_probe=k8s.core.v1.ProbeArgs(
                                http_get=k8s.core.v1.HTTPGetActionArgs(
                                    path="/manifest.json",
                                    port=80,
                                ),
                                initial_delay_seconds=20,
                                period_seconds=10,
                            ),
                        ),
                    ],
                ),
            ),
        ),
        opts=pulumi.ResourceOptions(depends_on=[mcp_server_service]),
    )

    mcp_plugin_service = k8s.core.v1.Service(
        "penpot-mcp-plugin-service",
        metadata=k8s.meta.v1.ObjectMetaArgs(
            name="penpot-mcp-plugin",
            namespace=penpot_namespace.metadata.name,
            labels=mcp_plugin_labels,
        ),
        spec=k8s.core.v1.ServiceSpecArgs(
            selector=mcp_plugin_labels,
            ports=[
                k8s.core.v1.ServicePortArgs(
                    name="http",
                    port=80,
                    target_port=80,
                ),
            ],
            type="ClusterIP",
        ),
        opts=pulumi.ResourceOptions(depends_on=[mcp_plugin_deployment]),
    )

    mcp_plugin_ingress = k8s.networking.v1.Ingress(
        "penpot-mcp-plugin-ingress",
        metadata=k8s.meta.v1.ObjectMetaArgs(
            name="penpot-mcp-plugin",
            namespace=penpot_namespace.metadata.name,
            labels=mcp_plugin_labels,
        ),
        spec=k8s.networking.v1.IngressSpecArgs(
            ingress_class_name="tailscale",
            rules=[
                k8s.networking.v1.IngressRuleArgs(
                    host=mcp_plugin_host,
                    http=k8s.networking.v1.HTTPIngressRuleValueArgs(
                        paths=[
                            k8s.networking.v1.HTTPIngressPathArgs(
                                path="/",
                                path_type="Prefix",
                                backend=k8s.networking.v1.IngressBackendArgs(
                                    service=k8s.networking.v1.IngressServiceBackendArgs(
                                        name=mcp_plugin_service.metadata.name,
                                        port=k8s.networking.v1.ServiceBackendPortArgs(
                                            number=80,
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
                    hosts=[mcp_plugin_host],
                ),
            ],
        ),
        opts=pulumi.ResourceOptions(depends_on=[mcp_plugin_service]),
    )

pulumi.export("namespace", penpot_namespace.metadata.name)
pulumi.export("ingress_host", ingress_host)
pulumi.export("public_uri", public_uri)
pulumi.export("postgres_host", pg_host)
pulumi.export("postgres_db", db_name)
pulumi.export("postgres_provider_host", pg_provider_host)
pulumi.export("database_managed_by_pulumi", create_database)
pulumi.export("valkey_host", valkey_host)
pulumi.export("penpot_release", penpot_chart.urn)
pulumi.export("mcp_enabled", mcp_enabled)
pulumi.export("mcp_url", f"https://{mcp_http_host}/mcp")
pulumi.export("mcp_ws_url", f"wss://{mcp_ws_host}")
pulumi.export("mcp_plugin_manifest_url", f"https://{mcp_plugin_host}/manifest.json")
