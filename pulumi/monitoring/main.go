package main

import (
	"github.com/pulumi/pulumi/sdk/v3/go/pulumi"
)

func main() {
	pulumi.Run(func(ctx *pulumi.Context) error {
		deployFuncs := []pulumi.RunFunc{
			DeployKubernetesMonitoring,
			DeployEFKStack,
		}

		for _, fn := range deployFuncs {
			if err := fn(ctx); err != nil {
				return err
			}
		}

		return nil
	})
}
