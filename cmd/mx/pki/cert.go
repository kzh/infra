package pki

import (
	vault "github.com/hashicorp/vault/api"
	"github.com/spf13/cobra"
	"os"
)

var rootCa = &cobra.Command{
	Use: "root-ca",
	Run: func(cmd *cobra.Command, args []string) {
		config := vault.DefaultConfig()
		vc, err := vault.NewClient(config)
		if err != nil {
			panic(err)
		}

		secret, err := vc.Logical().Read("pki/cert/ca")
		if err != nil {
			panic(err)
		}

		err = os.WriteFile("root_ca.crt", []byte(secret.Data["certificate"].(string)), 0664)
		if err != nil {
			panic(err)
		}
	},
}

var genClientCert = &cobra.Command{
	Use: "gen-client-cert",
	Run: func(cmd *cobra.Command, args []string) {
		config := vault.DefaultConfig()
		vc, err := vault.NewClient(config)
		if err != nil {
			panic(err)
		}

		secret, err := vc.Logical().Write("pki_int/issue/kevin", map[string]interface{}{
			"common_name": "kevin",
		})
		if err != nil {
			panic(err)
		}

		if err := writeCertificate("kevin", secret); err != nil {
			panic(err)
		}
	},
}

var genServerCert = &cobra.Command{
	Use: "gen-server-cert",
	Run: func(cmd *cobra.Command, args []string) {
		config := vault.DefaultConfig()
		vc, err := vault.NewClient(config)
		if err != nil {
			panic(err)
		}

		secret, err := vc.Logical().Write("pki_int/issue/internal", map[string]interface{}{
			"common_name": "*.faust.dev",
		})
		if err != nil {
			panic(err)
		}

		if err := writeCertificate("faust", secret); err != nil {
			panic(err)
		}
	},
}

func writeCertificate(name string, secret *vault.Secret) error {
	err := os.WriteFile(name+".crt", []byte(secret.Data["certificate"].(string)), 0644)
	if err != nil {
		return err
	}

	err = os.WriteFile(name+".key", []byte(secret.Data["private_key"].(string)), 0644)
	if err != nil {
		return err
	}

	err = os.WriteFile("issuing_ca.crt", []byte(secret.Data["issuing_ca"].(string)), 0644)
	if err != nil {
		return err
	}

	return nil
}
