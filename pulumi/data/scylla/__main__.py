import pulumi
import pulumi_kubernetes as k8s

operator = k8s.helm.v3.Release(
    "operator",
    chart="scylla-operator",
    namespace="scylla-operator",
    create_namespace=True,
    repository_opts=k8s.helm.v3.RepositoryOptsArgs(
        repo="https://scylla-operator-charts.storage.googleapis.com/stable"
    ),
    version="1.14.0",
    values={
        "replicas": 1,
    },
)

manager = k8s.helm.v3.Release(
    "manager",
    chart="scylla-manager",
    namespace="scylla-manager",
    create_namespace=True,
    repository_opts=k8s.helm.v3.RepositoryOptsArgs(
        repo="https://scylla-operator-charts.storage.googleapis.com/stable"
    ),
    version="1.14.0",
    values={
        "scylla": {
            "racks": [
                {
                    "name": "rack",
                    "members": 1,
                    "storage": {
                        "capacity": "5Gi",
                        "storageClassName": "rook-ceph-block",
                    },
                    "resources": {
                        "limits": {
                            "cpu": "1",
                            "memory": "200Mi",
                        },
                        "requests": {
                            "cpu": "1",
                            "memory": "200Mi",
                        },
                    },
                }
            ],
        }
    },
)

config = pulumi.Config()
scylla = k8s.helm.v3.Release(
    "scylla",
    chart="scylla",
    namespace=config.require("namespace"),
    create_namespace=True,
    repository_opts=k8s.helm.v3.RepositoryOptsArgs(
        repo="https://scylla-operator-charts.storage.googleapis.com/stable"
    ),
    version="1.14.0",
    values={
        "datacenter": "fsn1-dc17",
        "racks": [
            {
                "name": "mx",
                "members": 1,
                "storage": {
                    "capacity": "20Gi",
                    "storageClassName": "rook-ceph-block",
                },
                "resources": {
                    "limits": {
                        "cpu": "1",
                        "memory": "4Gi",
                    },
                    "requests": {
                        "cpu": "1",
                        "memory": "4Gi",
                    },
                },
            }
        ],
    },
)
