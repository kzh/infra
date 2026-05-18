# cert-manager

Source: `pulumi/core/security/cert-manager`

cert-manager is the cluster's certificate lifecycle controller. In Kubernetes,
a TLS certificate normally becomes useful only after three separate things line
up:

```text
an authority can sign or obtain the certificate
a Kubernetes object asks for the exact names it needs
a workload or ingress consumes the resulting Secret
```

cert-manager gives those steps a Kubernetes API. Instead of hand-copying PEM
files into Secrets and remembering to renew them later, a stack can create a
`Certificate` resource that points at an `Issuer` or `ClusterIssuer`. The
controller then creates the signing request, waits for the issuer or ACME
challenge path to succeed, writes a TLS Secret, and renews that Secret before
expiry.

That is the whole value of cert-manager in this repo: it turns certificate
intent into a reconciled Secret lifecycle. The Secret is still sensitive. The
private key is still real key material. The consuming app still has to point at
the right Secret in the right namespace. cert-manager automates the lifecycle;
it does not make trust design automatic.

## What This Stack Installs

The Pulumi program for this stack is deliberately small. It reads
`cert-manager:namespace` from stack config, creates that namespace, and installs
the Jetstack `cert-manager` Helm chart with CRDs enabled.

Current stack shape:

```text
Pulumi project:   pulumi/core/security/cert-manager
Stack config:     cert-manager:namespace
Namespace on mx:  cert-manager
Chart:            cert-manager
Chart version:    v1.20.2
Repository:       https://charts.jetstack.io
CRDs:             enabled by Helm values
Runtime:          Python 3.12 through uv
```

The chart installs the controller, webhook, CA injector, Services, Deployments,
and cert-manager API types. The important CRD groups are
`cert-manager.io` and `acme.cert-manager.io`.

This stack does not currently define the repo's certificate policy. It does not
create a `ClusterIssuer`, namespace `Issuer`, or application `Certificate`.
Those resources belong in the stack that owns the trust decision or the
consumer. That boundary is intentional. Installing cert-manager is platform
machinery; deciding who may issue what names is application or environment
policy.

## The Moving Parts

A healthy cert-manager path has four roles.

The controller is the reconciler. It watches `Certificate`, `Issuer`,
`ClusterIssuer`, `CertificateRequest`, `Order`, and `Challenge` resources and
moves the cluster toward the requested state. If the controller is down,
certificates do not issue or renew even if all YAML looks correct.

The webhook is the admission and validation path for cert-manager resources. If
the webhook Service has no endpoints or its serving certificate is broken,
Kubernetes may reject creates and updates before the controller ever sees them.
Webhook failures often show up during Pulumi preview or apply as admission
errors, not as certificate status changes.

The CA injector copies CA bundles into webhook and APIService-style objects
that need them. It is easy to forget because it is not the thing issuing your
app certificate, but broken CA injection can make admission paths fail in ways
that look unrelated to one specific `Certificate`.

Issuers and certificates are the user-facing cert-manager API. An issuer says
"this is an authority or signing workflow." A certificate says "create and keep
fresh a TLS Secret for these names using that issuer."

## Certificate Lifecycle

Start from first principles. A workload cannot use "a certificate" in the
abstract. It uses bytes in a Kubernetes Secret, usually a Secret of type
`kubernetes.io/tls` with `tls.crt` and `tls.key`. cert-manager's job is to keep
that Secret correct over time.

The normal lifecycle is:

```text
Issuer or ClusterIssuer becomes Ready
Certificate is created in the consumer namespace
cert-manager creates a CertificateRequest
issuer signs it, or ACME Order and Challenge resources complete
cert-manager writes or updates the target Secret
consumer references the Secret
consumer reloads, restarts, or watches the Secret as needed
cert-manager renews before NotAfter
old material ages out through the controller and consumer behavior
```

When something breaks, place the failure on that timeline before changing
anything. A missing TLS Secret can mean the `Certificate` was never accepted,
the issuer is not ready, an ACME challenge cannot complete, the webhook is
down, or cert-manager lacks permission to write the Secret. An existing Secret
can still be unusable if the Ingress references a different name, the Secret is
in the wrong namespace, the host names do not match, or the app has not reloaded
after renewal.

The `Certificate` status is the best first evidence because it connects intent,
issuer, target Secret, observed generation, readiness, expiry, and renewal time.
The related `CertificateRequest` explains the signing request. For ACME, `Order`
and `Challenge` resources explain the external proof step.

Useful read-only map:

```bash
kubectl get certificate,certificaterequest,order,challenge,issuer,clusterissuer -A
```

Then focus on one namespace and one object:

```bash
kubectl describe certificate -n <namespace> <certificate>
kubectl describe certificaterequest -n <namespace> <request>
kubectl get events -n <namespace> --sort-by=.lastTimestamp
```

Do not start by decoding Secret data. Most certificate failures can be diagnosed
from object status, events, Secret type, expiry metadata, and consumer
references. If the certificate bytes must be inspected, keep the raw PEM and key
material local and share only the non-sensitive conclusion.

## Issuers And Trust Policy

An issuer is the authority path. A `Certificate` without a valid issuer is only
a request with nowhere to go.

Use a namespace `Issuer` when the authority should be scoped to one namespace.
Use a `ClusterIssuer` only when many namespaces intentionally share the same
authority. The wider the issuer, the wider the blast radius of a mistake. A
bad namespace issuer can break one app. A bad cluster issuer can break many
apps at once.

Common issuer patterns are:

```text
ACME issuer       public or private ACME flow, often Let's Encrypt
CA issuer         signs from an existing CA Secret
selfSigned issuer bootstraps local CA material or internal-only certificates
Vault issuer      signs through Vault if that integration is configured
```

This repo's cert-manager stack does not pick one of those patterns for you. If
a future app needs public certificates, the app or environment stack should own
the ACME issuer choice, solver configuration, DNS credential Secret, hostnames,
and renewal behavior. If a future internal service needs a private CA, the stack
that owns that CA and trust distribution should own the issuer. If the trust
policy changes, review every consumer that depends on it.

When debugging a single broken `Certificate`, inspect the referenced issuer
before editing the certificate:

```bash
kubectl describe issuer -n <namespace> <issuer>
kubectl describe clusterissuer <clusterissuer>
```

Issuer `Ready=False` usually means every certificate that depends on it will
fail. Issuer `Ready=True` does not prove a particular certificate is valid; it
only proves the authority path is available.

## Secrets Are Deliverables, Not Source Code

The TLS Secret is the deliverable cert-manager writes for the consumer. It is
not the source of truth for certificate intent. The source of truth is the
`Certificate` plus its issuer.

A cert-manager-managed TLS Secret usually has:

```text
type: kubernetes.io/tls
data keys: tls.crt, tls.key
optional data key: ca.crt
owner/controller metadata or cert-manager annotations
same namespace as the Certificate
same namespace as the Ingress or pod that references it
```

The same-namespace rule matters. Kubernetes Ingress TLS references do not reach
across namespaces. A Secret named `app-tls` in `cert-manager` does not help an
Ingress in `coder`; the Secret must exist in the consumer namespace unless a
separate sync mechanism intentionally copies it.

Safe Secret inspection:

```bash
kubectl get secret -n <namespace> <secret>
kubectl describe secret -n <namespace> <secret>
kubectl get secret -n <namespace> <secret> -o jsonpath='{.type}{"\n"}'
```

`kubectl describe secret` shows metadata and key names without decoding values.
That is usually enough to confirm whether the Secret exists, has the expected
type, and contains the expected key names. Do not paste decoded `tls.key`,
decoded Secret `data`, full PEM blocks, or base64 payloads into docs, commit
messages, PRs, issues, or chat.

Manual Secret edits are fragile. cert-manager may overwrite them, and consumers
may see partial or unexpected material. If the Secret is wrong, fix the
`Certificate`, issuer, challenge path, or consumer reference. Deleting a Secret
can force reissuance, but it can also break live traffic until a new Secret is
ready, so treat it as an intentional maintenance action rather than a first
debugging move.

## Consumers In This Repo

This stack installs cert-manager so other stacks can use the cert-manager API.
The current hand-written Pulumi code does not define a `Certificate`, `Issuer`,
or `ClusterIssuer` outside the cert-manager installation itself.

That does not mean TLS Secrets are absent from the repo. Consumers can still
reference TLS material directly. For example, the Coder stack has optional
Ingress TLS settings: when `ingress_tls_enabled` and a TLS Secret name are
configured, the Ingress points at that Secret. cert-manager would only be part
of that path if another resource creates and maintains the Secret in Coder's
namespace.

There are also certificate paths that are not cert-manager paths. The Vault
stack generates its own TLS material with Pulumi and stores it in a Kubernetes
Secret. PostgreSQL-related docs and stacks may talk about CA Secrets for
database trust. The Tailscale operator can also handle HTTPS behavior for
tailnet exposure. Do not assume every certificate-shaped object came from
cert-manager. Identify the owner before changing it.

For any consumer, the practical questions are:

```text
Which namespace owns the workload?
Which Secret name does the workload or Ingress reference?
Which Certificate, if any, owns that Secret?
Which Issuer or ClusterIssuer does the Certificate reference?
Does the consumer reload automatically when the Secret changes?
```

If there is no `Certificate` for the Secret, cert-manager is not currently the
source of truth for that Secret. Look for a Helm value, Pulumi-generated Secret,
operator-generated Secret, manual bootstrap process, or external secret sync.

## Debugging Controller Health

Use the stack config to find the namespace:

```bash
cd pulumi/core/security/cert-manager
NS="$(pulumi config get namespace --stack mx)"
```

Then check the installed control plane:

```bash
kubectl get pods,deploy,svc,endpoints -n "$NS"
kubectl get crd | rg 'cert-manager.io|acme.cert-manager.io'
kubectl get validatingwebhookconfiguration,mutatingwebhookconfiguration | rg cert-manager
kubectl get deploy -n "$NS" --show-labels | rg 'cert-manager|webhook|cainjector'
kubectl logs -n "$NS" deploy/<controller-deployment> --tail=200
kubectl logs -n "$NS" deploy/<webhook-deployment> --tail=200
kubectl logs -n "$NS" deploy/<cainjector-deployment> --tail=200
```

For the controller, look for reconcile errors and repeated failures involving
the same issuer or Secret. For the webhook, look for admission, serving
certificate, endpoint, or CA bundle errors. For the CA injector, look for
objects that cannot be patched or watched.

If CRDs are missing, Kubernetes will not understand `Certificate` or issuer
resources. If the webhook has no endpoints, Kubernetes may reject cert-manager
resources even though the CRDs exist. If the controller is running but issuer
status is bad, the installation may be healthy while certificate policy is not.

Those are different failures, and they call for different fixes.

## Debugging A Certificate

Start with the resource that expresses intent:

```bash
kubectl get certificate -n <namespace> <certificate> -o wide
kubectl describe certificate -n <namespace> <certificate>
```

Read the condition messages. In cert-manager, `Ready=False` is usually paired
with a reason and message that says what it is waiting for. `Issuing=True`
usually means cert-manager has decided a new certificate is needed. The status
also points at the target Secret, last failure time, renewal time, and not-after
date when available.

Then follow the chain:

```bash
kubectl get certificaterequest -n <namespace>
kubectl describe certificaterequest -n <namespace> <request>
kubectl get order,challenge -n <namespace>
kubectl describe order -n <namespace> <order>
kubectl describe challenge -n <namespace> <challenge>
kubectl get events -n <namespace> --sort-by=.lastTimestamp
```

Common readings:

```text
Certificate Ready=True
  cert-manager produced a currently valid Secret. Move to consumer debugging.

Certificate Ready=False and issuer not ready
  debug the referenced Issuer or ClusterIssuer.

CertificateRequest denied or failed
  inspect issuer policy, signer behavior, and request details.

ACME Challenge pending or failed
  debug DNS, HTTP reachability, solver pods/services/ingresses, and credentials.

Secret exists but app serves old material
  debug the consumer reload path and whether it references the same Secret.

Secret does not exist
  find whether issuance ever reached the Secret write step.
```

If the resource names are not obvious, use labels and owner references to map
them:

```bash
kubectl get certificaterequest,order,challenge -n <namespace> --show-labels
kubectl get secret -n <namespace> <secret> -o yaml
```

Only use the YAML form when you need metadata. Do not copy the Secret `data`
field into durable text.

## Debugging Webhook And Admission Errors

Webhook failures happen before normal cert-manager reconciliation. They often
show up as errors while creating or updating resources:

```text
failed calling webhook
no endpoints available for service
x509: certificate signed by unknown authority
context deadline exceeded
```

When that happens, do not rewrite the `Certificate` first. Check the webhook
plumbing:

```bash
kubectl get pods,svc,endpoints -n "$NS" | rg 'cert-manager|webhook'
kubectl get deploy,svc -n "$NS" | rg webhook
kubectl describe deploy -n "$NS" <webhook-deployment>
kubectl describe svc -n "$NS" <webhook-service>
kubectl get validatingwebhookconfiguration -o name | rg cert-manager
kubectl describe validatingwebhookconfiguration <name>
```

No endpoints usually means the webhook pod is not ready or the Service selector
does not match. TLS trust errors usually point at webhook serving certificate or
CA bundle injection problems. Timeouts can be network policy, pod readiness, or
API server reachability to the webhook Service.

Because the webhook is cluster-admission plumbing, a bad webhook can block
unrelated cert-manager resources across namespaces. Treat it as controller
health, not as one app's certificate problem.

## Rotation And Renewal

Normal renewal is cert-manager's job. A `Certificate` has a requested duration
and renewal window, either explicit in spec or inherited from defaults and
issuer behavior. cert-manager watches expiry and starts issuing a replacement
before `NotAfter`.

The stable path is:

```text
keep Certificate metadata.name stable
keep spec.secretName stable unless intentionally migrating consumers
keep issuerRef stable unless changing authority on purpose
let cert-manager update the Secret
verify the consumer noticed the updated Secret
```

Changing DNS names, issuer references, key settings, duration, or the target
Secret name can cause reissuance. Changing the Secret name is also a consumer
migration because every Ingress, pod mount, chart value, or app config pointing
at the old Secret must move.

Consumers handle renewed Secrets differently. Ingress controllers usually watch
TLS Secret changes and reload. Pods mounting a Secret as a volume usually see
file updates eventually, but the application process may not reload the files.
Environment variables sourced from Secrets do not update until the pod is
recreated. Some applications cache TLS material at startup. Always verify the
consumer behavior, not only the Secret timestamp.

Safer renewal checks:

```bash
kubectl describe certificate -n <namespace> <certificate>
kubectl get secret -n <namespace> <secret>
kubectl rollout status deployment -n <namespace> <deployment>
curl -fsSIk https://<host>
```

If you need to force a renewal, prefer the cert-manager-supported path
available in the environment, such as `cmctl renew`, after confirming the
target certificate and consumer impact. Deleting live Secrets, deleting all
`CertificateRequest` objects, or editing Secret data by hand can create outages
and makes the cause harder to reconstruct.

CA rotation is higher risk than leaf certificate renewal. Any client that
trusts the old CA may need an overlap window, trust bundle update, restart, or
configuration change. Do not rotate an issuer or CA because one consumer is
misconfigured until the evidence points at the authority itself.

## Safe Changes To This Stack

Changes to this stack can affect every future or existing cert-manager
consumer, even when the Pulumi diff looks like a small Helm value change. CRDs,
webhook behavior, default controller behavior, and ACME solver behavior are
cluster-wide concerns.

Before editing the stack source, know which kind of change you are making:

```text
chart upgrade         CRDs, webhook behavior, controller defaults, API behavior
namespace change      controller relocation and potential replacement
CRD setting change    API installation and ownership behavior
issuer addition       trust policy, credentials, DNS or CA ownership
consumer certificate  app-specific Secret lifecycle and reload behavior
```

Issuer and consumer certificate resources should usually live with the stack
that owns the policy or app, not in this controller installation stack. If a
shared cluster issuer is added later, document why it is shared, what namespaces
may use it, where credentials live, and how renewal and challenge failures are
verified without exposing secret values.

For code or chart changes, use the repo checks and stop at preview unless an
apply was explicitly requested:

```bash
just sync pulumi/core/security/cert-manager
just check-python
just lint
git diff --check
just preview pulumi/core/security/cert-manager stack=mx
```

For this docs-only page, a Pulumi preview is usually unnecessary because the
program did not change. If source code, chart values, project dependencies, or
stack config change, preview the stack and classify any failure before editing
more code:

```text
missing config
bad ESC or stack reference
live cluster drift
provider or Helm behavior
real program bug
```

Do not run `pulumi up`, `pulumi destroy`, or `just up` unless there is an
explicit apply or destructive-action request.

## Adding A New Certificate Path

A good cert-manager integration has a clear owner and a narrow contract.

For an app certificate, define:

```text
namespace that owns the app
DNS names or service names on the certificate
Secret name the app will consume
Issuer or ClusterIssuer reference
reload behavior for the app or ingress controller
readiness check after issuance and renewal
```

For an issuer, define:

```text
issuer scope: namespace or cluster
signing authority: ACME, CA, self-signed, Vault, or another plugin
credential storage and rotation path
allowed consumers
challenge or signing proof path
failure blast radius
```

For an ACME issuer, the solver is part of the trust path. HTTP-01 needs the
public or routed HTTP challenge endpoint to be reachable by the ACME server.
DNS-01 needs DNS provider credentials and correct zone permissions. If a
challenge fails, inspect `Order` and `Challenge` resources before changing the
application Deployment.

For a private issuer, trust distribution is the hard part. Issuing a certificate
is not enough if clients do not trust the CA. Document how the trust bundle
reaches clients, how overlap works during CA rotation, and how old trust is
removed.

## Quick Reference

Controller install:

```bash
cd pulumi/core/security/cert-manager
NS="$(pulumi config get namespace --stack mx)"
kubectl get pods,deploy,svc,endpoints -n "$NS"
kubectl get crd | rg 'cert-manager.io|acme.cert-manager.io'
```

All cert-manager resources:

```bash
kubectl get certificate,certificaterequest,order,challenge,issuer,clusterissuer -A
```

One certificate:

```bash
kubectl describe certificate -n <namespace> <certificate>
kubectl describe certificaterequest -n <namespace> <request>
kubectl get events -n <namespace> --sort-by=.lastTimestamp
```

Issuer readiness:

```bash
kubectl describe issuer -n <namespace> <issuer>
kubectl describe clusterissuer <clusterissuer>
```

Consumer reference:

```bash
kubectl get ingress -n <namespace> <ingress> -o yaml
kubectl get secret -n <namespace> <secret> -o jsonpath='{.type}{"\n"}'
kubectl get pods -n <namespace>
```

Safe repo validation:

```bash
just sync pulumi/core/security/cert-manager
just check-python
just lint
git diff --check
just preview pulumi/core/security/cert-manager stack=mx
```

The shortest useful rule is: debug from intent to issuer to Secret to consumer.
If the `Certificate` is not ready, stay in cert-manager. If the Secret is ready
but the app is still wrong, move to the consumer. If many certificates fail at
once, suspect issuer or controller health before changing individual apps.
