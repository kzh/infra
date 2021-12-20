package main

import (
	"github.com/pulumi/pulumi-kubernetes/sdk/v3/go/kubernetes/helm/v3"
	"github.com/pulumi/pulumi/sdk/v3/go/pulumi"
)

func DeployPrometheusStack(ctx *pulumi.Context) error {
	const (
		ResourceName = "kube-prometheus-stack"
		Repository   = "https://prometheus-community.github.io/helm-charts"
		Chart        = "kube-prometheus-stack"
		ChartVersion = "24.0.1"

		Namespace = "monitoring"
	)

	_, err := helm.NewRelease(ctx, ResourceName, &helm.ReleaseArgs{
		Namespace: pulumi.String(Namespace),
		Name:      pulumi.String(Chart),
		Chart:     pulumi.String(Chart),
		Version:   pulumi.String(ChartVersion),
		RepositoryOpts: helm.RepositoryOptsArgs{
			Repo: pulumi.String(Repository),
		},
		Atomic: pulumi.Bool(true),
	})
	return err
}
