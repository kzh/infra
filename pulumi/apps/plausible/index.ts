import * as pulumi from "@pulumi/pulumi";
import * as kubernetes from "@pulumi/kubernetes";
import * as cloudflare from "@pulumi/cloudflare";

import {Buffer} from "buffer";

const decode = (str: string): string => Buffer.from(str, 'base64').toString('binary');

const config = new pulumi.Config();
const namespace = new kubernetes.core.v1.Namespace("plausible", {
    metadata: {
        name: "plausible",
    }
});

const clickhouse = new kubernetes.helm.v3.Release("clickhouse", {
    chart: "oci://registry-1.docker.io/bitnamicharts/clickhouse",
    version: "6.0.3",
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
        resourcesPreset: "none",
        shards: 1,
        replicaCount: 1
    }
});

const postgres = new kubernetes.helm.v3.Release("postgres", {
    chart: "oci://registry-1.docker.io/bitnamicharts/postgresql",
    version: "15.2.8",
    namespace: namespace.metadata.name,
    values: {
        auth: {
            database: "plausible_db"
        }
    }
});

const databaseUrl = pulumi.all([namespace.metadata.name, postgres.status.name]).apply(([namespace, chart]) => {
    const secret = kubernetes.core.v1.Secret.get("postgres-secret", `${namespace}/${chart}-postgresql`, {});
    return secret.data.apply(data => `postgres://postgres:${decode(data['postgres-password'])}@${chart}-postgresql:5432/plausible_db`);
});

const clickhouseUrl = pulumi.all([namespace.metadata.name, clickhouse.status.name]).apply(([namespace, chart]) => {
    const secret = kubernetes.core.v1.Secret.get("clickhouse-secret", `${namespace}/${chart}`, {});
    return secret.data.apply(data => `http://admin:${decode(data['admin-password'])}@${chart}:8123/default`);
});

const secret = new kubernetes.core.v1.Secret("plausible-secret", {
    metadata: {
        name: "plausible",
        namespace: namespace.metadata.name,
    },
    stringData: {
        "DATABASE_URL": databaseUrl,
        "CLICKHOUSE_DATABASE_URL": clickhouseUrl,
        "SECRET_KEY_BASE": config.require("SECRET_KEY_BASE"),
        "TOTP_VAULT_KEY": config.require("TOTP_VAULT_KEY"),
    },
});

const labels = {
    app: "plausible",
}

const plausible = new kubernetes.apps.v1.Deployment("plausible", {
    metadata: {
        name: "plausible",
        namespace: namespace.metadata.name,
        labels: labels,
    },
    spec: {
        selector: {
            matchLabels: labels,
        },
        template: {
            metadata: {
                labels: labels,
            },
            spec: {
                containers: [{
                    name: "analytics",
                    image: config.require("IMAGE"),
                    ports: [{
                        containerPort: 8000,
                    }],
                    command: [
                        "sh", "-c",
                        "sleep 10 && /entrypoint.sh db createdb && /entrypoint.sh db migrate && /entrypoint.sh run"
                    ],
                    env: [{
                        name: "BASE_URL",
                        value: "https://" + config.require("BASE_URL")
                    }],
                    envFrom: [{
                        secretRef: {
                            name: secret.metadata.name,
                        }
                    }]
                }]
            }
        }
    }
});

const service = new kubernetes.core.v1.Service("plausible-service", {
    metadata: {
        name: "plausible",
        namespace: namespace.metadata.name,
        labels: labels,
    },
    spec: {
        type: "ClusterIP",
        ports: [{
            port: 8000,
            targetPort: 8000,
        }],
        selector: labels,
    }
});

const tunnel = new cloudflare.Tunnel("plausible-tunnel", {
    accountId: config.require("CLOUDFLARE_ACCOUNT_ID"),
    name: "plausible",
    secret: config.require("CLOUDFLARE_TUNNEL_SECRET"),
});

const tunnelCredentials = tunnel.id.apply(id => {
    const credentials = {
        AccountTag: config.require("CLOUDFLARE_ACCOUNT_ID"),
        TunnelSecret: config.require("CLOUDFLARE_TUNNEL_SECRET"),
        TunnelID: id
    };
    return JSON.stringify(credentials);
});

const tunnelSecret = new kubernetes.core.v1.Secret("plausible-tunnel-secret", {
    metadata: {
        name: "plausible-cloudflared",
        namespace: namespace.metadata.name,
    },
    stringData: {
        "credentials.json": tunnelCredentials
    }
});

const tunnelConfig = new kubernetes.core.v1.ConfigMap("plausible-tunnel-config", {
    metadata: {
        name: "plausible-cloudflared",
        namespace: namespace.metadata.name,
    },
    data: {
        "config.yaml": `
    # Name of the tunnel you want to run
    tunnel: plausible
    credentials-file: /etc/cloudflared/creds/credentials.json
    metrics: 0.0.0.0:2000
    no-autoupdate: true
    ingress:
    - hostname: ${config.require("BASE_URL")}
      service: http://plausible:8000
    - service: http_status:404
        `
    }
});

const cloudflared = new kubernetes.apps.v1.Deployment("cloudflared", {
    metadata: {
        name: "cloudflared",
        namespace: namespace.metadata.name,
    },
    spec: {
        selector: {
            matchLabels: {
                app: "cloudflared",
            },
        },
        template: {
            metadata: {
                labels: {
                    app: "cloudflared",
                },
            },
            spec: {
                containers: [{
                    name: "cloudflared",
                    image: "cloudflare/cloudflared:2024.4.1",
                    args: [
                        "tunnel",
                        "--config",
                        "/etc/cloudflared/config/config.yaml",
                        "run"
                    ],
                    livenessProbe: {
                        httpGet: {
                            path: "/ready",
                            port: 2000,
                        },
                        failureThreshold: 1,
                        initialDelaySeconds: 10,
                        periodSeconds: 10,
                    },
                    volumeMounts: [{
                        name: "config",
                        mountPath: "/etc/cloudflared/config",
                        readOnly: true,
                    }, {
                        name: "credentials",
                        mountPath: "/etc/cloudflared/creds",
                        readOnly: true,
                    }],
                }],
                volumes: [{
                    name: "config",
                    configMap: {
                        name: tunnelConfig.metadata.name,
                        items: [{
                            key: "config.yaml",
                            path: "config.yaml",
                        }]
                    },
                }, {
                    name: "credentials",
                    secret: {
                        secretName: tunnelSecret.metadata.name,
                    },
                }]
            }
        }
    }
});

const cname = tunnel.id.apply(id => `${id}.cfargotunnel.com`);

const dns = new cloudflare.Record("plausible-dns", {
    zoneId: config.require("CLOUDFLARE_ZONE_ID"),
    name: config.require("BASE_URL"),
    type: "CNAME",
    value: cname,
    proxied: true
})