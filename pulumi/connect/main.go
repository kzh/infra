package main

import (
	"github.com/kzh/infra-faust/pkg/services"
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
			ChartVersion = "1.7.0"
		)

		namespace, err := corev1.NewNamespace(ctx, Namespace, &corev1.NamespaceArgs{
			Metadata: metav1.ObjectMetaArgs{
				Name: pulumi.String(Namespace),
			},
		})

		cfg := config.New(ctx, "")
		chart, err := helm.NewChart(ctx, ResourceName, helm.ChartArgs{
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
				},
			},
		}, pulumi.DependsOn([]pulumi.Resource{namespace}))
		if err != nil {
			return err
		}

		output := chart.GetResource("v1/Service", "onepassword-connect", Namespace).ApplyT(
			func(r interface{}) (pulumi.StringPtrOutput, error) {
				return r.(*corev1.Service).Spec.ClusterIP(), nil
			},
		).(pulumi.AnyOutput)
		clusterIP := pulumi.StringPtrOutput{OutputState: output.OutputState}

		_, err = services.NewTailscaleProxy(ctx, "onepassword-connect", "connect", clusterIP)
		if err != nil {
			return err
		}

		return nil
	})
}
