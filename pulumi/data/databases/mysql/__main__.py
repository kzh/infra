import pulumi_kubernetes as k8s
import pulumi_random as random
from pulumi_mysql_operator_crds.mysql.v2 import (
    InnoDBCluster,
    InnoDBClusterSpecArgs,
    InnoDBClusterSpecRouterArgs,
)

import pulumi

config = pulumi.Config()

namespace_name = config.get("namespace") or "mysql"
mysql_cluster_name = config.get("clusterName") or "shared-mysql"
mysql_version = config.get("mysqlVersion") or "9.7.0"
mysql_instances = config.get_int("mysqlInstances") or 1
mysql_router_instances = config.get_int("mysqlRouterInstances") or 1
mysql_storage_size = config.get("mysqlStorageSize") or "40Gi"
storage_class_name = config.get("storageClassName")
mysql_root_user = config.get("mysqlRootUser") or "root"
mysql_root_host = config.get("mysqlRootHost") or "%"
mysql_root_password_length = config.get_int("mysqlRootPasswordLength") or 32
mysql_mycnf = (
    config.get("mycnf")
    or """
[mysqld]
innodb_buffer_pool_size=256M
max_connections=100
""".lstrip()
)

labels = {
    "app": "shared-mysql",
}

mysql_namespace = k8s.core.v1.Namespace(
    "mysql-namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=namespace_name,
        labels=labels,
    ),
)

mysql_root_password = random.RandomPassword(
    "shared-mysql-root-password",
    length=mysql_root_password_length,
    lower=True,
    upper=True,
    numeric=True,
    special=False,
    min_lower=1,
    min_upper=1,
    min_numeric=1,
)

mysql_root_credentials = k8s.core.v1.Secret(
    "shared-mysql-root-credentials",
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
    opts=pulumi.ResourceOptions(depends_on=[mysql_namespace]),
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

shared_mysql_cluster = InnoDBCluster(
    "shared-mysql-cluster",
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
        mycnf=mysql_mycnf,
        datadir_volume_claim_template=mysql_data_volume_claim_template,
    ),
    opts=pulumi.ResourceOptions(depends_on=[mysql_namespace, mysql_root_credentials]),
)

mysql_service_host = f"{mysql_cluster_name}.{namespace_name}.svc.cluster.local"
mysql_instance_host = (
    f"{mysql_cluster_name}-0."
    f"{mysql_cluster_name}-instances."
    f"{namespace_name}.svc.cluster.local"
)

pulumi.export("namespace", namespace_name)
pulumi.export("mysqlClusterName", mysql_cluster_name)
pulumi.export("mysqlVersion", mysql_version)
pulumi.export("mysqlHost", mysql_service_host)
pulumi.export("mysqlInstanceHost", mysql_instance_host)
pulumi.export("mysqlPort", 3306)
pulumi.export("rootSecretName", mysql_root_credentials.metadata.name)
pulumi.export("rootUser", pulumi.Output.secret(mysql_root_user))
pulumi.export("rootHost", mysql_root_host)
pulumi.export("rootPassword", mysql_root_password.result)
pulumi.export("status", shared_mysql_cluster.status)
