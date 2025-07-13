import * as kubernetes from "@pulumi/kubernetes";

const namespace = new kubernetes.core.v1.Namespace("namespace", {
    metadata: {
        name: "clickhouse",
    }
});

const clickhouse = new kubernetes.helm.v3.Release("clickhouse", {
    chart: "oci://registry-1.docker.io/bitnamicharts/clickhouse",
    version: "8.0.5",
    namespace: namespace.metadata.name,
    values: {
        auth: {
            username: "admin"
        },
        zookeeper: {
            enabled: false,
        },
        ingress: {
            enabled: false,
        },
        service: {
            annotations: {
                "tailscale.com/expose": "true",
                "tailscale.com/hostname": "clickhouse"
            }
        },
        persistence: {
            storageClass: "local-path",
            size: "100Gi",
        },
        resourcesPreset: "none",
        shards: 1,
        replicaCount: 1
    }
});
