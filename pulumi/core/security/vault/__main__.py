import base64

import pulumi_kubernetes as k8s
import pulumi_tls as tls

import pulumi

RESOURCE_NAME = "vault"
NAMESPACE = "vault"
SKIP_AWAIT_ANNOTATION = {
    "pulumi.com/skipAwait": "true",
}
VAULT_CONFIG = """
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
"""

namespace = k8s.core.v1.Namespace(
    NAMESPACE,
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=NAMESPACE,
    ),
)


def base64_pem(value: pulumi.Output[str]) -> pulumi.Output[str]:
    return value.apply(lambda pem: base64.b64encode(pem.encode()).decode())


def setup_tls(namespace_resource: pulumi.Resource) -> k8s.core.v1.Secret:
    ca_key = tls.PrivateKey(
        "vault-ca-private-key",
        algorithm="ECDSA",
        ecdsa_curve="P256",
    )
    ca_cert = tls.SelfSignedCert(
        "vault-ca",
        private_key_pem=ca_key.private_key_pem,
        is_ca_certificate=True,
        validity_period_hours=87600,
        early_renewal_hours=720,
        allowed_uses=[
            "cert_signing",
            "crl_signing",
        ],
        subject=tls.SelfSignedCertSubjectArgs(
            common_name=f"{RESOURCE_NAME}-ca",
            organization="kzh",
        ),
    )

    key = tls.PrivateKey(
        "vault-private-key",
        algorithm="ECDSA",
        ecdsa_curve="P256",
    )

    cert_request = tls.CertRequest(
        "vault-cr",
        private_key_pem=key.private_key_pem,
        subject=tls.CertRequestSubjectArgs(
            common_name=f"{RESOURCE_NAME}.{NAMESPACE}.svc",
            organization="kzh",
        ),
        dns_names=[
            RESOURCE_NAME,
            f"{RESOURCE_NAME}.{NAMESPACE}",
            f"{RESOURCE_NAME}.{NAMESPACE}.svc",
            f"{RESOURCE_NAME}.{NAMESPACE}.svc.cluster.local",
        ],
        ip_addresses=[
            "127.0.0.1",
        ],
    )

    cert = tls.LocallySignedCert(
        "vault-cert",
        cert_request_pem=cert_request.cert_request_pem,
        ca_private_key_pem=ca_key.private_key_pem,
        ca_cert_pem=ca_cert.cert_pem,
        validity_period_hours=8760,
        early_renewal_hours=720,
        allowed_uses=[
            "digital_signature",
            "key_encipherment",
            "server_auth",
        ],
    )

    return k8s.core.v1.Secret(
        "vault-certs",
        metadata=k8s.meta.v1.ObjectMetaArgs(
            name=RESOURCE_NAME,
            namespace=NAMESPACE,
        ),
        data={
            "vault.key": base64_pem(key.private_key_pem),
            "vault.crt": base64_pem(cert.cert_pem),
            "vault.ca": base64_pem(ca_cert.cert_pem),
        },
        opts=pulumi.ResourceOptions(depends_on=[namespace_resource]),
    )


def new_vault_chart(secret: pulumi.Resource) -> k8s.helm.v4.Chart:
    return k8s.helm.v4.Chart(
        RESOURCE_NAME,
        chart="vault",
        resource_prefix="",
        namespace=NAMESPACE,
        repository_opts=k8s.helm.v4.RepositoryOptsArgs(
            repo="https://helm.releases.hashicorp.com",
        ),
        version="0.32.0",
        values={
            "global": {
                "tlsDisable": False,
            },
            "injector": {
                "enabled": False,
            },
            "server": {
                "extraEnvironmentVars": {
                    "VAULT_CACERT": "/etc/pki/vault/vault.ca",
                },
                "annotations": SKIP_AWAIT_ANNOTATION,
                "service": {
                    "annotations": {
                        "pulumi.com/skipAwait": "true",
                        "tailscale.com/expose": "true",
                        "tailscale.com/hostname": "vault",
                    },
                },
                "statefulSet": {
                    "annotations": SKIP_AWAIT_ANNOTATION,
                },
                "extraVolumes": [
                    {
                        "type": "secret",
                        "name": RESOURCE_NAME,
                        "path": "/etc/pki",
                    },
                ],
                "standalone": {
                    "enabled": True,
                    "config": VAULT_CONFIG,
                },
            },
        },
        opts=pulumi.ResourceOptions(
            depends_on=[secret],
            aliases=[pulumi.Alias(type_="kubernetes:helm.sh/v3:Chart")],
        ),
    )


# Main execution
secret = setup_tls(namespace)
vault_chart = new_vault_chart(secret)
