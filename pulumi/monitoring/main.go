package main

import (
	"github.com/pulumi/pulumi/sdk/v3/go/pulumi"
)

func main() {
	pulumi.Run(func(ctx *pulumi.Context) error {
		runFuncs := []pulumi.RunFunc{
			DeployKubernetesMonitoring,
			DeployPrometheusStack,
		}

		for _, fn := range runFuncs {
			if err := fn(ctx); err != nil {
				return err
			}
		}

		return nil
	})
}
