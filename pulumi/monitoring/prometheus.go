package main

import (
	"github.com/pulumi/pulumi-kubernetes/sdk/v3/go/kubernetes/helm/v3"
	"github.com/pulumi/pulumi/sdk/v3/go/pulumi"
)

func DeployPrometheusStackCRDs(ctx *pulumi.Context) error {
	const (
		ResourceName = "prometheus-operator-crds"
		Repository   = "https://prometheus-community.github.io/helm-charts"
		Chart        = "prometheus-operator-crds"
		ChartVersion = "11.0.0"

		Namespace = "monitoring"
	)

	_, err := helm.NewChart(ctx, ResourceName, helm.ChartArgs{
		Namespace: pulumi.String(Namespace),
		Chart:     pulumi.String(Chart),
		Version:   pulumi.String(ChartVersion),
		FetchArgs: helm.FetchArgs{
			Repo: pulumi.String(Repository),
		},
	})
	return err
}

func DeployPrometheusStack(ctx *pulumi.Context) error {
	const (
		ResourceName = "kube-prometheus-stack"
		Repository   = "https://prometheus-community.github.io/helm-charts"
		Chart        = "kube-prometheus-stack"
		ChartVersion = "58.1.3"

		Namespace = "monitoring"
	)

	values := pulumi.Map{
		"prometheus": pulumi.Map{
			"prometheusSpec": pulumi.Map{
				"storageSpec": pulumi.Map{
					"volumeClaimTemplate": pulumi.Map{
						"spec": pulumi.Map{
							"storageClassName": pulumi.String("rook-ceph-block"),
							"accessModes":      pulumi.StringArray{pulumi.String("ReadWriteOnce")},
							"resources": pulumi.Map{
								"requests": pulumi.Map{
									"storage": pulumi.String("20Gi"),
								},
							},
						},
					},
				},
			},
		},
		"grafana": pulumi.Map{
			"persistence": pulumi.Map{
				"enabled": pulumi.Bool(true),
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
	})
	return err
}
