import pulumi_kubernetes as k8s

import pulumi

config = pulumi.Config()
pgref = pulumi.StackReference(config.require("postgres_stack"))
pg_host = pgref.require_output("rw_service_fqdn")
pg_port = pulumi.Output.format("{}", pgref.require_output("port"))
pg_connect_addr = pulumi.Output.format("{}:{}", pg_host, pg_port)
pg_user = pgref.require_output("username")
pg_password = pgref.require_output("password")
default_db = config.get("default_db") or "temporal"
visibility_db = config.get("visibility_db") or "temporal_visibility"
archival_pvc_name = config.get("archival_pvc_name") or "temporal-archival"
archival_storage_size = config.get("archival_storage_size") or "20Gi"
archival_mount_path = config.get("archival_mount_path") or "/var/temporal-archive"
history_archival_uri = f"file://{archival_mount_path}/history"
visibility_archival_uri = f"file://{archival_mount_path}/visibility"

temporal_namespace = k8s.core.v1.Namespace(
    "temporal-namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=config.require("namespace"),
    ),
)

archival_pvc = k8s.core.v1.PersistentVolumeClaim(
    "temporal-archival-pvc",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        namespace=temporal_namespace.metadata.name,
        name=archival_pvc_name,
    ),
    spec=k8s.core.v1.PersistentVolumeClaimSpecArgs(
        access_modes=["ReadWriteOnce"],
        resources=k8s.core.v1.VolumeResourceRequirementsArgs(
            requests={"storage": archival_storage_size},
        ),
    ),
)

db_secret = k8s.core.v1.Secret(
    "temporal-db-credentials",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="temporal-db-credentials",
        namespace=temporal_namespace.metadata.name,
    ),
    string_data={
        "password": pg_password,
    },
    type="Opaque",
    opts=pulumi.ResourceOptions(depends_on=[temporal_namespace]),
)

temporal_chart = k8s.helm.v4.Chart(
    "temporal",
    chart="temporal",
    repository_opts=k8s.helm.v4.RepositoryOptsArgs(
        repo="https://temporalio.github.io/helm-charts",
    ),
    version="1.2.0",
    namespace=temporal_namespace.metadata.name,
    values={
        "server": {
            "replicaCount": 1,
            "frontend": {
                "service": {
                    "annotations": {
                        "tailscale.com/expose": "true",
                        "tailscale.com/hostname": "temporal-frontend",
                    }
                }
            },
            "config": {
                "namespaces": {
                    "create": True,
                },
                "persistence": {
                    "defaultStore": "default",
                    "visibilityStore": "visibility",
                    "datastores": {
                        "default": {
                            "sql": {
                                "createDatabase": True,
                                "manageSchema": True,
                                "pluginName": "postgres12",
                                "driverName": "postgres12",
                                "databaseName": default_db,
                                "connectAddr": pg_connect_addr,
                                "connectProtocol": "tcp",
                                "user": pg_user,
                                "existingSecret": db_secret.metadata.name,
                                "secretKey": "password",
                                "maxConns": 20,
                                "maxConnLifetime": "1h",
                            },
                        },
                        "visibility": {
                            "sql": {
                                "createDatabase": True,
                                "manageSchema": True,
                                "pluginName": "postgres12",
                                "driverName": "postgres12",
                                "databaseName": visibility_db,
                                "connectAddr": pg_connect_addr,
                                "connectProtocol": "tcp",
                                "user": pg_user,
                                "existingSecret": db_secret.metadata.name,
                                "secretKey": "password",
                                "maxConns": 20,
                                "maxConnLifetime": "1h",
                            },
                        },
                    },
                },
            },
            "archival": {
                "history": {
                    "state": "enabled",
                    "enableRead": True,
                    "provider": {
                        "filestore": {
                            "fileMode": "0666",
                            "dirMode": "0766",
                        },
                    },
                },
                "visibility": {
                    "state": "enabled",
                    "enableRead": True,
                    "provider": {
                        "filestore": {
                            "fileMode": "0666",
                            "dirMode": "0766",
                        },
                    },
                },
            },
            "namespaceDefaults": {
                "archival": {
                    "history": {
                        "state": "enabled",
                        "URI": history_archival_uri,
                    },
                    "visibility": {
                        "state": "enabled",
                        "URI": visibility_archival_uri,
                    },
                },
            },
            "additionalVolumes": [
                {
                    "name": "archival-data",
                    "persistentVolumeClaim": {
                        "claimName": archival_pvc.metadata.name,
                    },
                }
            ],
            "additionalVolumeMounts": [
                {
                    "name": "archival-data",
                    "mountPath": archival_mount_path,
                }
            ],
        },
        "schema": {
            "useHelmHooks": False,
        },
        "web": {
            "ingress": {
                "enabled": True,
                "className": "tailscale",
                "tls": [
                    {
                        "hosts": ["temporal"],
                        "secretName": "",
                    }
                ],
            }
        },
        "shims": {
            "dockerize": False,
            "elasticsearchTool": False,
        },
    },
    opts=pulumi.ResourceOptions(depends_on=[archival_pvc, db_secret]),
)
