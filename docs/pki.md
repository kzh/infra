## PKI
#### mTLS
```
# Create Root CA
$ step certificate create root-ca root-ca.crt root-ca.key --profile root-ca

# Create Intermediate CA
$ step certificate create intermediate-ca intermediate-ca.crt intermediate-ca.key \
  --profile intermediate-ca \
  --ca ./root-ca.crt --ca-key ./root-ca.key

# Generate Client/Server Certificate and Key
$ step certificate create \*.faust.dev server.crt server.key \
  --profile leaf \
  --ca ./intermediate-ca.crt --ca-key ./intermediate-ca.key \
  --not-after=2160h
```