package main

import (
	"github.com/pulumi/pulumi-kubernetes/sdk/v3/go/kubernetes/helm/v3"
	"github.com/pulumi/pulumi/sdk/v3/go/pulumi"
)

func NewMetricsServer(ctx *pulumi.Context) error {
	const (
		ResourceName = "metrics-server"
		Repository   = "https://kubernetes-sigs.github.io/metrics-server/"
		Chart        = "metrics-server"
		ChartVersion = "3.7.0"

		Namespace = "kube-system"
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
