package k8s

import (
	"fmt"
	"github.com/kzh/infra-faust/pkg/k8s"
	"github.com/spf13/cobra"
)

func Cmd() *cobra.Command {
	cmd := &cobra.Command{
		Use: "k8s",
	}
	cmd.AddCommand(ca)
	return cmd
}

var ca = &cobra.Command{
	Use: "ca",
	Run: func(cmd *cobra.Command, args []string) {
		fmt.Print(string(k8s.CA()))
	},
}
