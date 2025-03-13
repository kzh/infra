package main

import (
	"github.com/pulumi/pulumi-kubernetes/sdk/v3/go/kubernetes/helm/v3"
	"github.com/pulumi/pulumi/sdk/v3/go/pulumi"
)

func DeployPrometheusStackCRDs(ctx *pulumi.Context) (*helm.Chart, error) {
	const (
		ResourceName = "prometheus-operator-crds"
		Repository   = "https://prometheus-community.github.io/helm-charts"
		Chart        = "prometheus-operator-crds"
		ChartVersion = "18.0.1"

		Namespace = "monitoring"
	)

	chart, err := helm.NewChart(ctx, ResourceName, helm.ChartArgs{
		Namespace: pulumi.String(Namespace),
		Chart:     pulumi.String(Chart),
		Version:   pulumi.String(ChartVersion),
		FetchArgs: helm.FetchArgs{
			Repo: pulumi.String(Repository),
		},
	})
	return chart, err
}

func DeployPrometheusStack(ctx *pulumi.Context, chart *helm.Chart) error {
	const (
		ResourceName = "kube-prometheus-stack"
		Repository   = "https://prometheus-community.github.io/helm-charts"
		Chart        = "kube-prometheus-stack"
		ChartVersion = "69.8.2"

		Namespace = "monitoring"
	)

	values := pulumi.Map{
		"prometheus": pulumi.Map{
			"prometheusSpec": pulumi.Map{
				"storageSpec": pulumi.Map{
					"volumeClaimTemplate": pulumi.Map{
						"spec": pulumi.Map{
							"storageClassName": pulumi.String("local-path"),
							"accessModes":      pulumi.StringArray{pulumi.String("ReadWriteOnce")},
							"resources": pulumi.Map{
								"requests": pulumi.Map{
									"storage": pulumi.String("100Gi"),
								},
							},
						},
					},
				},
				"retention":      pulumi.String("90d"),
				"enableAdminAPI": pulumi.Bool(true),
			},
		},
		"grafana": pulumi.Map{
			"persistence": pulumi.Map{
				"enabled": pulumi.Bool(true),
			},
			"ingress": pulumi.Map{
				"enabled":          pulumi.Bool(true),
				"ingressClassName": pulumi.String("tailscale"),
				"hosts":            pulumi.StringArray{pulumi.String("grafana")},
				"tls": pulumi.MapArray{
					pulumi.Map{
						"hosts": pulumi.StringArray{pulumi.String("grafana")},
					},
				},
			},
		},
		"crds": pulumi.Map{
			"enabled": pulumi.Bool(false),
		},
	}

	_, err := helm.NewChart(ctx, ResourceName, helm.ChartArgs{
		Namespace: pulumi.String(Namespace),
		Chart:     pulumi.String(Chart),
		Version:   pulumi.String(ChartVersion),
		FetchArgs: helm.FetchArgs{
			Repo: pulumi.String(Repository),
		},
		Values: values,
	}, pulumi.DependsOn([]pulumi.Resource{chart}))
	return err
}
