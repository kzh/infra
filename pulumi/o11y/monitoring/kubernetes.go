package main

import (
	"github.com/pulumi/pulumi-kubernetes/sdk/v3/go/kubernetes/helm/v3"
	"github.com/pulumi/pulumi/sdk/v3/go/pulumi"
)

func DeployKubernetesMonitoring(ctx *pulumi.Context) error {
	// if err := NewKubernetesMetricsServer(ctx); err != nil {
	// 	return err
	// }
	//	if err := NewKubernetesDashboard(ctx); err != nil {
	//		return err
	//	}
	return nil
}

func NewKubernetesMetricsServer(ctx *pulumi.Context) error {
	const (
		ResourceName = "metrics-server"
		Repository   = "https://kubernetes-sigs.github.io/metrics-server/"
		Chart        = "metrics-server"
		ChartVersion = "3.7.0"

		Namespace = "kube-system"
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

func NewKubernetesDashboard(ctx *pulumi.Context) error {
	const (
		ResourceName = "kubernetes-dashboard"
		Repository   = "https://kubernetes.github.io/dashboard/"
		Chart        = "kubernetes-dashboard"
		ChartVersion = "7.3.2"

		Namespace = "monitoring"
	)

	_, err := helm.NewChart(ctx, ResourceName, helm.ChartArgs{
		Namespace: pulumi.String(Namespace),
		Chart:     pulumi.String(Chart),
		Version:   pulumi.String(ChartVersion),
		FetchArgs: helm.FetchArgs{
			Repo: pulumi.String(Repository),
		},
		Values: pulumi.Map{
			"rbac": pulumi.Map{
				"clusterReadOnlyRole": pulumi.Bool(true),
			},
			"service": pulumi.Map{
				"externalPort": pulumi.Int(80),
			},
			"protocolHttp": pulumi.Bool(true),
			"metricsScraper": pulumi.Map{
				"enabled": pulumi.Bool(true),
			},
		},
	})
	return err
}
