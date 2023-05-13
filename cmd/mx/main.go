package main

import (
	"github.com/kzh/infra-faust/cmd/mx/k8s"
	"github.com/kzh/infra-faust/cmd/mx/pki"
	"github.com/kzh/infra-faust/cmd/mx/vault"
	"github.com/spf13/cobra"
)

func main() {
	cmd := &cobra.Command{
		Use: "mx",
	}
	cmd.AddCommand(vault.Cmd())
	cmd.AddCommand(pki.Cmd())
	cmd.AddCommand(k8s.Cmd())

	if err := cmd.Execute(); err != nil {
		panic(err)
	}
}
