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
                        "capacity": "20Gi",
                        "storageClassName": "rook-ceph-block",
                    },
                    "resources": {
                        "limits": {
                            "cpu": "2",
                            "memory": "2Gi",
                        },
                        "requests": {
                            "cpu": "2",
                            "memory": "2Gi",
                        },
                    },
                }
            ],
        }
    },
)


def annotate_scylla_cluster(args: pulumi.ResourceTransformArgs):
    if args.type_ == "kubernetes:scylla.scylladb.com/v1:ScyllaCluster":
        args.props["metadata"]["annotations"] = {
            "pulumi.com/waitFor": r"jsonpath={.status.readyMembers}=1"
        }
    return pulumi.ResourceTransformResult(args.props, args.opts)


config = pulumi.Config()
namespace = k8s.core.v1.Namespace(
    "namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=config.require("namespace"),
    ),
)

scylla = k8s.helm.v4.Chart(
    "scylla",
    chart="scylla",
    namespace=namespace.metadata.name,
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
                        "cpu": "2",
                        "memory": "4Gi",
                    },
                    "requests": {
                        "cpu": "2",
                        "memory": "4Gi",
                    },
                },
            }
        ],
    },
    opts=pulumi.ResourceOptions(transforms=[annotate_scylla_cluster]),
)
