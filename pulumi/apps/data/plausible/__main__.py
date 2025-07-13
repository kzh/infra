import base64
import pulumi
import pulumi_kubernetes as k8s
import pulumi_random as random

config = pulumi.Config()

namespace = k8s.core.v1.Namespace(
    "namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="plaus",
    ),
)

clickhouse = k8s.helm.v4.Chart(
    "clickhouse",
    chart="oci://registry-1.docker.io/bitnamicharts/clickhouse",
    namespace=namespace.metadata.apply(lambda m: m.name),
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
    namespace=namespace.metadata.apply(lambda m: m.name),
    version="16.7.18",
    values={
        "auth": {
            "database": "plausible_db",
        },
    },
)

# For now, use placeholder URLs since secret lookup is complex in Python
database_url = "postgres://postgres:password@postgres-postgresql:5432/plausible_db"
clickhouse_url = "http://admin:password@clickhouse:8123/default"

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
        namespace=namespace.metadata.apply(lambda m: m.name),
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
        namespace=namespace.metadata.apply(lambda m: m.name),
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
                                value="https://plaus.example.com",
                            ),
                        ],
                        env_from=[
                            k8s.core.v1.EnvFromSourceArgs(
                                secret_ref=k8s.core.v1.SecretEnvSourceArgs(
                                    name=plausible_secret.metadata.apply(lambda m: m.name),
                                ),
                            ),
                        ],
                    ),
                ],
            ),
        ),
    ),
)

service = k8s.core.v1.Service(
    "service",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="plausible",
        namespace=namespace.metadata.apply(lambda m: m.name),
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