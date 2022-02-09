package main

import (
	"github.com/kzh/infra-faust/pkg/services"
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

		chart, err := NewVaultChart(ctx, secret)
		if err != nil {
			return err
		}

		chart.GetResource("v1/Service", "vault", "vault").ApplyT(
			func(arg interface{}) error {
				service := arg.(*corev1.Service)
				services.NewTailscaleProxy(ctx, service)
				return nil
			},
		)

		return nil
	})
}

func NewVaultChart(ctx *pulumi.Context, secret *corev1.Secret) (*helm.Chart, error) {
	const (
		Repository   = "https://helm.releases.hashicorp.com"
		Chart        = "vault"
		ChartVersion = "0.18.0"
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
				"statefulSet": pulumi.Map{
					"annotations": pulumi.Map{
						"pulumi.com/skipAwait": pulumi.String("true"),
					},
				},
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
