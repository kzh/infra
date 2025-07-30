import pulumi
import pulumi_kubernetes as k8s
import pulumi_tls as tls

RESOURCE_NAME = "vault"
NAMESPACE = "vault"

namespace = k8s.core.v1.Namespace(
    NAMESPACE,
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name=NAMESPACE,
    ),
)

def setup_tls(namespace_resource):
    """
    Note: This is a simplified version of the TLS setup.
    The original Go code includes complex CSR approval logic that would need
    to be implemented separately using the kubernetes client-go equivalent in Python.
    """
    
    # Private Key
    key = tls.PrivateKey(
        "vault-private-key",
        algorithm="ECDSA",
        ecdsa_curve="P256",
    )
    
    # Certificate Request
    cert_request = tls.CertRequest(
        "vault-cr",
        private_key_pem=key.private_key_pem,
        subject=tls.CertRequestSubjectArgs(
            common_name=f"system:node:{RESOURCE_NAME}.{NAMESPACE}.svc",
            organization="system:nodes",
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
    
    # Note: The original Go code includes CSR approval logic that requires
    # direct Kubernetes API access. This would need to be implemented using
    # the Kubernetes Python client or a custom Pulumi dynamic provider.
    
    # For now, we'll create a placeholder secret that would need to be populated
    # with the actual certificates after CSR approval
    return k8s.core.v1.Secret(
        "vault-certs",
        metadata=k8s.meta.v1.ObjectMetaArgs(
            name=RESOURCE_NAME,
            namespace=NAMESPACE,
        ),
        string_data={
            "vault.key": key.private_key_pem,
            # Note: These would need to be populated after CSR approval
            "vault.crt": "# TODO: Populate after CSR approval",
            "vault.ca": "# TODO: Populate with cluster CA",
        },
        opts=pulumi.ResourceOptions(depends_on=[namespace_resource]),
    )

def new_vault_chart(secret):
    """Deploy Vault Chart"""
    config = """
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
    
    await_annotation = {
        "pulumi.com/skipAwait": "true",
    }
    
    return k8s.helm.v4.Chart(
        RESOURCE_NAME,
        chart="vault",
        namespace=NAMESPACE,
        repository_opts=k8s.helm.v4.RepositoryOptsArgs(
            repo="https://helm.releases.hashicorp.com",
        ),
        version="0.30.0",
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
                "annotations": await_annotation,
                "service": {
                    "annotations": {
                        "pulumi.com/skipAwait": "true",
                        "tailscale.com/expose": "true",
                        "tailscale.com/hostname": "vault",
                    },
                },
                "statefulSet": {
                    "annotations": await_annotation,
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
                    "config": config,
                },
            },
        },
        opts=pulumi.ResourceOptions(depends_on=[secret]),
    )

# Main execution
secret = setup_tls(namespace)
vault_chart = new_vault_chart(secret)