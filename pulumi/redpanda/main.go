package main

import (
	corev1 "github.com/pulumi/pulumi-kubernetes/sdk/v4/go/kubernetes/core/v1"
	helmv3 "github.com/pulumi/pulumi-kubernetes/sdk/v4/go/kubernetes/helm/v3"
	metav1 "github.com/pulumi/pulumi-kubernetes/sdk/v4/go/kubernetes/meta/v1"
	"github.com/pulumi/pulumi/sdk/v3/go/pulumi"
)

func main() {
	pulumi.Run(func(ctx *pulumi.Context) error {
		namespace, err := corev1.NewNamespace(ctx, "namespace", &corev1.NamespaceArgs{
			Metadata: &metav1.ObjectMetaArgs{
				Labels: pulumi.StringMap{
					"app": pulumi.String("redpanda"),
				},
				Name: pulumi.String("redpanda"),
			},
		})
		if err != nil {
			return err
		}

		redpanda, err := helmv3.NewRelease(ctx, "redpanda", &helmv3.ReleaseArgs{
			Chart:     pulumi.String("operator"),
			Namespace: namespace.Metadata.Name(),
			RepositoryOpts: &helmv3.RepositoryOptsArgs{
				Repo: pulumi.String("https://charts.redpanda.com"),
			},
			SkipCrds: pulumi.Bool(true),
			Values: pulumi.Map{
				"additionalCmdFlags": pulumi.StringArray{pulumi.String("--enable-helm-controllers=false")},
			},
			Version: pulumi.String("0.4.34"),
		})
		if err != nil {
			return err
		}

		ctx.Export("name", redpanda.Name)
		return nil
	})
}
