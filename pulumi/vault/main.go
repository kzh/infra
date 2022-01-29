package main

import (
	"context"
	"encoding/base64"
	"errors"
	"github.com/kzh/infra-faust/pkg/infra"
	certificates "github.com/pulumi/pulumi-kubernetes/sdk/v3/go/kubernetes/certificates/v1"
	corev1 "github.com/pulumi/pulumi-kubernetes/sdk/v3/go/kubernetes/core/v1"
	"github.com/pulumi/pulumi-kubernetes/sdk/v3/go/kubernetes/helm/v3"
	metav1 "github.com/pulumi/pulumi-kubernetes/sdk/v3/go/kubernetes/meta/v1"
	"github.com/pulumi/pulumi-tls/sdk/v4/go/tls"
	"github.com/pulumi/pulumi/sdk/v3/go/pulumi"
	k8scerts "k8s.io/api/certificates/v1"
	k8scorev1 "k8s.io/api/core/v1"
	k8serrors "k8s.io/apimachinery/pkg/api/errors"
	k8smetav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	k8scsr "k8s.io/client-go/util/certificate/csr"
)

func main() {
	pulumi.Run(func(ctx *pulumi.Context) error {
		key, err := tls.NewPrivateKey(ctx, "vault-private-key", &tls.PrivateKeyArgs{
			Algorithm:  pulumi.String("ECDSA"),
			EcdsaCurve: pulumi.String("P256"),
		})
		if err != nil {
			return err
		}

		const (
			ResourceName = "vault"
			Namespace    = "vault-test"
		)

		cr, err := tls.NewCertRequest(ctx, "vault-cr", &tls.CertRequestArgs{
			KeyAlgorithm:  key.Algorithm,
			PrivateKeyPem: key.PrivateKeyPem,
			Subjects: tls.CertRequestSubjectArray{
				tls.CertRequestSubjectArgs{
					CommonName:   pulumi.String("system:node:" + ResourceName + "." + Namespace + ".svc"),
					Organization: pulumi.String("system:nodes"),
				},
			},
			DnsNames: pulumi.StringArray{
				pulumi.String(ResourceName),
				pulumi.String(ResourceName + "." + Namespace),
				pulumi.String(ResourceName + "." + Namespace + ".svc"),
				pulumi.String(ResourceName + "." + Namespace + ".svc.cluster.local"),
			},
			IpAddresses: pulumi.StringArray{
				pulumi.String("127.0.0.1"),
			},
		})
		if err != nil {
			return err
		}

		req := cr.CertRequestPem.ToStringOutput().ApplyT(func(input string) string {
			return base64.StdEncoding.EncodeToString([]byte(input))
		}).(pulumi.StringOutput)

		ctx.Export("certificate", req)

		namespace, err := corev1.NewNamespace(ctx, Namespace, &corev1.NamespaceArgs{
			Metadata: metav1.ObjectMetaArgs{
				Name: pulumi.String(Namespace),
			},
		})
		if err != nil {
			return err
		}

		csr, err := certificates.NewCertificateSigningRequest(ctx, "vault-csr", &certificates.CertificateSigningRequestArgs{
			Metadata: metav1.ObjectMetaArgs{
				Name:      pulumi.String(ResourceName),
				Namespace: pulumi.String(Namespace),
			},
			Spec: certificates.CertificateSigningRequestSpecArgs{
				SignerName: pulumi.String("kubernetes.io/kubelet-serving"),
				Request:    req,
				Groups: pulumi.StringArray{
					pulumi.String("system:authenticated"),
				},
				Usages: pulumi.StringArray{
					pulumi.String("digital signature"),
					pulumi.String("key encipherment"),
					pulumi.String("server auth"),
				},
			},
		}, pulumi.DependsOn([]pulumi.Resource{namespace}))
		if err != nil {
			return err
		}

		cert := csr.ID().ApplyT(func(id pulumi.ID) (string, error) {
			if cert, err := ApproveCSR(ResourceName); cert != "" && err == nil {
				return cert, nil
			} else if cert := FetchSecretCert(ResourceName, Namespace); cert != "" {
				return cert, nil
			}
			return "", errors.New("missing vault certificate")
		}).(pulumi.StringOutput)

		secret, err := corev1.NewSecret(ctx, "vault-certs", &corev1.SecretArgs{
			Metadata: metav1.ObjectMetaArgs{
				Name:      pulumi.String(ResourceName),
				Namespace: pulumi.String(Namespace),
			},
			StringData: pulumi.StringMap{
				"vault.key": key.PrivateKeyPem,
				"vault.crt": cert,
				"vault.ca":  pulumi.String(infra.K8SCA()),
			},
		}, pulumi.DependsOn([]pulumi.Resource{csr}))
		if err != nil {
			return err
		}

		const (
			Repository   = "https://helm.releases.hashicorp.com"
			Chart        = "vault"
			ChartVersion = "0.18.0"
		)

		config := `
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

		_, err = helm.NewChart(ctx, ResourceName, helm.ChartArgs{
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
		if err != nil {
			return err
		}

		return nil
	})
}

func ApproveCSR(name string) (string, error) {
	clientset, err := infra.K8SClientset()
	if err != nil {
		return "", err
	}

	csr, err := clientset.CertificatesV1().CertificateSigningRequests().Get(context.Background(), name, k8smetav1.GetOptions{})
	if k8serrors.IsNotFound(err) {
		return "", nil
	} else if err != nil {
		return "", err
	}

	// Approve the CSR only if it is still pending
	if len(csr.Status.Conditions) == 0 {
		csr.Status.Conditions = append(csr.Status.Conditions, k8scerts.CertificateSigningRequestCondition{
			Type:   k8scerts.CertificateApproved,
			Status: k8scorev1.ConditionTrue,
		})

		csr, err = clientset.CertificatesV1().CertificateSigningRequests().UpdateApproval(
			context.Background(),
			name,
			csr,
			k8smetav1.UpdateOptions{},
		)
		if err != nil {
			return "", err
		}
	}

	cert, err := k8scsr.WaitForCertificate(context.Background(), clientset, name, csr.UID)
	return string(cert), err
}

func FetchSecretCert(name, namespace string) string {
	clientset, err := infra.K8SClientset()
	if err != nil {
		return ""
	}

	secret, err := clientset.CoreV1().Secrets(namespace).Get(
		context.Background(),
		name,
		k8smetav1.GetOptions{},
	)
	if err != nil {
		return ""
	}

	return string(secret.Data["vault.crt"])
}

func InitVault(name, namespace string) {

}
