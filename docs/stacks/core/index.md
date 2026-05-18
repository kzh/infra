# Core Services

Core stacks are the cluster's shared machinery: the routing layer, operator
layer, certificate layer, and secret-service layer that other stacks assume
already exist. They are not usually the product someone is trying to use, but
they often decide whether that product can be reached, trusted, scheduled,
reconciled, or configured.

That makes this area different from `apps`, `data`, or `ops`. A leaf app can be
broken while every core service is fine. A core service can also be the reason
several unrelated apps all fail at once. The job during an incident is to avoid
collapsing those cases together. Treat core as the place where you prove shared
platform assumptions before rewriting a consumer stack.

The pages under this section are practical runbooks for those shared
assumptions:

- [Networking](/stacks/core/networking/) covers how requests enter the cluster.
  [Tailscale Operator](/stacks/core/networking/tailscale) is the private-access
  path for tailnet services and the Kubernetes API proxy. [Cloudflare
  Tunnel](/stacks/core/networking/cf-tunnel) is the public-edge path for
  services that intentionally need public DNS, webhook reachability, or
  Cloudflare policy.
- [Operators](/stacks/core/operators/) covers controllers that reconcile custom
  resources after Pulumi submits them. [CloudNativePG](/stacks/core/operators/cnpg)
  manages PostgreSQL clusters, [MySQL Operator](/stacks/core/operators/mysql)
  manages MySQL `InnoDBCluster` resources, and
  [KubeRay](/stacks/core/operators/kuberay) manages Ray clusters. KubeRay also
  includes a small `ray-dev` cluster, so it is both an operator stack and an
  immediate consumer of that operator.
- [Security Services](/stacks/core/security/) covers trust and runtime secret
  primitives. [cert-manager](/stacks/core/security/cert-manager) installs the
  certificate controller and CRDs. [Vault](/stacks/core/security/vault) runs a
  small TLS-enabled in-cluster secrets service with private exposure.

## Think In Layers

Most core debugging is layer separation. A request, credential, certificate, or
custom resource crosses several ownership boundaries before a user sees success.
The useful question is not "is Kubernetes broken?" The useful question is
"which layer stopped carrying its part of the contract?"

For networking, the path is usually:

```text
client -> name -> public edge or tailnet proxy -> Kubernetes Service -> EndpointSlice -> pod -> app process
```

Tailscale and Cloudflare both eventually route to Kubernetes Services, but the
front half of the path is different. A Tailscale failure may involve client
tailnet state, ACLs, a proxy object, or a tailnet hostname. A Cloudflare failure
may involve DNS, tunnel reconciliation, Cloudflare policy, or connector health.
Once traffic reaches the cluster, both paths need the same backend truth: the
Service must select ready endpoints on the port the route expects.

For operators, the path is:

```text
Pulumi program -> CRD installed -> custom resource accepted -> operator reconciles -> generated pods/services/secrets -> consumer uses outputs
```

Pulumi can succeed while an operator later rejects or stalls a custom resource.
An operator can be running while one `Cluster`, `InnoDBCluster`, or `RayCluster`
is invalid. A custom resource can be ready while the consuming app has the
wrong hostname, secret, database name, port, or dependency image. Keep the
controller, the custom resource, the generated resources, and the consuming app
as separate checks.

For certificates and secrets, the path is:

```text
policy/config -> controller or service lifecycle -> Secret or secret API -> mounted/reference by app -> runtime reload/use
```

cert-manager can issue a valid Secret that an Ingress never references. Vault
can have a running pod while the service is sealed, uninitialized, missing an
auth method, or inaccessible from the intended client. A Kubernetes Secret can
exist while a workload still has stale env vars or a process that needs a
restart. Verify the material and verify the consumer accepted it.

## What Each Stack Owns

`pulumi/core/networking/tailscale` owns the Tailscale operator installation,
OAuth-backed reconciliation, API server proxy configuration, a default metrics
`ProxyClass`, a metrics Service, a ServiceMonitor, and Grafana dashboards. It
does not own each app hostname or backend Service. App stacks create their own
Tailscale ingress or annotated Service, usually keeping the backend Service as
`ClusterIP`.

`pulumi/core/networking/cf-tunnel` owns the Cloudflare Tunnel ingress
controller, tunnel credentials from config, the controller ServiceMonitor, and
tunnel dashboards. It does not make a public app safe or healthy. App stacks
still own the public hostname choice, authentication decision, backend Service,
ports, pods, and route intent.

`pulumi/core/operators/cnpg` owns the CloudNativePG controller and monitoring
hooks. The actual PostgreSQL cluster lives in the PostgreSQL data stack, and
apps consume that database through the data stack's outputs and Secrets. If an
app cannot connect to PostgreSQL, inspect the app and database cluster before
changing the CNPG operator.

`pulumi/core/operators/mysql` owns the shared MySQL operator and generated CRD
binding workflow. MediaWiki, WordPress, and similar consumers own their own
`InnoDBCluster` resources, database initialization jobs, router Services, and
application credentials. Operator health is necessary, but it is not proof that
an app database is usable.

`pulumi/core/operators/kuberay` owns the KubeRay controller and the repo's
small `ray-dev` cluster. That makes its blast radius wider than a pure
controller stack: changing it can affect the operator, the Ray CRDs, the dev
cluster image/version/resources, the Tailscale-exposed Ray client API, the
private dashboard ingress, PodMonitor scraping, and Ray Grafana dashboards.

`pulumi/core/security/cert-manager` owns the cert-manager controller, webhook,
CA injector, and CRD installation. It does not automatically own every issuer
or every certificate. The stack that defines a trust policy should own the
`Issuer`, `ClusterIssuer`, or `Certificate` that expresses it.

`pulumi/core/security/vault` owns a standalone Vault server, generated TLS
material for the server, chart values, private service exposure, and the
Kubernetes resources needed to run the service. A deployed Vault chart is not
the same thing as an initialized, unsealed, policy-configured, consumer-ready
Vault. Do not place root tokens, unseal keys, or secret values in docs, commits,
PR text, or chat.

## Controller Health Versus Consumer Health

Core services are shared controllers and primitives. Consumers are the app,
database, notebook, dashboard, or workflow stack that uses them. Debugging goes
faster when you decide which side of that boundary you are on.

Controller health answers questions like:

```text
is the controller pod running?
are the CRDs installed and discoverable?
is the webhook accepting requests?
can the controller authenticate to its external API?
are ServiceMonitor or PodMonitor objects selecting anything?
are controller reconcile errors increasing?
```

Consumer health answers different questions:

```text
does this Service have endpoints?
does this Ingress point at the intended Service and port?
is this custom resource ready?
did the operator-created router or head service appear?
does the app Secret contain the expected key names?
did the app reload the certificate or credential?
can the user reach the service from the same network path they use day to day?
```

Do not use controller health as a shortcut for consumer health. A healthy
Tailscale operator does not prove `spark` has endpoints. A healthy Cloudflare
controller does not prove a public app should be exposed. A healthy cert-manager
pod does not prove a particular `Certificate` is ready. A healthy Vault pod does
not prove Vault is unsealed or that a workload has a policy allowing reads.

Also avoid the reverse mistake. A single app returning 502 does not mean the
shared networking stack is broken. A single database consumer failing auth does
not mean the database operator needs an upgrade. Work from the consumer back to
the shared primitive, then stop at the first broken contract.

## Blast Radius

Core changes deserve slower blast-radius thinking than leaf changes. They sit
under multiple services, and many failures present as app-specific symptoms.

Networking changes can alter how private names resolve, how public routes reach
the cluster, how proxy metrics are scraped, and which backend Services receive
traffic. Hostname changes are user-facing API changes: people bookmark them,
paste them into scripts, and build habits around them. Public routes also
change the security contract. If a service only needs to be reached by trusted
devices, prefer the Tailscale path.

Operator upgrades can change CRD schemas, webhook behavior, default labels,
generated Services, pod templates, status fields, and reconciliation timing.
The operator stack preview may look small while the behavioral impact lands in
a consuming data or app stack. For CRD-backed changes, compile checks are only
the start; preview at least one representative consumer when the CRD surface or
operator defaults changed.

Security changes can affect every workload that relies on TLS issuance,
Kubernetes Secrets, Vault reachability, Vault policy, or service certificates.
Certificate rotation needs proof that the resource is ready and that the
consumer is serving or trusting the new material. Vault changes need proof of
service state, TLS behavior, seal state, auth method, policy, and consumer
access. Generated secrets and controller-owned secrets should not be hand-edited
as normal configuration.

Monitoring wiring is part of the blast radius. Many core stacks create
dashboards, ServiceMonitors, PodMonitors, or labels that the monitoring stack
expects. If a dashboard is empty after a chart upgrade, check scrape labels,
monitor selectors, metrics ports, and namespace selectors before editing the
dashboard JSON.

## Safe Verification

Start with read-only context and the repo's own commands:

```bash
git status --short
just projects
just check-python
just lint
git diff --check
```

For a core stack change, run the targeted preview for that stack:

```bash
just preview pulumi/core/networking/tailscale stack=mx
just preview pulumi/core/networking/cf-tunnel stack=mx
just preview pulumi/core/operators/cnpg stack=mx
just preview pulumi/core/operators/mysql stack=mx
just preview pulumi/core/operators/kuberay stack=mx
just preview pulumi/core/security/cert-manager stack=mx
just preview pulumi/core/security/vault stack=mx
```

Use apply commands only during an intentional apply window. For orientation,
review, and docs work, previews and read-only cluster checks are the right
default.

When verifying live behavior, keep the checks matched to the layer under test.
For the controller layer:

```bash
kubectl get pods -A | rg 'tailscale|cloudflare|cert-manager|vault|cnpg|mysql|ray'
kubectl get crd | rg 'tailscale|cert-manager|postgres|mysql|ray'
kubectl get servicemonitors,podmonitors -A | rg 'tailscale|cloudflare|cnpg|ray|mysql'
kubectl get events -A --sort-by=.lastTimestamp
```

For the routing layer:

```bash
kubectl get svc,endpoints,endpointslices -A | rg '<service-or-app>'
kubectl describe svc -n <namespace> <service>
kubectl describe ingress -n <namespace> <ingress>
kubectl get pods -n <namespace> --show-labels
```

For Tailscale paths, include client-side proof:

```bash
tailscale status
tailscale ping <tailnet-hostname>
```

For operator-backed resources:

```bash
kubectl describe <kind> -n <namespace> <name>
kubectl get events -n <namespace> --sort-by=.lastTimestamp
kubectl logs -n <operator-namespace> -l <operator-label-selector> --tail=200
```

For certificate paths:

```bash
kubectl get certificate,issuer,clusterissuer -A
kubectl describe certificate -n <namespace> <name>
kubectl describe certificaterequest -n <namespace> <name>
kubectl get secret -n <namespace> <tls-secret-name>
```

For Vault, verify service reachability and application state separately:

```bash
kubectl get pods,svc,endpoints,secrets -n vault
kubectl logs -n vault -l app.kubernetes.io/name=vault --tail=200
vault status
```

Do not paste secret values while verifying. It is fine to show the command that
retrieves a token, password, certificate, or key for local use; it is not fine
to copy the value into docs or status text.

## Incident Triage

When several apps fail at once, suspect shared primitives earlier. Look for
patterns: every private hostname failing points toward Tailscale, local tailnet
state, DNS, or shared backend assumptions. Every public hostname failing points
toward Cloudflare Tunnel, DNS, Cloudflare policy, or common backend selectors.
Several PostgreSQL-backed apps failing may point toward the PostgreSQL data
stack or CNPG. Several MySQL-backed apps failing may point toward the MySQL
operator or a shared chart behavior change. TLS failures across namespaces may
point toward cert-manager or issuer state. Secret-read failures across runtime
clients may point toward Vault availability, seal state, auth methods, or
policy.

When one app fails, start narrower. Prove its Deployment, Service, endpoints,
Ingress or Tailscale exposure, Secret references, and application logs. Move to
core only when the consumer points there: empty endpoints because an
operator-created Service selector drifted, an admission webhook error, a custom
resource stuck with useful status conditions, a missing CRD, a controller auth
error, or a shared proxy with reconcile failures.

The durable fix should land where the desired state lives. If a Service selector
is wrong in an app stack, fix the app stack. If an operator-owned Service keeps
being regenerated with the wrong shape, fix the custom resource or controller
configuration. If an ingress route points to the wrong backend, fix the route in
the owning stack. If a certificate never issues, fix the issuer, certificate,
challenge, or controller health. Manual Kubernetes patches are useful for
diagnosis and emergency recovery, but they are rarely the final repo-backed
answer.

## Before You Change Core

Before editing a core stack, write down the contract you intend to preserve:
hostnames, ports, namespaces, output names, CRD versions, resource names,
Service selectors, monitoring labels, and any consumer that depends on them.
Renames can cause replacement. Chart upgrades can change labels. CRD upgrades
can change generated SDKs. Credential rotation can break reconciliation even if
the Pulumi preview looks structurally small.

Then choose at least one representative consumer to verify after the core
change. For Tailscale, verify an existing private service from the laptop. For
Cloudflare, verify one public route and its backend endpoints. For CNPG, verify
the shared PostgreSQL cluster status and one app connection path. For MySQL,
verify an existing `InnoDBCluster`, its router Service, and an app database init
or connection path. For KubeRay, verify the `RayCluster`, dashboard ingress,
client Service, and a small job or dashboard view. For cert-manager, verify a
real `Certificate` and the app or ingress using its Secret. For Vault, verify
pod readiness, service endpoints, TLS, seal state, and one intended auth/read
path without exposing values.

Core services are valuable because they make the rest of the cluster boring to
operate. The safest work here is explicit about ownership, careful about
consumer contracts, private by default where possible, and verified at the same
layer where the change took effect.
