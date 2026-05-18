import pulumi_kubernetes as k8s
import pulumi_random as random
from infra_helpers.k8s import secret_env_var, stable_task_id
from pulumi_mysql_operator_crds.mysql.v2 import (
    InnoDBCluster,
    InnoDBClusterSpecArgs,
    InnoDBClusterSpecRouterArgs,
)

import pulumi

config = pulumi.Config()


namespace_name = config.get("namespace") or "wordpress"
hostname = config.get("hostname") or "wordpress"
wordpress_image = config.get("wordpressImage") or "wordpress:6.9.4-php8.3-apache"
mysql_version = config.get("mysqlVersion") or "9.7.0"
mysql_client_image = config.get("mysqlClientImage") or f"mysql:{mysql_version}"
mysql_cluster_name = config.get("mysqlClusterName") or "wordpress-mysql"
mysql_instances = config.get_int("mysqlInstances") or 1
mysql_router_instances = config.get_int("mysqlRouterInstances") or 1
mysql_storage_size = config.get("mysqlStorageSize") or "20Gi"
wordpress_storage_size = config.get("wordpressStorageSize") or "20Gi"
storage_class_name = config.get("storageClassName")
tailscale_domain = config.get("tailscaleDomain")
db_name = config.get("dbName") or "wordpress"
db_user = config.get("dbUser") or "wordpress"
mysql_root_user = config.get("mysqlRootUser") or "root"
mysql_root_host = config.get("mysqlRootHost") or "%"
table_prefix = config.get("tablePrefix") or "wp_"
mysql_root_password_length = config.get_int("mysqlRootPasswordLength") or 32
wordpress_db_password_length = config.get_int("wordpressDbPasswordLength") or 32
db_init_revision = "20260316-2"

labels = {
    "app": "wordpress",
}

wordpress_namespace = k8s.core.v1.Namespace(
    "wordpress-namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=namespace_name,
        labels=labels,
    ),
)

mysql_root_password = random.RandomPassword(
    "wordpress-mysql-root-password",
    length=mysql_root_password_length,
    lower=True,
    upper=True,
    numeric=True,
    special=False,
    min_lower=1,
    min_upper=1,
    min_numeric=1,
)

wordpress_db_password = random.RandomPassword(
    "wordpress-db-password",
    length=wordpress_db_password_length,
    lower=True,
    upper=True,
    numeric=True,
    special=False,
    min_lower=1,
    min_upper=1,
    min_numeric=1,
)

mysql_root_credentials = k8s.core.v1.Secret(
    "wordpress-mysql-root-credentials",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=f"{mysql_cluster_name}-root-credentials",
        namespace=namespace_name,
    ),
    type="Opaque",
    string_data={
        "rootUser": mysql_root_user,
        "rootHost": mysql_root_host,
        "rootPassword": mysql_root_password.result,
    },
    opts=pulumi.ResourceOptions(depends_on=[wordpress_namespace]),
)

wordpress_db_credentials = k8s.core.v1.Secret(
    "wordpress-db-credentials",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="wordpress-db-credentials",
        namespace=namespace_name,
    ),
    type="Opaque",
    string_data={
        "WORDPRESS_DB_NAME": db_name,
        "WORDPRESS_DB_USER": db_user,
        "WORDPRESS_DB_PASSWORD": wordpress_db_password.result,
    },
    opts=pulumi.ResourceOptions(depends_on=[wordpress_namespace]),
)

mysql_data_volume_claim_template = {
    "accessModes": ["ReadWriteOnce"],
    "resources": {
        "requests": {
            "storage": mysql_storage_size,
        }
    },
}
if storage_class_name:
    mysql_data_volume_claim_template["storageClassName"] = storage_class_name

wordpress_mysql_cluster = InnoDBCluster(
    "wordpress-mysql-cluster",
    metadata={
        "name": mysql_cluster_name,
        "namespace": namespace_name,
        "annotations": {
            "pulumi.com/skipAwait": "true",
        },
    },
    spec=InnoDBClusterSpecArgs(
        secret_name=mysql_root_credentials.metadata.name,
        version=mysql_version,
        instances=mysql_instances,
        router=InnoDBClusterSpecRouterArgs(instances=mysql_router_instances),
        tls_use_self_signed=True,
        datadir_volume_claim_template=mysql_data_volume_claim_template,
    ),
    opts=pulumi.ResourceOptions(
        depends_on=[wordpress_namespace, mysql_root_credentials]
    ),
)

mysql_service_host = f"{mysql_cluster_name}.{namespace_name}.svc.cluster.local"
wordpress_url = config.get("publicUrl") or (
    f"https://{hostname}.{tailscale_domain}"
    if tailscale_domain
    else f"https://{hostname}"
)

db_init_task_id = pulumi.Output.all(
    db_init_revision,
    wordpress_db_password.result,
    db_name,
    db_user,
    mysql_service_host,
    mysql_version,
).apply(stable_task_id)

db_init_job = k8s.batch.v1.Job(
    "wordpress-db-init",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="wordpress-db-init",
        namespace=namespace_name,
        labels={
            **labels,
            "component": "db-init",
        },
    ),
    spec=k8s.batch.v1.JobSpecArgs(
        backoff_limit=10,
        ttl_seconds_after_finished=3600,
        template=k8s.core.v1.PodTemplateSpecArgs(
            metadata=k8s.meta.v1.ObjectMetaArgs(
                labels={
                    **labels,
                    "component": "db-init",
                },
                annotations={
                    "wordpress.k8s.kevin/task-id": db_init_task_id,
                },
            ),
            spec=k8s.core.v1.PodSpecArgs(
                restart_policy="OnFailure",
                containers=[
                    k8s.core.v1.ContainerArgs(
                        name="db-init",
                        image=mysql_client_image,
                        image_pull_policy="IfNotPresent",
                        env=[
                            k8s.core.v1.EnvVarArgs(
                                name="MYSQL_HOST", value=mysql_service_host
                            ),
                            k8s.core.v1.EnvVarArgs(name="MYSQL_PORT", value="3306"),
                            secret_env_var(
                                name="MYSQL_ROOT_USER",
                                secret_name=mysql_root_credentials.metadata.name,
                                key="rootUser",
                            ),
                            secret_env_var(
                                name="MYSQL_PWD",
                                secret_name=mysql_root_credentials.metadata.name,
                                key="rootPassword",
                            ),
                            secret_env_var(
                                name="WORDPRESS_DB_NAME",
                                secret_name=wordpress_db_credentials.metadata.name,
                                key="WORDPRESS_DB_NAME",
                            ),
                            secret_env_var(
                                name="WORDPRESS_DB_USER",
                                secret_name=wordpress_db_credentials.metadata.name,
                                key="WORDPRESS_DB_USER",
                            ),
                            secret_env_var(
                                name="WORDPRESS_DB_PASSWORD",
                                secret_name=wordpress_db_credentials.metadata.name,
                                key="WORDPRESS_DB_PASSWORD",
                            ),
                        ],
                        command=["sh", "-c"],
                        args=[
                            """
set -eu

until mysqladmin ping -h "${MYSQL_HOST}" -P "${MYSQL_PORT}" -u"${MYSQL_ROOT_USER}" --silent; do
    echo "waiting for MySQL router service at ${MYSQL_HOST}:${MYSQL_PORT}"
    sleep 10
done

mysql -h "${MYSQL_HOST}" -P "${MYSQL_PORT}" -u"${MYSQL_ROOT_USER}" <<SQL
CREATE DATABASE IF NOT EXISTS ${WORDPRESS_DB_NAME} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '${WORDPRESS_DB_USER}'@'%' IDENTIFIED BY '${WORDPRESS_DB_PASSWORD}';
ALTER USER '${WORDPRESS_DB_USER}'@'%' IDENTIFIED BY '${WORDPRESS_DB_PASSWORD}';
GRANT ALL PRIVILEGES ON ${WORDPRESS_DB_NAME}.* TO '${WORDPRESS_DB_USER}'@'%';
FLUSH PRIVILEGES;
SQL
""".strip()
                        ],
                    ),
                ],
            ),
        ),
    ),
    opts=pulumi.ResourceOptions(
        depends_on=[
            wordpress_mysql_cluster,
            mysql_root_credentials,
            wordpress_db_credentials,
        ],
        delete_before_replace=True,
        replace_on_changes=["spec"],
    ),
)

wordpress_pvc_spec_kwargs = {
    "access_modes": ["ReadWriteOnce"],
    "resources": k8s.core.v1.VolumeResourceRequirementsArgs(
        requests={"storage": wordpress_storage_size},
    ),
}
if storage_class_name:
    wordpress_pvc_spec_kwargs["storage_class_name"] = storage_class_name

wordpress_pvc_spec = k8s.core.v1.PersistentVolumeClaimSpecArgs(
    **wordpress_pvc_spec_kwargs
)

wordpress_pvc = k8s.core.v1.PersistentVolumeClaim(
    "wordpress-data",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="wordpress-data",
        namespace=namespace_name,
        labels=labels,
    ),
    spec=wordpress_pvc_spec,
    opts=pulumi.ResourceOptions(depends_on=[wordpress_namespace]),
)

wordpress_deployment = k8s.apps.v1.Deployment(
    "wordpress-deployment",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="wordpress",
        namespace=namespace_name,
        labels=labels,
    ),
    spec=k8s.apps.v1.DeploymentSpecArgs(
        replicas=1,
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
                        name="wordpress",
                        image=wordpress_image,
                        image_pull_policy="IfNotPresent",
                        env=[
                            k8s.core.v1.EnvVarArgs(
                                name="WORDPRESS_DB_HOST", value=mysql_service_host
                            ),
                            secret_env_var(
                                name="WORDPRESS_DB_NAME",
                                secret_name=wordpress_db_credentials.metadata.name,
                                key="WORDPRESS_DB_NAME",
                            ),
                            secret_env_var(
                                name="WORDPRESS_DB_USER",
                                secret_name=wordpress_db_credentials.metadata.name,
                                key="WORDPRESS_DB_USER",
                            ),
                            secret_env_var(
                                name="WORDPRESS_DB_PASSWORD",
                                secret_name=wordpress_db_credentials.metadata.name,
                                key="WORDPRESS_DB_PASSWORD",
                            ),
                            k8s.core.v1.EnvVarArgs(
                                name="WORDPRESS_TABLE_PREFIX", value=table_prefix
                            ),
                            k8s.core.v1.EnvVarArgs(
                                name="WORDPRESS_CONFIG_EXTRA",
                                value=(
                                    f"define('WP_HOME', '{wordpress_url}');\n"
                                    f"define('WP_SITEURL', '{wordpress_url}');"
                                ),
                            ),
                        ],
                        ports=[
                            k8s.core.v1.ContainerPortArgs(
                                name="http",
                                container_port=80,
                            ),
                        ],
                        readiness_probe=k8s.core.v1.ProbeArgs(
                            http_get=k8s.core.v1.HTTPGetActionArgs(
                                path="/",
                                port="http",
                            ),
                            initial_delay_seconds=10,
                            period_seconds=10,
                            timeout_seconds=5,
                        ),
                        liveness_probe=k8s.core.v1.ProbeArgs(
                            http_get=k8s.core.v1.HTTPGetActionArgs(
                                path="/",
                                port="http",
                            ),
                            initial_delay_seconds=30,
                            period_seconds=20,
                            timeout_seconds=5,
                        ),
                        volume_mounts=[
                            k8s.core.v1.VolumeMountArgs(
                                name="wordpress-data",
                                mount_path="/var/www/html",
                            ),
                        ],
                    ),
                ],
                volumes=[
                    k8s.core.v1.VolumeArgs(
                        name="wordpress-data",
                        persistent_volume_claim=k8s.core.v1.PersistentVolumeClaimVolumeSourceArgs(
                            claim_name=wordpress_pvc.metadata.name,
                        ),
                    ),
                ],
            ),
        ),
    ),
    opts=pulumi.ResourceOptions(depends_on=[db_init_job, wordpress_pvc]),
)

wordpress_service = k8s.core.v1.Service(
    "wordpress-service",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="wordpress",
        namespace=namespace_name,
        labels=labels,
    ),
    spec=k8s.core.v1.ServiceSpecArgs(
        type="ClusterIP",
        selector=labels,
        ports=[
            k8s.core.v1.ServicePortArgs(
                name="http",
                port=80,
                target_port=80,
            ),
        ],
    ),
    opts=pulumi.ResourceOptions(depends_on=[wordpress_deployment]),
)

wordpress_ingress = k8s.networking.v1.Ingress(
    "wordpress-ingress",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="wordpress",
        namespace=namespace_name,
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
                                    name=wordpress_service.metadata.name,
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
                hosts=[hostname],
            ),
        ],
    ),
    opts=pulumi.ResourceOptions(depends_on=[wordpress_service]),
)

pulumi.export("namespace", namespace_name)
pulumi.export("hostname", hostname)
pulumi.export("url", wordpress_url)
pulumi.export("wordpressImage", wordpress_image)
pulumi.export("mysqlClusterName", mysql_cluster_name)
pulumi.export("mysqlHost", mysql_service_host)
pulumi.export("mysqlPort", 3306)
