from pathlib import Path

import pulumi
import pulumi_kubernetes as k8s

config = pulumi.Config()

namespace_name = config.get("namespace") or "code-server"
chart_path = config.get("path") or str(Path(__file__).resolve().parent / "chart")
hostname = config.get("hostname") or "code-server"
persistence_size = config.get("persistence_size") or "20Gi"
persistence_storage_class = config.get("persistence_storage_class")
image_tag = config.get("image_tag")

labels = {
    "app": "code-server",
}

code_server_namespace = k8s.core.v1.Namespace(
    "code-server-namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=namespace_name,
        labels=labels,
    ),
)

chart_values: dict[str, object] = {
    "replicaCount": 1,
    "service": {
        "type": "ClusterIP",
        "port": 8080,
    },
    "ingress": {
        "enabled": True,
        "ingressClassName": "tailscale",
        "hosts": [
            {
                "host": hostname,
                "paths": ["/"],
            }
        ],
        "tls": [
            {
                "hosts": [hostname],
                "secretName": "",
            }
        ],
    },
    "persistence": {
        "enabled": True,
        "accessMode": "ReadWriteOnce",
        "size": persistence_size,
    },
    "image": {
        "pullPolicy": "IfNotPresent",
    },
    "extraArgs": ["--auth", "none"],
}

if persistence_storage_class:
    chart_values["persistence"]["storageClass"] = persistence_storage_class

if image_tag:
    chart_values["image"]["tag"] = image_tag

code_server_chart = k8s.helm.v4.Chart(
    "code-server",
    chart=chart_path,
    namespace=code_server_namespace.metadata.name,
    values=chart_values,
    opts=pulumi.ResourceOptions(depends_on=[code_server_namespace]),
)

pulumi.export("namespace", code_server_namespace.metadata.name)
pulumi.export("hostname", hostname)
pulumi.export("chart_path", chart_path)
pulumi.export("ingress_resource", "code-server")
pulumi.export("service_resource", "code-server")
