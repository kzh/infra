import * as kubernetes from "@pulumi/kubernetes";

const namespace = new kubernetes.core.v1.Namespace("logging", {
    metadata: {
        name: "logging",
    }
});

const logs = new kubernetes.helm.v3.Release("victoria-logs", {
    chart: "victoria-logs-single",
    namespace: namespace.metadata.name,
    repositoryOpts: {
        repo: "https://victoriametrics.github.io/helm-charts/",
    },
    values: {
        "fluent-bit": {
            enabled: true,
        },
        server: {
            persistentVolume: {
                enabled: true,
                size: "20Gi"
            },
            service: {
                annotations: {
                    "tailscale.com/expose": "true",
                    "tailscale.com/hostname": "logs"
                },
            },
            statefulSet: {
                enabled: false
            },
        }
    },
    version: "0.3.7",
});