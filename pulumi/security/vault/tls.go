package main

import (
	"context"
	"encoding/base64"
	"errors"

	certificates "github.com/pulumi/pulumi-kubernetes/sdk/v3/go/kubernetes/certificates/v1"
	corev1 "github.com/pulumi/pulumi-kubernetes/sdk/v3/go/kubernetes/core/v1"
	metav1 "github.com/pulumi/pulumi-kubernetes/sdk/v3/go/kubernetes/meta/v1"
	"github.com/pulumi/pulumi-tls/sdk/v4/go/tls"
	"github.com/pulumi/pulumi/sdk/v3/go/pulumi"
	k8scerts "k8s.io/api/certificates/v1"
	k8scorev1 "k8s.io/api/core/v1"
	k8serrors "k8s.io/apimachinery/pkg/api/errors"
	k8smetav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	k8scsr "k8s.io/client-go/util/certificate/csr"

	"github.com/kzh/infra-faust/pkg/k8s"
)

func kSetupTLS(ctx *pulumi.Context, namespace *corev1.Namespace) (*corev1.Secret, error) {
	// PrivateKey
	key, err := tls.NewPrivateKey(ctx, "vault-private-key", &tls.PrivateKeyArgs{
		Algorithm:  pulumi.String("ECDSA"),
		EcdsaCurve: pulumi.String("P256"),
	})
	if err != nil {
		return nil, err
	}

	// CertRequest
	cr, err := tls.NewCertRequest(ctx, "vault-cr", &tls.CertRequestArgs{
		PrivateKeyPem: key.PrivateKeyPem,
		Subject: tls.CertRequestSubjectArgs{
			CommonName:   pulumi.String("system:node:" + ResourceName + "." + Namespace + ".svc"),
			Organization: pulumi.String("system:nodes"),
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
		return nil, err
	}

	// Base64 encode CertRequest
	req := cr.CertRequestPem.ApplyT(func(input string) string {
		return base64.StdEncoding.EncodeToString([]byte(input))
	}).(pulumi.StringOutput)

	// CertificateSigningRequest
	csr, err := certificates.NewCertificateSigningRequest(ctx, "vault-csr", &certificates.CertificateSigningRequestArgs{
		Metadata: metav1.ObjectMetaArgs{
			Name: pulumi.String(ResourceName),
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
	})

	cert := csr.Status.ApplyT(func(arg interface{}) (string, error) {
		if cert, err := ApproveCSR(ResourceName); cert != "" && err == nil {
			return cert, nil
		} else if cert := FetchCert(ResourceName, Namespace); cert != "" {
			return cert, nil
		}
		return "", errors.New("missing vault certificate")
	}).(pulumi.StringOutput)

	return corev1.NewSecret(ctx, "vault-certs", &corev1.SecretArgs{
		Metadata: metav1.ObjectMetaArgs{
			Name:      pulumi.String(ResourceName),
			Namespace: pulumi.String(Namespace),
		},
		StringData: pulumi.StringMap{
			"vault.key": key.PrivateKeyPem,
			"vault.crt": cert,
			"vault.ca":  pulumi.String(k8s.CA()),
		},
	}, pulumi.DependsOn([]pulumi.Resource{namespace}))
}

func ApproveCSR(name string) (string, error) {
	clientset, err := k8s.Clientset()
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
func FetchCert(name, namespace string) string {
	clientset, err := k8s.Clientset()
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
