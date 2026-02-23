import pulumi
import pulumi_kubernetes as k8s

config = pulumi.Config()
pgref = pulumi.StackReference(config.require("postgres_stack"))
pg_namespace = pgref.get_output("k8s_namespace")
pg_host = pg_namespace.apply(lambda ns: f"postgresql-cluster-rw.{ns}.svc.cluster.local")
pg_port = pulumi.Output.format("{}", pgref.get_output("port"))
pg_user = pgref.get_output("username")
pg_password = pgref.get_output("password")
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

temporal_chart = k8s.helm.v4.Chart(
    "temporal",
    chart="temporal",
    repository_opts=k8s.helm.v4.RepositoryOptsArgs(
        repo="https://temporalio.github.io/helm-charts",
    ),
    version="0.73.1",
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
                    "default": {
                        "driver": "sql",
                        "sql": {
                            "driver": "postgres12",
                            "host": pg_host,
                            "port": pg_port,
                            "database": default_db,
                            "user": pg_user,
                            "password": pg_password,
                            "maxConns": 20,
                            "maxConnLifetime": "1h",
                        },
                    },
                    "visibility": {
                        "driver": "sql",
                        "sql": {
                            "driver": "postgres12",
                            "host": pg_host,
                            "port": pg_port,
                            "database": visibility_db,
                            "user": pg_user,
                            "password": pg_password,
                            "maxConns": 20,
                            "maxConnLifetime": "1h",
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
        "cassandra": {
            "enabled": False,
        },
        "elasticsearch": {
            "enabled": False,
        },
        "prometheus": {
            "enabled": False,
        },
        "grafana": {
            "enabled": False,
        },
        "schema": {
            "createDatabase": {
                "enabled": True,
            },
            "setup": {
                "enabled": True,
            },
            "update": {
                "enabled": True,
            },
        },
    },
    opts=pulumi.ResourceOptions(depends_on=[archival_pvc]),
)
