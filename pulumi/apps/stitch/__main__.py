import pulumi
import pulumi_kubernetes as k8s
import pulumi_postgresql as pg
import pulumi_random as random

config = pulumi.Config()
pg_config = pulumi.Config("postgresql")

password = random.RandomPassword(
    "stitch-password",
    length=32,
    special=False,
)

role = pg.Role(
    "stitch-role",
    name="stitch",
    password=password.result,
    login=True,
)

database = pg.Database(
    "stitch-database",
    name="stitch",
    owner=role.name,
)

db_url = pulumi.Output.concat(
    "postgres://",
    role.name,
    ":",
    password.result,
    "@",
    config.require("POSTGRES_HOST"),
    ".",
    config.require("k8s_namespace"),
    ":",
    pg_config.require("port"),
    "/",
    database.name,
    "?sslmode=disable",
)

namespace = config.get("namespace", "stitch")
labels = {
    "app": "stitch",
}

namespace = k8s.core.v1.Namespace(
    "namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        labels=labels,
        name=namespace,
    ),
)

path = config.get("path", "stitch")
chart = k8s.helm.v4.Chart(
    "stitch",
    k8s.helm.v4.ChartArgs(
        chart=path,
        namespace=namespace.metadata.name,
        values={
            "config": {
                "server": {
                    "port": config.require("PORT"),
                },
                "database": {
                    "url": db_url,
                },
                "twitch": {
                    "clientId": config.require("TWITCH_CLIENT_ID"),
                    "clientSecret": config.require("TWITCH_CLIENT_SECRET"),
                    "webhookUrl": config.require("WEBHOOK_URL"),
                },
                "webhook": {
                    "secret": config.require("WEBHOOK_SECRET"),
                    "port": config.require("WEBHOOK_PORT"),
                    "url": config.require("WEBHOOK_URL"),
                },
                "discord": {
                    "token": config.require("DISCORD_TOKEN"),
                    "channel": config.require("DISCORD_CHANNEL"),
                },
            },
            "ingress": {
                "enabled": True,
                "className": "cloudflare-tunnel",
                "host": config.require("WEBHOOK_URL"),
            },
            "service": {
                "annotations": {
                    "tailscale.com/expose": "true",
                    "tailscale.com/hostname": "stitch",
                },
            },
        },
    ),
    opts=pulumi.ResourceOptions(depends_on=[role, database]),
)
