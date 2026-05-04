import pulumi_kubernetes as k8s

import pulumi

config = pulumi.Config("n8n")
image = config.get("image") or "n8nio/n8n:2.19.2"
n8n_namespace = k8s.core.v1.Namespace(
    "n8n-namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=config.require("namespace"),
    ),
)
pgref = pulumi.StackReference(config.require("postgres_stack"))
pg_host = pgref.require_output("rw_service_fqdn")
pg_port = pgref.require_output("port")
pg_user = pgref.require_output("username")
pg_password = pgref.require_output("password")
db_name = config.get("db_name") or "n8n"

labels = {
    "app": "n8n",
}

pvc = k8s.core.v1.PersistentVolumeClaim(
    "storage",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        namespace=n8n_namespace.metadata.name,
        name="n8n",
        labels=labels,
    ),
    spec=k8s.core.v1.PersistentVolumeClaimSpecArgs(
        access_modes=["ReadWriteOnce"],
        resources=k8s.core.v1.ResourceRequirementsArgs(
            requests={"storage": "4Gi"},
        ),
    ),
)

db_secret = k8s.core.v1.Secret(
    "n8n-db-credentials",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        namespace=n8n_namespace.metadata.name,
        name="n8n-db-credentials",
        labels=labels,
    ),
    string_data={
        "DB_POSTGRESDB_PASSWORD": pg_password,
    },
    type="Opaque",
    opts=pulumi.ResourceOptions(depends_on=[n8n_namespace]),
)

deployment = k8s.apps.v1.Deployment(
    "n8n-deployment",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        namespace=n8n_namespace.metadata.name,
        name="n8n",
        labels=labels,
    ),
    spec=k8s.apps.v1.DeploymentSpecArgs(
        selector=k8s.meta.v1.LabelSelectorArgs(
            match_labels=labels,
        ),
        replicas=1,
        template=k8s.core.v1.PodTemplateSpecArgs(
            metadata=k8s.meta.v1.ObjectMetaArgs(
                labels=labels,
            ),
            spec=k8s.core.v1.PodSpecArgs(
                init_containers=[
                    k8s.core.v1.ContainerArgs(
                        name="init",
                        image="busybox:1.37.0",
                        command=["sh", "-c", "chown 1000:1000 /data"],
                        volume_mounts=[
                            k8s.core.v1.VolumeMountArgs(
                                name="n8n",
                                mount_path="/data",
                            ),
                        ],
                    ),
                ],
                containers=[
                    k8s.core.v1.ContainerArgs(
                        name="n8n",
                        image=image,
                        image_pull_policy="IfNotPresent",
                        env=[
                            k8s.core.v1.EnvVarArgs(
                                name="DB_TYPE",
                                value="postgresdb",
                            ),
                            k8s.core.v1.EnvVarArgs(
                                name="DB_POSTGRESDB_HOST",
                                value=pg_host,
                            ),
                            k8s.core.v1.EnvVarArgs(
                                name="DB_POSTGRESDB_PORT",
                                value=pulumi.Output.format("{}", pg_port),
                            ),
                            k8s.core.v1.EnvVarArgs(
                                name="DB_POSTGRESDB_DATABASE",
                                value=db_name,
                            ),
                            k8s.core.v1.EnvVarArgs(
                                name="DB_POSTGRESDB_USER",
                                value=pg_user,
                            ),
                            k8s.core.v1.EnvVarArgs(
                                name="DB_POSTGRESDB_PASSWORD",
                                value_from=k8s.core.v1.EnvVarSourceArgs(
                                    secret_key_ref=k8s.core.v1.SecretKeySelectorArgs(
                                        name=db_secret.metadata.name,
                                        key="DB_POSTGRESDB_PASSWORD",
                                    ),
                                ),
                            ),
                            k8s.core.v1.EnvVarArgs(
                                name="N8N_PROTOCOL",
                                value="http",
                            ),
                            k8s.core.v1.EnvVarArgs(
                                name="N8N_PORT",
                                value="5678",
                            ),
                        ],
                        command=["/bin/sh"],
                        args=[
                            "-c",
                            "sleep 5; n8n start",
                        ],
                        ports=[
                            k8s.core.v1.ContainerPortArgs(
                                container_port=5678,
                            ),
                        ],
                        volume_mounts=[
                            k8s.core.v1.VolumeMountArgs(
                                name="n8n",
                                mount_path="/home/node/.n8n",
                            ),
                        ],
                    ),
                ],
                volumes=[
                    k8s.core.v1.VolumeArgs(
                        name="n8n",
                        persistent_volume_claim=k8s.core.v1.PersistentVolumeClaimVolumeSourceArgs(
                            claim_name=pvc.metadata.name,
                        ),
                    ),
                ],
            ),
        ),
    ),
    opts=pulumi.ResourceOptions(depends_on=[pvc, db_secret]),
)

service = k8s.core.v1.Service(
    "n8n-service",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        namespace=n8n_namespace.metadata.name,
        name="n8n",
        labels=labels,
    ),
    spec=k8s.core.v1.ServiceSpecArgs(
        type="ClusterIP",
        ports=[
            k8s.core.v1.ServicePortArgs(
                port=5678,
                target_port=5678,
            ),
        ],
        selector=labels,
    ),
)

n8n_ingress = k8s.networking.v1.Ingress(
    "n8n-ingress",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="n8n",
        namespace=n8n_namespace.metadata.name,
        labels=labels,
    ),
    spec=k8s.networking.v1.IngressSpecArgs(
        ingress_class_name="tailscale",
        rules=[
            k8s.networking.v1.IngressRuleArgs(
                host="n8n",
                http=k8s.networking.v1.HTTPIngressRuleValueArgs(
                    paths=[
                        k8s.networking.v1.HTTPIngressPathArgs(
                            path="/",
                            path_type="Prefix",
                            backend=k8s.networking.v1.IngressBackendArgs(
                                service=k8s.networking.v1.IngressServiceBackendArgs(
                                    name="n8n",
                                    port=k8s.networking.v1.ServiceBackendPortArgs(
                                        number=5678,
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
                hosts=["n8n"],
            ),
        ],
    ),
)
