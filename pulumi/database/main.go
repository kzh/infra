package main

import (
	"github.com/kzh/infra-faust/pkg/services"
	corev1 "github.com/pulumi/pulumi-kubernetes/sdk/v3/go/kubernetes/core/v1"
	"github.com/pulumi/pulumi/sdk/v3/go/pulumi"
)

func main() {
	pulumi.Run(func(ctx *pulumi.Context) error {
		service, err := corev1.GetService(
			ctx,
			"cockroachdb-public",
			pulumi.ID("crdb/cockroachdb-public"),
			nil,
		)
		if err != nil {
			return err
		}
		services.NewTailscaleProxy(ctx, service)

		return nil
	})
}
