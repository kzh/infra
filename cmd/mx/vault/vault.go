package vault

import (
	"context"
	"fmt"
	"os"

	vault "github.com/hashicorp/vault/api"
	"github.com/pulumi/pulumi/sdk/v3/go/auto"
	"github.com/pulumi/pulumi/sdk/v3/go/auto/optdestroy"
	"github.com/pulumi/pulumi/sdk/v3/go/auto/optup"
	"github.com/spf13/cobra"
)

func Cmd() *cobra.Command {
	cmd := &cobra.Command{
		Use: "vault",
	}
	cmd.AddCommand(
		up,
		destroy,
	)
	return cmd
}

var up = &cobra.Command{
	Use: "up",
	Run: func(cmd *cobra.Command, args []string) {
		ctx := context.Background()
		stack, err := auto.UpsertStackLocalSource(ctx, "vault", "/home/kevin/Code/Repos/github.com/kzh/infra-faust/pulumi/vault")
		if err != nil {
			panic(err)
		}

		stdout := optup.ProgressStreams(os.Stdout)
		_, err = stack.Up(ctx, stdout)
		if err != nil {
			panic(err)
		}

		vc, err := NewVaultClient()
		if err != nil {
			panic(err)
		}

		status, err := vc.Sys().SealStatus()
		if err != nil {
			panic(err)
		}

		if !status.Initialized {
			resp, err := vc.Sys().Init(&vault.InitRequest{
				SecretShares:    3,
				SecretThreshold: 2,
			})
			if err != nil {
				panic(err)
			}
			if err = SaveVaultCredentials(resp); err != nil {
				panic(err)
			}
		}

		fmt.Printf("%#v\n", status)
	},
}

var destroy = &cobra.Command{
	Use: "destroy",
	Run: func(cmd *cobra.Command, args []string) {
		ctx := context.Background()
		stack, err := auto.UpsertStackLocalSource(ctx, "vault", "/home/kevin/Code/Repos/github.com/kzh/infra-faust/pulumi/vault")
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
