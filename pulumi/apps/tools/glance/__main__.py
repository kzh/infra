import pulumi
import pulumi_kubernetes as k8s

config = pulumi.Config()
namespace = k8s.core.v1.Namespace(
    "namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=config.require("namespace"),
    ),
)

labels = {
    "app": "glance",
}

pvc = k8s.core.v1.PersistentVolumeClaim(
    "glance-config-pvc",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="glance-config",
        namespace=namespace.metadata.apply(lambda m: m["name"]),
        labels=labels,
    ),
    spec=k8s.core.v1.PersistentVolumeClaimSpecArgs(
        access_modes=["ReadWriteOnce"],
        resources=k8s.core.v1.VolumeResourceRequirementsArgs(
            requests={"storage": "1Gi"}
        ),
    ),
)

configmap = k8s.core.v1.ConfigMap(
    "glance-config",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="glance-config",
        namespace=namespace.metadata.apply(lambda m: m["name"]),
        labels=labels,
    ),
    data={
        "glance.yml": """server:
  port: 8080

theme:
  light: false

pages:
  - name: Home
    columns:
      - size: full
        widgets:
          - type: clock
            hour-format: 24h
            timezones:
              - timezone: UTC
                label: UTC
"""
    },
)

deployment = k8s.apps.v1.Deployment(
    "glance",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="glance",
        namespace=namespace.metadata.apply(lambda m: m["name"]),
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
                        name="glance",
                        image="glanceapp/glance:latest",
                        ports=[
                            k8s.core.v1.ContainerPortArgs(
                                container_port=8080,
                                name="http",
                            ),
                        ],
                        volume_mounts=[
                            k8s.core.v1.VolumeMountArgs(
                                name="config",
                                mount_path="/app/config",
                            ),
                        ],
                        resources=k8s.core.v1.ResourceRequirementsArgs(
                            requests={
                                "memory": "64Mi",
                                "cpu": "50m",
                            },
                            limits={
                                "memory": "128Mi",
                                "cpu": "100m",
                            },
                        ),
                    ),
                ],
                init_containers=[
                    k8s.core.v1.ContainerArgs(
                        name="init-config",
                        image="busybox:1.35",
                        command=[
                            "sh",
                            "-c",
                            "if [ ! -f /app/config/glance.yml ]; then cp /tmp/glance.yml /app/config/glance.yml; fi",
                        ],
                        volume_mounts=[
                            k8s.core.v1.VolumeMountArgs(
                                name="config",
                                mount_path="/app/config",
                            ),
                            k8s.core.v1.VolumeMountArgs(
                                name="default-config",
                                mount_path="/tmp",
                            ),
                        ],
                    ),
                ],
                volumes=[
                    k8s.core.v1.VolumeArgs(
                        name="config",
                        persistent_volume_claim=k8s.core.v1.PersistentVolumeClaimVolumeSourceArgs(
                            claim_name=pvc.metadata.apply(lambda m: m["name"]),
                        ),
                    ),
                    k8s.core.v1.VolumeArgs(
                        name="default-config",
                        config_map=k8s.core.v1.ConfigMapVolumeSourceArgs(
                            name=configmap.metadata.apply(lambda m: m["name"]),
                        ),
                    ),
                ],
            ),
        ),
    ),
    opts=pulumi.ResourceOptions(depends_on=[pvc, configmap]),
)

service = k8s.core.v1.Service(
    "glance",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="glance",
        namespace=namespace.metadata.apply(lambda m: m["name"]),
        labels=labels,
    ),
    spec=k8s.core.v1.ServiceSpecArgs(
        selector=labels,
        ports=[
            k8s.core.v1.ServicePortArgs(
                name="http",
                port=8080,
                target_port=8080,
                protocol="TCP",
            ),
        ],
        type="ClusterIP",
    ),
    opts=pulumi.ResourceOptions(depends_on=[deployment]),
)

ingress = k8s.networking.v1.Ingress(
    "glance",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="glance",
        namespace=namespace.metadata.apply(lambda m: m["name"]),
        labels=labels,
    ),
    spec=k8s.networking.v1.IngressSpecArgs(
        ingress_class_name="tailscale",
        tls=[
            k8s.networking.v1.IngressTLSArgs(
                hosts=["glance"],
            ),
        ],
        rules=[
            k8s.networking.v1.IngressRuleArgs(
                host="glance",
                http=k8s.networking.v1.HTTPIngressRuleValueArgs(
                    paths=[
                        k8s.networking.v1.HTTPIngressPathArgs(
                            path="/",
                            path_type="Prefix",
                            backend=k8s.networking.v1.IngressBackendArgs(
                                service=k8s.networking.v1.IngressServiceBackendArgs(
                                    name=service.metadata.apply(lambda m: m["name"]),
                                    port=k8s.networking.v1.ServiceBackendPortArgs(
                                        number=8080,
                                    ),
                                ),
                            ),
                        ),
                    ],
                ),
            ),
        ],
    ),
    opts=pulumi.ResourceOptions(depends_on=[service]),
)