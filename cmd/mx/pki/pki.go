package pki

import (
	"context"
	"github.com/kzh/infra-faust/pkg/k8s"
	"github.com/pulumi/pulumi/sdk/v3/go/auto"
	"github.com/pulumi/pulumi/sdk/v3/go/auto/optdestroy"
	"github.com/pulumi/pulumi/sdk/v3/go/auto/optup"
	"github.com/spf13/cobra"
	"os"
)

func Cmd() *cobra.Command {
	cmd := &cobra.Command{
		Use: "pki",
	}
	cmd.AddCommand(up)
	cmd.AddCommand(destroy)
	return cmd
}

var up = &cobra.Command{
	Use: "up",
	Run: func(cmd *cobra.Command, args []string) {
		stop := make(chan struct{})
		ready := make(chan struct{})

		go func() {
			err := k8s.PortForward(
				"vault",
				"vault",
				[]string{"8200:8200"},
				stop,
				ready,
			)
			if err != nil {
				panic(err)
			}
		}()
		defer func() {
			stop <- struct{}{}
		}()

		<-ready

		ctx := context.Background()
		stack, err := auto.UpsertStackLocalSource(ctx, "pki", "/root/Code/Repos/infra-faust/pulumi/pki")
		if err != nil {
			panic(err)
		}

		stdout := optup.ProgressStreams(os.Stdout)
		_, err = stack.Up(ctx, stdout)
		if err != nil {
			panic(err)
		}
	},
}

var destroy = &cobra.Command{
	Use: "destroy",
	Run: func(cmd *cobra.Command, args []string) {
		stop := make(chan struct{})
		ready := make(chan struct{})

		go func() {
			err := k8s.PortForward(
				"vault",
				"vault",
				[]string{"8200:8200"},
				stop,
				ready,
			)
			if err != nil {
				panic(err)
			}
		}()
		defer func() {
			stop <- struct{}{}
		}()

		<-ready

		ctx := context.Background()
		stack, err := auto.UpsertStackLocalSource(ctx, "pki", "/root/Code/Repos/infra-faust/pulumi/pki")
		if err != nil {
			panic(err)
		}

		stdout := optdestroy.ProgressStreams(os.Stdout)
		_, err = stack.Destroy(ctx, stdout)
		if err != nil {
			panic(err)
		}
	},
}
