package main

import (
	"github.com/pulumi/pulumi-vault/sdk/v5/go/vault"
	"github.com/pulumi/pulumi-vault/sdk/v5/go/vault/pkisecret"
	"github.com/pulumi/pulumi/sdk/v3/go/pulumi"
	"strconv"
)

const (
	ttl = 31536000 // one year in seconds
)

func main() {

	pulumi.Run(func(ctx *pulumi.Context) error {
		// PKI mount for Root CA
		root, err := vault.NewMount(ctx, "pki", &vault.MountArgs{
			Description:            pulumi.String("Root CA"),
			Path:                   pulumi.String("pki"),
			Type:                   pulumi.String("pki"),
			DefaultLeaseTtlSeconds: pulumi.Int(ttl * 5),
			MaxLeaseTtlSeconds:     pulumi.Int(ttl * 5),
		})
		if err != nil {
			return err
		}

		// PKI mount for Intermediate CA
		intermediate, err := vault.NewMount(ctx, "pki_int", &vault.MountArgs{
			Description:            pulumi.String("Intermediate CA"),
			Path:                   pulumi.String("pki_int"),
			Type:                   pulumi.String("pki"),
			DefaultLeaseTtlSeconds: pulumi.Int(ttl),
			MaxLeaseTtlSeconds:     pulumi.Int(ttl),
		})
		if err != nil {
			return nil
		}

		// Generate Root CA
		rootCert, err := pkisecret.NewSecretBackendRootCert(ctx, "root", &pkisecret.SecretBackendRootCertArgs{
			Backend:           root.Path,
			Type:              pulumi.String("internal"),
			CommonName:        pulumi.String("mx"),
			Ttl:               pulumi.String(strconv.Itoa(ttl * 5)),
			Format:            pulumi.String("pem"),
			PrivateKeyFormat:  pulumi.String("der"),
			KeyType:           pulumi.String("rsa"),
			KeyBits:           pulumi.Int(4096),
			ExcludeCnFromSans: pulumi.Bool(true),
		})

		// Generate CSR for intermediate certificate
		intermediateCertRequest, err := pkisecret.NewSecretBackendIntermediateCertRequest(
			ctx, "intermediateCr", &pkisecret.SecretBackendIntermediateCertRequestArgs{
				Backend:    intermediate.Path,
				Type:       rootCert.Type,
				CommonName: pulumi.String("mx int"),
			},
		)
		if err != nil {
			return err
		}

		signInt, err := pkisecret.NewSecretBackendRootSignIntermediate(ctx, "signInt", &pkisecret.SecretBackendRootSignIntermediateArgs{
			Backend:           root.Path,
			Csr:               intermediateCertRequest.Csr,
			CommonName:        pulumi.String("mx int"),
			ExcludeCnFromSans: pulumi.Bool(true),
			Revoke:            pulumi.Bool(true),
		})
		if err != nil {
			return err
		}

		_, err = pkisecret.NewSecretBackendIntermediateSetSigned(ctx, "setSigned", &pkisecret.SecretBackendIntermediateSetSignedArgs{
			Backend:     intermediate.Path,
			Certificate: signInt.Certificate,
		})
		if err != nil {
			return err
		}

		_, err = pkisecret.NewSecretBackendRole(ctx, "internal", &pkisecret.SecretBackendRoleArgs{
			Name:             pulumi.String("internal"),
			Backend:          intermediate.Path,
			AllowSubdomains:  pulumi.Bool(true),
			AllowGlobDomains: pulumi.Bool(true),
			AllowedDomains:   pulumi.StringArray{pulumi.String("faust.dev")},
		})
		if err != nil {
			return err
		}

		_, err = pkisecret.NewSecretBackendRole(ctx, "kevin", &pkisecret.SecretBackendRoleArgs{
			Name:             pulumi.String("kevin"),
			Backend:          intermediate.Path,
			AllowBareDomains: pulumi.Bool(true),
			AllowedDomains:   pulumi.StringArray{pulumi.String("kevin")},
		})
		if err != nil {
			return err
		}

		return nil
	})
}
