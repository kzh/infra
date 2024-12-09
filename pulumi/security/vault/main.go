package main

import (
	corev1 "github.com/pulumi/pulumi-kubernetes/sdk/v3/go/kubernetes/core/v1"
	"github.com/pulumi/pulumi-kubernetes/sdk/v3/go/kubernetes/helm/v3"
	metav1 "github.com/pulumi/pulumi-kubernetes/sdk/v3/go/kubernetes/meta/v1"
	"github.com/pulumi/pulumi/sdk/v3/go/pulumi"
)

const (
	ResourceName = "vault"
	Namespace    = "vault"
)

func main() {
	pulumi.Run(func(ctx *pulumi.Context) error {
		namespace, err := corev1.NewNamespace(ctx, Namespace, &corev1.NamespaceArgs{
			Metadata: metav1.ObjectMetaArgs{
				Name: pulumi.String(Namespace),
			},
		})
		if err != nil {
			return err
		}

		secret, err := SetupTLS(ctx, namespace)
		if err != nil {
			return err
		}

		_, err = NewVaultChart(ctx, secret)
		return err
	})
}

func NewVaultChart(ctx *pulumi.Context, secret *corev1.Secret) (*helm.Chart, error) {
	const (
		Repository   = "https://helm.releases.hashicorp.com"
		Chart        = "vault"
		ChartVersion = "0.28.0"
	)

	config := `
  ui = true

  listener "tcp" {
	address = "[::]:8200"
	cluster_address = "[::]:8201"
	tls_cert_file = "/etc/pki/vault/vault.crt"
	tls_key_file  = "/etc/pki/vault/vault.key"
	tls_client_ca_file = "/etc/pki/vault/vault.ca"
  }

  storage "file" {
	path = "/vault/data"
  }
`
	await := pulumi.Map{
		"pulumi.com/skipAwait": pulumi.String("true"),
	}

	return helm.NewChart(ctx, ResourceName, helm.ChartArgs{
		Namespace: pulumi.String(Namespace),
		Chart:     pulumi.String(Chart),
		Version:   pulumi.String(ChartVersion),
		FetchArgs: helm.FetchArgs{
			Repo: pulumi.String(Repository),
		},
		Values: pulumi.Map{
			"global": pulumi.Map{
				"tlsDisable": pulumi.Bool(false),
			},
			"injector": pulumi.Map{
				"enabled": pulumi.Bool(false),
			},
			"server": pulumi.Map{
				"extraEnvironmentVars": pulumi.Map{
					"VAULT_CACERT": pulumi.String("/etc/pki/vault/vault.ca"),
				},
				"annotations": await,
				"service": pulumi.Map{"annotations": pulumi.Map{
					"pulumi.com/skipAwait":   pulumi.String("true"),
					"tailscale.com/expose":   pulumi.String("true"),
					"tailscale.com/hostname": pulumi.String("vault"),
				}},
				"statefulSet": pulumi.Map{"annotations": await},
				"extraVolumes": pulumi.MapArray{
					pulumi.Map{
						"type": pulumi.String("secret"),
						"name": pulumi.String(ResourceName),
						"path": pulumi.String("/etc/pki"),
					},
				},
				"standalone": pulumi.Map{
					"enabled": pulumi.Bool(true),
					"config":  pulumi.String(config),
				},
			},
		},
	}, pulumi.DependsOn([]pulumi.Resource{secret}))
}
