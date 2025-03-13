package main

import (
	"github.com/pulumi/pulumi/sdk/v3/go/pulumi"
)

func main() {
	pulumi.Run(func(ctx *pulumi.Context) error {
		chart, err := DeployPrometheusStackCRDs(ctx)
		if err != nil {
			return err
		}

		return DeployPrometheusStack(ctx, chart)
	})
}
