import pulumi_kubernetes as k8s
import pulumi_postgresql as pg
import pulumi_random as random

import pulumi

config = pulumi.Config()

APP_NAME = "litellm"
CHART = "oci://docker.litellm.ai/berriai/litellm-helm"

namespace_name = config.get("namespace") or APP_NAME
hostname = config.get("hostname") or APP_NAME
chart_version = config.get("chart_version") or "1.84.0"
storage_size = config.get("storage_size") or "2Gi"
storage_class_name = config.get("storage_class")
service_name = config.get("service_name") or APP_NAME
service_port = config.get_int("service_port") or 4000
postgres_stack = config.get("postgres_stack") or "kzh/postgresql/mx"
db_name = config.get("db_name") or APP_NAME
postgres_admin_db = config.get("postgres_admin_db") or "postgres"
postgres_sslmode = config.get("postgres_sslmode") or "disable"

TOKENS_PER_MILLION = 1_000_000

labels = {
    "app": APP_NAME,
    "app.kubernetes.io/name": APP_NAME,
    "app.kubernetes.io/part-of": APP_NAME,
}


def prefixed_secret(prefix: str, suffix: pulumi.Output[str]) -> pulumi.Output[str]:
    return pulumi.Output.concat(prefix, suffix)


def first_present(values: list[object]) -> object:
    primary, fallback = values
    return primary or fallback


def usd_per_token(usd_per_million_tokens: float) -> float:
    return usd_per_million_tokens / TOKENS_PER_MILLION


GPT_5_5_PRICING = {
    "input_cost_per_token": usd_per_token(5.00),
    "cache_read_input_token_cost": usd_per_token(0.50),
    "output_cost_per_token": usd_per_token(30.00),
}

GPT_5_4_MINI_PRICING = {
    "input_cost_per_token": usd_per_token(0.75),
    "cache_read_input_token_cost": usd_per_token(0.075),
    "output_cost_per_token": usd_per_token(4.50),
}

# OpenAI marks GPT-5.3-Codex-Spark rates as research preview/non-final. Until
# published rates exist, track it at the GPT-5.3-Codex-equivalent token rate.
GPT_5_3_CODEX_SPARK_PRICING = {
    "input_cost_per_token": usd_per_token(1.75),
    "cache_read_input_token_cost": usd_per_token(0.175),
    "output_cost_per_token": usd_per_token(14.00),
}


master_key_suffix = random.RandomPassword(
    "litellm-master-key-suffix",
    length=40,
    special=False,
)
salt_key_suffix = random.RandomPassword(
    "litellm-salt-key-suffix",
    length=40,
    special=False,
)
db_password = random.RandomPassword(
    "litellm-db-password",
    length=40,
    special=False,
)

master_key = prefixed_secret("sk-", master_key_suffix.result)
salt_key = prefixed_secret("sk-", salt_key_suffix.result)

pgref = pulumi.StackReference(postgres_stack)
pg_host = pgref.require_output("rw_service_fqdn")
pg_port = pgref.require_output("port").apply(lambda p: int(p) if p else 5432)
pg_username = pgref.require_output("username")
pg_password = pgref.require_output("password")
pg_provider_host = pulumi.Output.all(
    pgref.require_output("ts_hostname"), pgref.require_output("host")
).apply(first_present)

admin_provider = pg.Provider(
    "litellm-pg-admin",
    host=pg_provider_host,
    port=pg_port,
    username=pg_username,
    password=pg_password,
    database=postgres_admin_db,
    sslmode=postgres_sslmode,
)

db_role = pg.Role(
    "litellm-db-role",
    name=APP_NAME,
    password=db_password.result,
    login=True,
    opts=pulumi.ResourceOptions(provider=admin_provider),
)

database = pg.Database(
    "litellm-database",
    name=db_name,
    owner=db_role.name,
    opts=pulumi.ResourceOptions(provider=admin_provider, depends_on=[db_role]),
)

namespace = k8s.core.v1.Namespace(
    "litellm-namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=namespace_name,
        labels=labels,
    ),
)

env_secret = k8s.core.v1.Secret(
    "litellm-env",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="litellm-env",
        namespace=namespace.metadata.name,
        labels=labels,
    ),
    string_data={
        "LITELLM_SALT_KEY": salt_key,
    },
    type="Opaque",
    opts=pulumi.ResourceOptions(depends_on=[namespace]),
)

db_secret = k8s.core.v1.Secret(
    "litellm-db-credentials",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="litellm-db-credentials",
        namespace=namespace.metadata.name,
        labels=labels,
    ),
    string_data={
        "username": db_role.name,
        "password": db_password.result,
    },
    type="Opaque",
    opts=pulumi.ResourceOptions(depends_on=[namespace, database]),
)

token_storage_spec_args = {
    "access_modes": ["ReadWriteOnce"],
    "resources": k8s.core.v1.ResourceRequirementsArgs(
        requests={"storage": storage_size},
    ),
}
if storage_class_name:
    token_storage_spec_args["storage_class_name"] = storage_class_name

token_storage = k8s.core.v1.PersistentVolumeClaim(
    "litellm-chatgpt-tokens",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="litellm-chatgpt-tokens",
        namespace=namespace.metadata.name,
        labels=labels,
    ),
    spec=k8s.core.v1.PersistentVolumeClaimSpecArgs(**token_storage_spec_args),
    opts=pulumi.ResourceOptions(depends_on=[namespace]),
)

chart_values = {
    "fullnameOverride": service_name,
    "masterkey": master_key,
    "replicaCount": 1,
    "image": {
        "repository": "docker.litellm.ai/berriai/litellm",
        "tag": "main-stable",
        "pullPolicy": "IfNotPresent",
    },
    "service": {
        "type": "ClusterIP",
        "port": service_port,
    },
    "serviceAccount": {
        "create": True,
        "automount": False,
        "name": APP_NAME,
    },
    "environmentSecrets": [
        env_secret.metadata.name,
    ],
    "envVars": {
        "CHATGPT_TOKEN_DIR": "/data/chatgpt",
    },
    "proxy_config": {
        "model_list": [
            {
                "model_name": "chatgpt/gpt-5.5",
                "model_info": {"mode": "responses", **GPT_5_5_PRICING},
                "litellm_params": {"model": "chatgpt/gpt-5.5"},
            },
            {
                "model_name": "chatgpt/gpt-5.4-mini",
                "model_info": {"mode": "responses", **GPT_5_4_MINI_PRICING},
                "litellm_params": {"model": "chatgpt/gpt-5.4-mini"},
            },
            {
                "model_name": "chatgpt/gpt-5.3-codex-spark",
                "model_info": {
                    "mode": "responses",
                    **GPT_5_3_CODEX_SPARK_PRICING,
                },
                "litellm_params": {"model": "chatgpt/gpt-5.3-codex-spark"},
            },
        ],
        "general_settings": {
            "master_key": "os.environ/PROXY_MASTER_KEY",
            "store_model_in_db": True,
            "store_prompts_in_spend_logs": True,
        },
    },
    "volumes": [
        {
            "name": "chatgpt-tokens",
            "persistentVolumeClaim": {
                "claimName": token_storage.metadata.name,
            },
        },
    ],
    "volumeMounts": [
        {
            "name": "chatgpt-tokens",
            "mountPath": "/data/chatgpt",
        },
    ],
    "db": {
        "useExisting": True,
        "deployStandalone": False,
        "endpoint": pg_host,
        "database": db_name,
        "secret": {
            "name": db_secret.metadata.name,
            "usernameKey": "username",
            "passwordKey": "password",
        },
    },
    "migrationJob": {
        "enabled": True,
        "ttlSecondsAfterFinished": 300,
    },
    "resources": {
        "requests": {
            "cpu": "250m",
            "memory": "512Mi",
        },
        "limits": {
            "cpu": "2",
            "memory": "2Gi",
        },
    },
}

release = k8s.helm.v3.Release(
    "litellm",
    chart=CHART,
    name=APP_NAME,
    version=chart_version,
    namespace=namespace.metadata.name,
    values=chart_values,
    timeout=600,
    wait_for_jobs=True,
    cleanup_on_fail=True,
    opts=pulumi.ResourceOptions(
        depends_on=[namespace, env_secret, db_secret, token_storage, database]
    ),
)

ingress = k8s.networking.v1.Ingress(
    "litellm-ingress",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=APP_NAME,
        namespace=namespace.metadata.name,
        labels=labels,
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
                                    name=service_name,
                                    port=k8s.networking.v1.ServiceBackendPortArgs(
                                        number=service_port,
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
    opts=pulumi.ResourceOptions(depends_on=[release]),
)

pulumi.export("namespace", namespace.metadata.name)
pulumi.export("release", release.name)
pulumi.export("service", service_name)
pulumi.export("ingress", ingress.metadata.name)
pulumi.export("hostname", hostname)
pulumi.export("url", f"https://{hostname}")
pulumi.export("openai_base_url", f"https://{hostname}/v1")
pulumi.export("chatgpt_token_dir", "/data/chatgpt")
pulumi.export("chatgpt_token_pvc", token_storage.metadata.name)
pulumi.export("database", database.name)
pulumi.export("postgres_stack", postgres_stack)
pulumi.export("chart", CHART)
pulumi.export("chart_version", chart_version)
pulumi.export("master_key", master_key)
