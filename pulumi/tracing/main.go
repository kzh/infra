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
			"opentelemetry-collector",
			pulumi.ID("tracing/opentelemetry-collector"),
			nil,
		)
		if err != nil {
			return err
		}

		_, err = services.NewTailscaleProxy(
			ctx,
			"opentelemetry-collector",
			"tracing",
			service.Spec.ClusterIP(),
		)
		return err
	})
}
