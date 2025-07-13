import * as pulumi from "@pulumi/pulumi";
import * as k8s from "@pulumi/kubernetes";

const namespace = new k8s.core.v1.Namespace("tailscale", {
	metadata: {
		name: "tailscale",
	},
});

const config = new pulumi.Config();
const operator = new k8s.helm.v3.Chart(
	"tailscale-operator",
	{
		chart: "tailscale-operator",
		namespace: "tailscale",
		version: "1.84.3",
		fetchOpts: {
			repo: "https://pkgs.tailscale.com/helmcharts",
		},
		values: {
			oauth: {
				clientId: config.require("TS_CLIENT_ID"),
				clientSecret: config.requireSecret("TS_CLIENT_SECRET"),
			},
			apiServerProxyConfig: {
				mode: "true",
			},
		},
	},
	{ dependsOn: [namespace] },
);
