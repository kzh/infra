import hashlib

import pulumi_kubernetes as k8s
import pulumi_random as random
from pulumi_mysql_operator_crds.mysql.v2 import (
    InnoDBCluster,
    InnoDBClusterSpecArgs,
    InnoDBClusterSpecRouterArgs,
)

import pulumi

config = pulumi.Config()


def secret_env_var(
    name: str,
    secret_name: pulumi.Input[str],
    key: str,
) -> k8s.core.v1.EnvVarArgs:
    return k8s.core.v1.EnvVarArgs(
        name=name,
        value_from=k8s.core.v1.EnvVarSourceArgs(
            secret_key_ref=k8s.core.v1.SecretKeySelectorArgs(
                name=secret_name,
                key=key,
            )
        ),
    )


def stable_task_id(values: list[object]) -> str:
    payload = "|".join(str(value) for value in values).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def php_string(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace("'", "\\'")


def render_local_settings(values: list[object]) -> str:
    (
        wiki_name,
        server_url,
        script_path,
        language,
        db_host,
        db_name,
        db_user,
        db_password,
        db_prefix,
        secret_key,
        upgrade_key,
    ) = values

    return f"""<?php
if ( !defined( 'MEDIAWIKI' ) ) {{
    exit;
}}

$wgSitename = '{php_string(wiki_name)}';
$wgMetaNamespace = '{php_string(str(wiki_name).replace(" ", "_"))}';

$wgScriptPath = '{php_string(script_path)}';
$wgServer = '{php_string(server_url)}';
$wgCanonicalServer = $wgServer;
$wgResourceBasePath = $wgScriptPath;
$wgForceHTTPS = true;

$wgDBtype = 'mysql';
$wgDBserver = '{php_string(db_host)}';
$wgDBport = 3306;
$wgDBname = '{php_string(db_name)}';
$wgDBuser = '{php_string(db_user)}';
$wgDBpassword = '{php_string(db_password)}';
$wgDBprefix = '{php_string(db_prefix)}';
$wgDBTableOptions = 'ENGINE=InnoDB, DEFAULT CHARSET=binary';

$wgEnableUploads = true;
$wgUseInstantCommons = false;
$wgShellLocale = 'C.UTF-8';
$wgLanguageCode = '{php_string(language)}';

$wgMainCacheType = CACHE_NONE;
$wgParserCacheType = CACHE_NONE;
$wgSessionCacheType = CACHE_DB;
$wgMemCachedServers = [];

$wgSecretKey = '{php_string(secret_key)}';
$wgAuthenticationTokenVersion = '1';
$wgUpgradeKey = '{php_string(upgrade_key)}';

wfLoadSkin( 'Vector' );
"""


namespace_name = config.get("namespace") or "mediawiki"
hostname = config.get("hostname") or "wiki"
wiki_name = config.get("wikiName") or "MediaWiki"
admin_user = config.get("adminUser") or "Admin"
language = config.get("language") or "en"
script_path = config.get("scriptPath") or ""
mediawiki_image = config.get("mediawikiImage") or "mediawiki:1.45.3"
mysql_version = config.get("mysqlVersion") or "9.6.0"
mysql_client_image = config.get("mysqlClientImage") or f"mysql:{mysql_version}"
mysql_cluster_name = config.get("mysqlClusterName") or "mediawiki-mysql"
mysql_instances = config.get_int("mysqlInstances") or 1
mysql_router_instances = config.get_int("mysqlRouterInstances") or 1
mysql_storage_size = config.get("mysqlStorageSize") or "20Gi"
images_storage_size = config.get("imagesStorageSize") or "20Gi"
storage_class_name = config.get("storageClassName")
tailscale_domain = config.get("tailscaleDomain")
db_name = config.get("dbName") or "mediawiki"
db_user = config.get("dbUser") or "mediawiki"
db_prefix = config.get("dbPrefix") or ""
mysql_root_user = config.get("mysqlRootUser") or "root"
mysql_root_host = config.get("mysqlRootHost") or "%"
mysql_root_password_length = config.get_int("mysqlRootPasswordLength") or 32
db_password_length = config.get_int("dbPasswordLength") or 32
admin_password_length = config.get_int("adminPasswordLength") or 32
secret_key_length = config.get_int("secretKeyLength") or 64
local_settings_revision = "20260504-1"
db_init_revision = "20260504-1"
install_revision = "20260504-1"
update_revision = "20260504-1"

labels = {
    "app": "mediawiki",
}

mediawiki_namespace = k8s.core.v1.Namespace(
    "mediawiki-namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=namespace_name,
        labels=labels,
    ),
)

mysql_root_password = random.RandomPassword(
    "mediawiki-mysql-root-password",
    length=mysql_root_password_length,
    lower=True,
    upper=True,
    numeric=True,
    special=False,
    min_lower=1,
    min_upper=1,
    min_numeric=1,
)

mediawiki_db_password = random.RandomPassword(
    "mediawiki-db-password",
    length=db_password_length,
    lower=True,
    upper=True,
    numeric=True,
    special=False,
    min_lower=1,
    min_upper=1,
    min_numeric=1,
)

mediawiki_admin_password = random.RandomPassword(
    "mediawiki-admin-password",
    length=admin_password_length,
    lower=True,
    upper=True,
    numeric=True,
    special=False,
    min_lower=1,
    min_upper=1,
    min_numeric=1,
)

mediawiki_secret_key = random.RandomPassword(
    "mediawiki-secret-key",
    length=secret_key_length,
    lower=True,
    upper=True,
    numeric=True,
    special=False,
    min_lower=1,
    min_upper=1,
    min_numeric=1,
)

mediawiki_upgrade_key = random.RandomPassword(
    "mediawiki-upgrade-key",
    length=secret_key_length,
    lower=True,
    upper=True,
    numeric=True,
    special=False,
    min_lower=1,
    min_upper=1,
    min_numeric=1,
)

mysql_root_credentials = k8s.core.v1.Secret(
    "mediawiki-mysql-root-credentials",
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
    opts=pulumi.ResourceOptions(depends_on=[mediawiki_namespace]),
)

mediawiki_db_credentials = k8s.core.v1.Secret(
    "mediawiki-db-credentials",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="mediawiki-db-credentials",
        namespace=namespace_name,
    ),
    type="Opaque",
    string_data={
        "MEDIAWIKI_DB_NAME": db_name,
        "MEDIAWIKI_DB_USER": db_user,
        "MEDIAWIKI_DB_PASSWORD": mediawiki_db_password.result,
    },
    opts=pulumi.ResourceOptions(depends_on=[mediawiki_namespace]),
)

mediawiki_admin_credentials = k8s.core.v1.Secret(
    "mediawiki-admin-credentials",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="mediawiki-admin-credentials",
        namespace=namespace_name,
    ),
    type="Opaque",
    string_data={
        "MEDIAWIKI_ADMIN_USER": admin_user,
        "MEDIAWIKI_ADMIN_PASSWORD": mediawiki_admin_password.result,
    },
    opts=pulumi.ResourceOptions(depends_on=[mediawiki_namespace]),
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

mediawiki_mysql_cluster = InnoDBCluster(
    "mediawiki-mysql-cluster",
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
        depends_on=[mediawiki_namespace, mysql_root_credentials]
    ),
)

mysql_service_host = f"{mysql_cluster_name}.{namespace_name}.svc.cluster.local"
mediawiki_url = config.get("publicUrl") or (
    f"https://{hostname}.{tailscale_domain}"
    if tailscale_domain
    else f"https://{hostname}"
)

db_init_task_id = pulumi.Output.all(
    db_init_revision,
    mediawiki_db_password.result,
    db_name,
    db_user,
    mysql_service_host,
    mysql_version,
).apply(stable_task_id)

db_init_job = k8s.batch.v1.Job(
    "mediawiki-db-init",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="mediawiki-db-init",
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
                    "mediawiki.k8s.kevin/task-id": db_init_task_id,
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
                                name="MEDIAWIKI_DB_NAME",
                                secret_name=mediawiki_db_credentials.metadata.name,
                                key="MEDIAWIKI_DB_NAME",
                            ),
                            secret_env_var(
                                name="MEDIAWIKI_DB_USER",
                                secret_name=mediawiki_db_credentials.metadata.name,
                                key="MEDIAWIKI_DB_USER",
                            ),
                            secret_env_var(
                                name="MEDIAWIKI_DB_PASSWORD",
                                secret_name=mediawiki_db_credentials.metadata.name,
                                key="MEDIAWIKI_DB_PASSWORD",
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
CREATE DATABASE IF NOT EXISTS ${MEDIAWIKI_DB_NAME} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '${MEDIAWIKI_DB_USER}'@'%' IDENTIFIED BY '${MEDIAWIKI_DB_PASSWORD}';
ALTER USER '${MEDIAWIKI_DB_USER}'@'%' IDENTIFIED BY '${MEDIAWIKI_DB_PASSWORD}';
GRANT ALL PRIVILEGES ON ${MEDIAWIKI_DB_NAME}.* TO '${MEDIAWIKI_DB_USER}'@'%';
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
            mediawiki_mysql_cluster,
            mysql_root_credentials,
            mediawiki_db_credentials,
        ],
        delete_before_replace=True,
        replace_on_changes=["spec"],
    ),
)

local_settings_php = pulumi.Output.all(
    wiki_name,
    mediawiki_url,
    script_path,
    language,
    mysql_service_host,
    db_name,
    db_user,
    mediawiki_db_password.result,
    db_prefix,
    mediawiki_secret_key.result,
    mediawiki_upgrade_key.result,
).apply(render_local_settings)

local_settings_task_id = pulumi.Output.all(
    local_settings_revision,
    local_settings_php,
).apply(stable_task_id)

mediawiki_local_settings = k8s.core.v1.Secret(
    "mediawiki-local-settings",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="mediawiki-local-settings",
        namespace=namespace_name,
        annotations={
            "mediawiki.k8s.kevin/task-id": local_settings_task_id,
        },
    ),
    type="Opaque",
    string_data={
        "LocalSettings.php": local_settings_php,
    },
    opts=pulumi.ResourceOptions(depends_on=[mediawiki_namespace]),
)

install_task_id = pulumi.Output.all(
    install_revision,
    mediawiki_image,
    mysql_service_host,
    db_name,
    db_user,
    wiki_name,
    admin_user,
    mediawiki_url,
    script_path,
    language,
).apply(stable_task_id)

mediawiki_install_job = k8s.batch.v1.Job(
    "mediawiki-install",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="mediawiki-install",
        namespace=namespace_name,
        labels={
            **labels,
            "component": "install",
        },
    ),
    spec=k8s.batch.v1.JobSpecArgs(
        backoff_limit=3,
        ttl_seconds_after_finished=3600,
        template=k8s.core.v1.PodTemplateSpecArgs(
            metadata=k8s.meta.v1.ObjectMetaArgs(
                labels={
                    **labels,
                    "component": "install",
                },
                annotations={
                    "mediawiki.k8s.kevin/task-id": install_task_id,
                },
            ),
            spec=k8s.core.v1.PodSpecArgs(
                restart_policy="OnFailure",
                containers=[
                    k8s.core.v1.ContainerArgs(
                        name="install",
                        image=mediawiki_image,
                        image_pull_policy="IfNotPresent",
                        working_dir="/var/www/html",
                        env=[
                            k8s.core.v1.EnvVarArgs(
                                name="MEDIAWIKI_DB_HOST", value=mysql_service_host
                            ),
                            secret_env_var(
                                name="MEDIAWIKI_DB_NAME",
                                secret_name=mediawiki_db_credentials.metadata.name,
                                key="MEDIAWIKI_DB_NAME",
                            ),
                            secret_env_var(
                                name="MEDIAWIKI_DB_USER",
                                secret_name=mediawiki_db_credentials.metadata.name,
                                key="MEDIAWIKI_DB_USER",
                            ),
                            secret_env_var(
                                name="MEDIAWIKI_ADMIN_USER",
                                secret_name=mediawiki_admin_credentials.metadata.name,
                                key="MEDIAWIKI_ADMIN_USER",
                            ),
                            k8s.core.v1.EnvVarArgs(
                                name="MEDIAWIKI_WIKI_NAME", value=wiki_name
                            ),
                            k8s.core.v1.EnvVarArgs(
                                name="MEDIAWIKI_SERVER", value=mediawiki_url
                            ),
                            k8s.core.v1.EnvVarArgs(
                                name="MEDIAWIKI_SCRIPT_PATH", value=script_path
                            ),
                            k8s.core.v1.EnvVarArgs(
                                name="MEDIAWIKI_LANGUAGE", value=language
                            ),
                        ],
                        command=["sh", "-c"],
                        args=[
                            """
set -eu

until php -r '
$host = getenv("MEDIAWIKI_DB_HOST");
$db = getenv("MEDIAWIKI_DB_NAME");
$user = getenv("MEDIAWIKI_DB_USER");
$pass = trim(file_get_contents("/run/secrets/db/password"));
$mysqli = mysqli_init();
$mysqli->real_connect($host, $user, $pass, $db);
'; do
    echo "waiting for MediaWiki database at ${MEDIAWIKI_DB_HOST}"
    sleep 10
done

if php -r '
$host = getenv("MEDIAWIKI_DB_HOST");
$db = getenv("MEDIAWIKI_DB_NAME");
$user = getenv("MEDIAWIKI_DB_USER");
$pass = trim(file_get_contents("/run/secrets/db/password"));
$mysqli = mysqli_init();
$mysqli->real_connect($host, $user, $pass, $db);
$result = $mysqli->query("SHOW TABLES LIKE \\"site_stats\\"");
exit($result && $result->num_rows > 0 ? 0 : 1);
'; then
    echo "MediaWiki database schema already exists; skipping install"
    exit 0
fi

mkdir -p /tmp/mediawiki-install

php maintenance/run.php install \
    --server "${MEDIAWIKI_SERVER}" \
    --scriptpath "${MEDIAWIKI_SCRIPT_PATH}" \
    --dbtype mysql \
    --dbname "${MEDIAWIKI_DB_NAME}" \
    --dbserver "${MEDIAWIKI_DB_HOST}" \
    --dbuser "${MEDIAWIKI_DB_USER}" \
    --dbpassfile /run/secrets/db/password \
    --passfile /run/secrets/admin/password \
    --lang "${MEDIAWIKI_LANGUAGE}" \
    --confpath /tmp/mediawiki-install \
    "${MEDIAWIKI_WIKI_NAME}" \
    "${MEDIAWIKI_ADMIN_USER}"
""".strip()
                        ],
                        volume_mounts=[
                            k8s.core.v1.VolumeMountArgs(
                                name="db-password",
                                mount_path="/run/secrets/db",
                                read_only=True,
                            ),
                            k8s.core.v1.VolumeMountArgs(
                                name="admin-password",
                                mount_path="/run/secrets/admin",
                                read_only=True,
                            ),
                        ],
                    ),
                ],
                volumes=[
                    k8s.core.v1.VolumeArgs(
                        name="db-password",
                        secret=k8s.core.v1.SecretVolumeSourceArgs(
                            secret_name=mediawiki_db_credentials.metadata.name,
                            items=[
                                k8s.core.v1.KeyToPathArgs(
                                    key="MEDIAWIKI_DB_PASSWORD",
                                    path="password",
                                )
                            ],
                        ),
                    ),
                    k8s.core.v1.VolumeArgs(
                        name="admin-password",
                        secret=k8s.core.v1.SecretVolumeSourceArgs(
                            secret_name=mediawiki_admin_credentials.metadata.name,
                            items=[
                                k8s.core.v1.KeyToPathArgs(
                                    key="MEDIAWIKI_ADMIN_PASSWORD",
                                    path="password",
                                )
                            ],
                        ),
                    ),
                ],
            ),
        ),
    ),
    opts=pulumi.ResourceOptions(
        depends_on=[
            db_init_job,
            mediawiki_db_credentials,
            mediawiki_admin_credentials,
        ],
        delete_before_replace=True,
        replace_on_changes=["spec"],
    ),
)

update_task_id = pulumi.Output.all(
    update_revision,
    mediawiki_image,
    local_settings_task_id,
).apply(stable_task_id)

mediawiki_update_job = k8s.batch.v1.Job(
    "mediawiki-update",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="mediawiki-update",
        namespace=namespace_name,
        labels={
            **labels,
            "component": "update",
        },
    ),
    spec=k8s.batch.v1.JobSpecArgs(
        backoff_limit=3,
        ttl_seconds_after_finished=3600,
        template=k8s.core.v1.PodTemplateSpecArgs(
            metadata=k8s.meta.v1.ObjectMetaArgs(
                labels={
                    **labels,
                    "component": "update",
                },
                annotations={
                    "mediawiki.k8s.kevin/task-id": update_task_id,
                },
            ),
            spec=k8s.core.v1.PodSpecArgs(
                restart_policy="OnFailure",
                containers=[
                    k8s.core.v1.ContainerArgs(
                        name="update",
                        image=mediawiki_image,
                        image_pull_policy="IfNotPresent",
                        working_dir="/var/www/html",
                        command=["php", "maintenance/run.php", "update", "--quick"],
                        volume_mounts=[
                            k8s.core.v1.VolumeMountArgs(
                                name="local-settings",
                                mount_path="/var/www/html/LocalSettings.php",
                                sub_path="LocalSettings.php",
                                read_only=True,
                            ),
                        ],
                    ),
                ],
                volumes=[
                    k8s.core.v1.VolumeArgs(
                        name="local-settings",
                        secret=k8s.core.v1.SecretVolumeSourceArgs(
                            secret_name=mediawiki_local_settings.metadata.name,
                        ),
                    ),
                ],
            ),
        ),
    ),
    opts=pulumi.ResourceOptions(
        depends_on=[mediawiki_install_job, mediawiki_local_settings],
        delete_before_replace=True,
        replace_on_changes=["spec"],
    ),
)

mediawiki_images_pvc_spec_kwargs = {
    "access_modes": ["ReadWriteOnce"],
    "resources": k8s.core.v1.VolumeResourceRequirementsArgs(
        requests={"storage": images_storage_size},
    ),
}
if storage_class_name:
    mediawiki_images_pvc_spec_kwargs["storage_class_name"] = storage_class_name

mediawiki_images_pvc = k8s.core.v1.PersistentVolumeClaim(
    "mediawiki-images",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="mediawiki-images",
        namespace=namespace_name,
        labels=labels,
    ),
    spec=k8s.core.v1.PersistentVolumeClaimSpecArgs(**mediawiki_images_pvc_spec_kwargs),
    opts=pulumi.ResourceOptions(depends_on=[mediawiki_namespace]),
)

mediawiki_deployment = k8s.apps.v1.Deployment(
    "mediawiki-deployment",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="mediawiki",
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
                annotations={
                    "mediawiki.k8s.kevin/local-settings-task-id": local_settings_task_id,
                },
            ),
            spec=k8s.core.v1.PodSpecArgs(
                security_context=k8s.core.v1.PodSecurityContextArgs(
                    fs_group=33,
                    fs_group_change_policy="OnRootMismatch",
                ),
                containers=[
                    k8s.core.v1.ContainerArgs(
                        name="mediawiki",
                        image=mediawiki_image,
                        image_pull_policy="IfNotPresent",
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
                                name="local-settings",
                                mount_path="/var/www/html/LocalSettings.php",
                                sub_path="LocalSettings.php",
                                read_only=True,
                            ),
                            k8s.core.v1.VolumeMountArgs(
                                name="images",
                                mount_path="/var/www/html/images",
                            ),
                        ],
                    ),
                ],
                volumes=[
                    k8s.core.v1.VolumeArgs(
                        name="local-settings",
                        secret=k8s.core.v1.SecretVolumeSourceArgs(
                            secret_name=mediawiki_local_settings.metadata.name,
                        ),
                    ),
                    k8s.core.v1.VolumeArgs(
                        name="images",
                        persistent_volume_claim=k8s.core.v1.PersistentVolumeClaimVolumeSourceArgs(
                            claim_name=mediawiki_images_pvc.metadata.name,
                        ),
                    ),
                ],
            ),
        ),
    ),
    opts=pulumi.ResourceOptions(
        depends_on=[mediawiki_update_job, mediawiki_images_pvc]
    ),
)

mediawiki_service = k8s.core.v1.Service(
    "mediawiki-service",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="mediawiki",
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
    opts=pulumi.ResourceOptions(depends_on=[mediawiki_deployment]),
)

mediawiki_ingress = k8s.networking.v1.Ingress(
    "mediawiki-ingress",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="mediawiki",
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
                                    name=mediawiki_service.metadata.name,
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
    opts=pulumi.ResourceOptions(depends_on=[mediawiki_service]),
)

pulumi.export("namespace", namespace_name)
pulumi.export("hostname", hostname)
pulumi.export("url", mediawiki_url)
pulumi.export("mediawikiImage", mediawiki_image)
pulumi.export("mysqlClusterName", mysql_cluster_name)
pulumi.export("mysqlHost", mysql_service_host)
pulumi.export("mysqlPort", 3306)
pulumi.export("localSettingsSecretName", mediawiki_local_settings.metadata.name)
pulumi.export("adminUser", admin_user)
pulumi.export("adminPassword", mediawiki_admin_password.result)
