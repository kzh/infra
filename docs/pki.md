## PKI
### mTLS
This instructions make heavy use of [smallstep/cli](https://github.com/smallstep/cli).
```
# Create Root CA
$ step certificate create root-ca root-ca.crt root-ca.key --profile root-ca

# Create Intermediate CA
$ step certificate create intermediate-ca intermediate-ca.crt intermediate-ca.key \
  --profile intermediate-ca \
  --ca ./root-ca.crt --ca-key ./root-ca.key

# Generate Server Certificate and Key
$ step certificate create \*.faust.dev server.crt server.key \
  --profile leaf \
  --ca ./intermediate-ca.crt --ca-key ./intermediate-ca.key \
  --not-after=4380h \
  --insecure --no-password

# Create Kubernetes CA secret
$ kubectl create secret generic mtls \
  --namespace istio-system \
  --from-file=tls.crt=server.crt \
  --from-file=tls.key=server.key \
  --from-file=ca.crt=intermediate-ca.crt

# Generate Client Certificate and Key
$ step certificate create \*.faust.dev client.crt client.key \
  --profile leaf \
  --ca ./intermediate-ca.crt --ca-key ./intermediate-ca.key \
  --not-after=4380h \
  --insecure --no-password

# Generate p12 format
$ step certificate p12 client.p12 client.crt client.key
```