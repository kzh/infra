import pulumi
import pulumi_kubernetes as k8s
import pulumi_random as random

config = pulumi.Config()

plausible_namespace = k8s.core.v1.Namespace(
    "plausible-namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="plausible",
    ),
)

clickhouse = k8s.helm.v4.Chart(
    "clickhouse",
    chart="oci://registry-1.docker.io/bitnamicharts/clickhouse",
    namespace=plausible_namespace.metadata.name,
    version="9.3.9",
    values={
        "auth": {
            "username": "admin",
        },
        "zookeeper": {
            "enabled": False,
        },
        "ingress": {
            "enabled": False,
        },
        "resourcesPreset": "none",
        "shards": 1,
        "replicaCount": 1,
    },
)

postgres = k8s.helm.v4.Chart(
    "postgres",
    chart="oci://registry-1.docker.io/bitnamicharts/postgresql",
    namespace=plausible_namespace.metadata.name,
    version="16.7.18",
    values={
        "auth": {
            "database": "plausible_db",
        },
    },
)

# TODO: Properly extract connection details from Helm chart outputs
# For now, these values need to be configured via pulumi config
database_url = config.require("databaseUrl")
clickhouse_url = config.require("clickhouseUrl")

secret_key = random.RandomPassword(
    "password",
    length=64,
    special=True,
    lower=True,
    upper=True,
    numeric=True,
)

plausible_secret = k8s.core.v1.Secret(
    "plausible-secret",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="plausible",
        namespace=plausible_namespace.metadata.name,
    ),
    string_data={
        "DATABASE_URL": database_url,
        "CLICKHOUSE_DATABASE_URL": clickhouse_url,
        "SECRET_KEY_BASE": secret_key.result,
    },
)

labels = {
    "app": "plausible",
}

deployment = k8s.apps.v1.Deployment(
    "plausible",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="plausible",
        namespace=plausible_namespace.metadata.name,
        labels=labels,
    ),
    spec=k8s.apps.v1.DeploymentSpecArgs(
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
                        name="analytics",
                        image="plausible/analytics:v2",
                        ports=[
                            k8s.core.v1.ContainerPortArgs(
                                container_port=8000,
                            ),
                        ],
                        command=[
                            "sh",
                            "-c",
                            "sleep 10 && /entrypoint.sh db createdb && /entrypoint.sh db migrate && /entrypoint.sh run",
                        ],
                        env=[
                            k8s.core.v1.EnvVarArgs(
                                name="BASE_URL",
                                value=config.get("baseUrl", "https://plaus.example.com"),
                            ),
                        ],
                        env_from=[
                            k8s.core.v1.EnvFromSourceArgs(
                                secret_ref=k8s.core.v1.SecretEnvSourceArgs(
                                    name=plausible_secret.metadata.name,
                                ),
                            ),
                        ],
                    ),
                ],
            ),
        ),
    ),
)

plausible_service = k8s.core.v1.Service(
    "plausible-service",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="plausible",
        namespace=plausible_namespace.metadata.name,
        labels=labels,
    ),
    spec=k8s.core.v1.ServiceSpecArgs(
        type="ClusterIP",
        ports=[
            k8s.core.v1.ServicePortArgs(
                port=8000,
                target_port=8000,
            ),
        ],
        selector=labels,
    ),
)

# Note: CloudflaredService is from custom package, needs to be implemented separately
# or replaced with appropriate Python equivalent