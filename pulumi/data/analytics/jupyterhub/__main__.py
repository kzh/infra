import pulumi
import pulumi_kubernetes as k8s

NAMESPACE = "jhub"
HOSTNAME = "jupyterhub"

namespace = k8s.core.v1.Namespace(
    "jupyterhub-namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(name=NAMESPACE),
)


def ignore_hub_db_pvc_metadata_drift(
    args: pulumi.ResourceTransformArgs,
) -> pulumi.ResourceTransformResult | None:
    if (
        args.type_ == "kubernetes:core/v1:PersistentVolumeClaim"
        and isinstance(args.props, dict)
        and (args.props.get("metadata") or {}).get("name") == "hub-db-dir"
    ):
        opts = pulumi.ResourceOptions.merge(pulumi.ResourceOptions(), args.opts)
        ignore_changes = list(opts.ignore_changes or [])
        if "metadata" not in ignore_changes:
            ignore_changes.append("metadata")
        opts.ignore_changes = ignore_changes
        return pulumi.ResourceTransformResult(props=args.props, opts=opts)
    return None

jupyterhub_chart = k8s.helm.v4.Chart(
    "jupyterhub",
    chart="jupyterhub",
    namespace=NAMESPACE,
    version="4.3.2",
    repository_opts=k8s.helm.v4.RepositoryOptsArgs(
        repo="https://hub.jupyter.org/helm-chart/",
    ),
    values={
        "ingress": {
            "enabled": True,
            "ingressClassName": "tailscale",
            "hosts": [HOSTNAME],
            "tls": [{"hosts": [HOSTNAME]}],
        },
        "proxy": {
            "service": {
                "type": "ClusterIP",
            }
        },
        "hub": {
            "db": {
                "type": "sqlite-pvc",
                "pvc": {
                    "storage": "5Gi",
                    "accessModes": ["ReadWriteOnce"],
                },
            },
            "networkPolicy": {"enabled": False},
        },
        "singleuser": {
            "startTimeout": 600,
            "image": {
                "name": "ghcr.io/kzh/jupyter",
                "tag": "py312-amd64",
                "pullPolicy": "Always",
            },
            "storage": {
                "type": "dynamic",
                "capacity": "60Gi",
                "dynamic": {
                    "storageAccessModes": ["ReadWriteOnce"],
                },
            }
        },
        "cull": {
            "enabled": False,
        },
        "scheduling": {
            "userScheduler": {"enabled": False},
            "userPlaceholder": {"enabled": False},
            "podPriority": {"enabled": False},
            "corePods": {"nodeAffinity": {"matchNodePurpose": "ignore"}},
            "userPods": {"nodeAffinity": {"matchNodePurpose": "ignore"}},
        },
    },
    opts=pulumi.ResourceOptions(
        depends_on=[namespace],
        transforms=[ignore_hub_db_pvc_metadata_drift],
    ),
)
