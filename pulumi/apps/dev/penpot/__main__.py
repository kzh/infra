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

pulumi.export("namespace", penpot_namespace.metadata.name)
pulumi.export("ingress_host", ingress_host)
pulumi.export("public_uri", public_uri)
pulumi.export("postgres_host", pg_host)
pulumi.export("postgres_db", db_name)
pulumi.export("postgres_provider_host", pg_provider_host)
pulumi.export("database_managed_by_pulumi", create_database)
pulumi.export("valkey_host", valkey_host)
pulumi.export("penpot_release", penpot_chart.urn)
