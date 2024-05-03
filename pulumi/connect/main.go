package main

import (
	corev1 "github.com/pulumi/pulumi-kubernetes/sdk/v3/go/kubernetes/core/v1"
	"github.com/pulumi/pulumi-kubernetes/sdk/v3/go/kubernetes/helm/v3"
	metav1 "github.com/pulumi/pulumi-kubernetes/sdk/v3/go/kubernetes/meta/v1"
	"github.com/pulumi/pulumi/sdk/v3/go/pulumi"
	"github.com/pulumi/pulumi/sdk/v3/go/pulumi/config"
)

func main() {
	pulumi.Run(func(ctx *pulumi.Context) error {
		const (
			ResourceName = "connect"
			Namespace    = "connect"

			Repository   = "https://1password.github.io/connect-helm-charts"
			Chart        = "connect"
			ChartVersion = "1.15.0"
		)

		namespace, err := corev1.NewNamespace(ctx, Namespace, &corev1.NamespaceArgs{
			Metadata: metav1.ObjectMetaArgs{
				Name: pulumi.String(Namespace),
			},
		})

		cfg := config.New(ctx, "")
		_, err = helm.NewChart(ctx, ResourceName, helm.ChartArgs{
			Namespace: pulumi.String(Namespace),
			Chart:     pulumi.String(Chart),
			Version:   pulumi.String(ChartVersion),
			FetchArgs: helm.FetchArgs{
				Repo: pulumi.String(Repository),
			},
			Values: pulumi.Map{
				"connect": pulumi.Map{
					"serviceType": pulumi.String("ClusterIP"),
					"credentials": cfg.RequireSecret("CONNECT_CREDENTIALS"),
					"serviceAnnotations": pulumi.Map{
						"tailscale.com/expose":   pulumi.String("true"),
						"tailscale.com/hostname": pulumi.String("onepassword-connect"),
					},
				},
			},
		}, pulumi.DependsOn([]pulumi.Resource{namespace}))
		if err != nil {
			return err
		}

		return nil
	})
}
