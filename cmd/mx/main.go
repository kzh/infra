package main

import (
	"github.com/kzh/infra-faust/cmd/mx/vault"
	"github.com/spf13/cobra"
)

func main() {
	cmd := &cobra.Command{
		Use: "mx",
	}
	cmd.AddCommand(vault.Cmd())

	if err := cmd.Execute(); err != nil {
		panic(err)
	}
}
