# Tailscale Operator

Source: `pulumi/core/networking/tailscale`

The Tailscale operator is the private-access layer for this cluster. It lets
selected Kubernetes Services become reachable from trusted tailnet devices
without turning those Services into public internet endpoints.

Start from the Kubernetes model. A normal `ClusterIP` Service is only an
internal load-balancing name. It selects pods, Kubernetes writes EndpointSlices
for the ready backends, and traffic from inside the cluster can reach the app on
the Service port. Nothing about that gives a laptop, phone, desktop, or remote
automation a route to the Service.

Tailscale adds that missing private client path. An app stack declares, "this
internal HTTP route or TCP Service should have a tailnet name." The operator
reconciles that intent into Tailscale proxy resources. A client already signed
into the tailnet can then use the private name, and the proxy forwards traffic
back to the Kubernetes Service. The app stays internal to Kubernetes; the
tailnet becomes the access boundary.

That is the default model for admin surfaces in this repo. Dashboards,
notebooks, workflow UIs, databases, private APIs, Spark/Ray/Trino-style tools,
and operational consoles should normally use Tailscale unless there is a real
reason for a public Cloudflare route.

## Ownership Model

This stack owns the shared operator layer. It does not own every private
hostname, every exposed app, or every backend port.

`pulumi/core/networking/tailscale` owns:

- the `tailscale` namespace
- the `tailscale-operator` Helm release from the Tailscale chart repo
- the OAuth client configuration consumed by the operator
- API server proxy support for `tailscale configure kubeconfig`
- the default `ProxyClass` used by generated proxies
- operator and proxy metrics wiring
- Grafana dashboard ConfigMaps for operator/proxy behavior
- the repo-local Tailscale CRD input used to generate typed `ProxyClass`
  bindings

The app stack owns:

- the application pods
- the Kubernetes Service and selector
- the Service port and target port
- the HTTP ingress or Service annotations that request Tailscale exposure
- the tailnet hostname chosen for that service
- any app-level base URL, callback URL, advertised listener, or client output

That split matters during debugging. If a private URL returns a 502 because the
backend Service has no endpoints, the Tailscale operator stack is probably not
the right place to edit. If every Tailscale hostname fails or the operator
cannot create proxies, then this stack is much more likely to be involved.

## What This Stack Installs

The Pulumi entrypoint installs the operator with conservative defaults and then
adds observability around it.

```text
Namespace:                  tailscale
Helm release:               tailscale-operator
Chart:                      tailscale-operator
Chart repository:           https://pkgs.tailscale.com/helmcharts
Default chart version:      1.96.5
API server proxy:           enabled
Default ProxyClass:         tailscale-default-metrics
Operator metrics Service:   tailscale-operator-metrics
Operator metrics port:      8080
Monitoring release label:   kube-prometheus-stack by default
Dashboard files:            tailscale-operator-overview.json
                            tailscale-proxy-metrics.json
```

The operator needs a Tailscale OAuth client. In Pulumi config those inputs are
`TS_CLIENT_ID` and `TS_CLIENT_SECRET` under the `tailscale` project namespace.
Treat both as sensitive operational metadata. Do not copy decrypted values,
client IDs, client secrets, tailnet suffixes, private hostnames, or raw stack
outputs into docs, commit messages, or chat. If an operator needs the current
value, retrieve it locally through Pulumi config or ESC-aware tooling rather
than preserving it in Markdown.

The Helm values set:

```python
"oauth": {
    "clientId": config.require("TS_CLIENT_ID"),
    "clientSecret": config.require_secret("TS_CLIENT_SECRET"),
},
"apiServerProxyConfig": {
    "mode": "true",
},
"proxyConfig": {
    "defaultProxyClass": "tailscale-default-metrics",
},
```

The `apiServerProxyConfig` setting is what makes the Kubernetes API reachable
through Tailscale after local kubeconfig setup. The `defaultProxyClass` setting
causes app-created Tailscale proxies to inherit the repo's metrics behavior
unless an exposure deliberately opts into a different class.

## Operator Resources

The operator itself is a Helm-managed workload in the `tailscale` namespace.
When it is healthy, it watches Kubernetes resources that request Tailscale
exposure and creates proxy workloads for them.

Check the operator layer with:

```bash
kubectl get deploy,pods,svc,servicemonitor -n tailscale
kubectl get proxyclass tailscale-default-metrics
kubectl logs -n tailscale -l app=operator --tail=200
```

`ProxyClass` is a cluster-scoped Tailscale CRD. This stack creates
`tailscale-default-metrics` with proxy metrics enabled and ServiceMonitor
creation enabled:

```yaml
apiVersion: tailscale.com/v1alpha1
kind: ProxyClass
metadata:
  name: tailscale-default-metrics
spec:
  metrics:
    enable: true
    serviceMonitor:
      enable: true
      labels:
        release: kube-prometheus-stack
```

The generated CRD bindings live under `pulumi/lib/tailscale_crds`. Do not edit
those generated SDK files by hand. If the operator CRD changes, regenerate the
bindings with the repo CRD workflow and then run the normal Python/lint checks.

The operator also creates resources in response to app intent. For a Tailscale
Ingress or exposed Service, expect proxy pods, Services, and metrics Services
to appear in or around the operator namespace. Those are reconciled resources,
not hand-managed app infrastructure. Debug them, but prefer fixing the Pulumi
resource that requested exposure over manually patching generated proxy
objects.

## Private Exposure Shapes

Most Tailscale exposure in this repo uses one of three shapes.

HTTP UIs use a normal Kubernetes Ingress with the Tailscale ingress class. The
backend Service stays `ClusterIP`.

```python
k8s.networking.v1.Ingress(
    "example-ingress",
    spec=k8s.networking.v1.IngressSpecArgs(
        ingress_class_name="tailscale",
        rules=[
            k8s.networking.v1.IngressRuleArgs(
                host="example-ui",
                http=k8s.networking.v1.HTTPIngressRuleValueArgs(
                    paths=[
                        k8s.networking.v1.HTTPIngressPathArgs(
                            path="/",
                            path_type="Prefix",
                            backend=k8s.networking.v1.IngressBackendArgs(
                                service=k8s.networking.v1.IngressServiceBackendArgs(
                                    name="example-ui",
                                    port=k8s.networking.v1.ServiceBackendPortArgs(
                                        number=8080,
                                    ),
                                ),
                            ),
                        )
                    ],
                ),
            )
        ],
        tls=[k8s.networking.v1.IngressTLSArgs(hosts=["example-ui"])],
    ),
)
```

Raw TCP, database, API, and service-level exposures usually annotate a
`ClusterIP` Service directly:

```yaml
apiVersion: v1
kind: Service
metadata:
  name: example-api
  annotations:
    tailscale.com/expose: "true"
    tailscale.com/hostname: example-api
spec:
  type: ClusterIP
  ports:
    - name: api
      port: 15002
      targetPort: 15002
      protocol: TCP
```

Some chart or operator integrations wrap the same idea in their own values. A
Helm chart may accept Service annotations, a database operator may have a
service template, and a streaming operator may express a Tailscale listener as a
load-balancer class plus hostname annotations. When debugging those stacks,
inspect the rendered Kubernetes Service. The client path still ends at a
Service, selector, EndpointSlice, target port, and app process.

The safe default is `ClusterIP` plus Tailscale. Do not change a private service
to `NodePort`, public `LoadBalancer`, or Cloudflare exposure just to make it
reachable from a laptop. If the caller can join the tailnet, keep the service
private.

## Tailnet Names

Tailnet names are user-facing API. People bookmark them, put them in kubeconfig,
paste them into CLI configs, and build small scripts around them. Treat a
hostname change like a compatibility change, even if the Kubernetes object
rename looks small.

In this repo, app stacks usually configure the short service name, such as
`example-ui` or `example-api`. The full MagicDNS name is that service name plus
the private tailnet suffix. The suffix is not documentation material. Discover
it locally when needed:

```bash
tailscale status --json | jq -r '.CurrentTailnet.MagicDNSSuffix'
```

Use placeholders in committed docs and examples:

```text
<tailnet-hostname>
<tailnet-suffix>
<tailnet-hostname>.<tailnet-suffix>
https://<tailnet-hostname>.<tailnet-suffix>
```

Prefer stable, boring hostnames:

- one obvious name for one user-facing surface
- separate names for separate protocols or roles
- names that describe the client contract, not the current chart internals
- no private tailnet suffix committed unless the value is intentionally public
  and reviewed as safe

For HTTP services, the hostname is usually enough. For raw TCP services, the
port is part of the contract. A hostname by itself is not a complete client
instruction for PostgreSQL, Kafka, Spark Connect, Trino, ClickHouse, Ray
Client, or an S3-compatible API.

If an app needs a full base URL internally, keep that value in stack config or
an app-owned output and review it as private metadata. Do not make this operator
page a registry of live private URLs.

## Kubernetes API Proxy

The operator is configured with API server proxy support. That gives trusted
tailnet clients a private path to the Kubernetes API without exposing the API on
the public internet.

The local setup command is:

```bash
tailscale configure kubeconfig <operator-hostname>
```

Be careful with kubeconfig state. The Tailscale command can add or update a
context and may change the current context. Preserve the current context before
running it and restore it after verification unless you intentionally want to
switch:

```bash
previous_context="$(kubectl config current-context)"
cp ~/.kube/config ~/.kube/config.backup-$(date +%Y%m%d%H%M%S)
tailscale configure kubeconfig <operator-hostname>
kubectl get --raw /version
kubectl auth can-i get pods -A
kubectl config use-context "$previous_context"
```

Use the local `tailscale` CLI as the source of truth for the active tailnet and
operator hostname. Do not infer the suffix from old notes or paste kubeconfig
contents into docs. Kubeconfig can contain private routes and credentials.

## Client Checks

Always test from the kind of client that is failing. A Service can work from a
pod and still fail from a laptop because Tailscale adds DNS, tailnet device
state, ACLs, local routing, and direct-vs-relayed transport to the path.

Start with identity and name resolution:

```bash
tailscale status
tailscale status --json | jq -r '.CurrentTailnet.MagicDNSSuffix'
tailscale ping <tailnet-hostname>
```

For HTTP:

```bash
curl -I https://<tailnet-hostname>
curl -I https://<tailnet-hostname>.<tailnet-suffix>
```

For TCP:

```bash
nc -vz <tailnet-hostname> <port>
```

Then use the real client if one exists: `psql`, `kcat`, `spark-shell`,
`trino`, `rclone`, a browser, or the app's CLI. A generic port check proves
that something is reachable. It does not prove protocol, auth, TLS, advertised
listeners, redirects, or base URLs are correct.

Read `tailscale ping` as a network-path hint. A failed ping or one-device-only
failure points toward device state, ACLs, DNS, or local routing. A DERP-routed
path may still work, but if a normally local service is slow or flaky, direct
vs relayed transport is worth checking before editing Kubernetes.

## Debug A Private Service

Use the same path every time. It keeps the investigation grounded and prevents
edge changes from hiding backend problems.

```text
client device
  -> tailnet DNS/name
  -> Tailscale proxy created by the operator
  -> Kubernetes Service
  -> EndpointSlice entries for ready pods
  -> pod IP and target port
  -> app process
```

First prove the app backend:

```bash
kubectl get svc,endpoints,endpointslice -n <namespace> <service>
kubectl describe svc -n <namespace> <service>
kubectl get pods -n <namespace> --show-labels
kubectl logs -n <namespace> -l <app-label-selector> --tail=200
```

Compare the Service selector to pod labels:

```bash
kubectl get svc -n <namespace> <service> -o jsonpath='{.spec.selector}'
kubectl get pods -n <namespace> --show-labels
```

If endpoints are empty, fix the app stack or the operator-owned backend Service
contract before changing Tailscale. Common causes are chart label changes,
renamed Services, pods that are not Ready, namespace mismatches, and
`targetPort` values that no longer match the app.

Then prove the exposure intent:

```bash
kubectl get ingress,svc -A | rg -i 'tailscale|<hostname>|<service>'
kubectl describe ingress -n <namespace> <ingress>
kubectl describe svc -n <namespace> <service>
```

Then inspect the operator and generated proxy resources:

```bash
kubectl get deploy,pods,svc,servicemonitor -n tailscale
kubectl get proxyclass tailscale-default-metrics -o yaml
kubectl get sts,pods,svc -n tailscale | rg -i '<hostname>|<namespace>|<service>'
kubectl logs -n tailscale -l app=operator --tail=200
```

Finally test from the client:

```bash
tailscale status
tailscale ping <tailnet-hostname>
curl -I https://<tailnet-hostname>
```

For TCP services, replace `curl` with a port check and the real protocol
client.

## Failure Patterns

A 502 from a Tailscale HTTPS name usually means the proxy exists and reached
the Kubernetes side, but the backend is wrong, empty, not ready, or speaking a
different protocol than the route expects. Start with `svc`, `endpoints`, and
`endpointslice`.

A timeout can be a client path problem, a proxy problem, or a backend listener
problem. Check `tailscale ping`, generated proxy pods, Service endpoints, and
the app logs before deciding which layer owns it.

A hostname that never appears in `tailscale status` usually means the exposure
intent did not reconcile, the hostname is different from what the client is
checking, the operator is unhealthy, or Tailscale-side policy/auth blocked the
device creation. Inspect the Ingress or annotated Service, then operator logs.

A service that works from one tailnet device but not another is usually not a
Kubernetes selector problem. Check the device's Tailscale state, DNS, ACLs,
route selection, and whether the failing client is using the same hostname and
port.

A TLS or redirect loop on an HTTP service often means the ingress host, TLS host
list, app base URL, or client URL do not agree. Check the app's own configured
external URL in the app stack before changing the operator.

A TCP client that connects but fails protocol negotiation usually means the
Tailscale path is alive but the advertised host, TLS mode, authentication, or
port-specific protocol config is wrong. Kafka and similar systems are especially
sensitive to advertised listener values.

Missing proxy metrics do not prove the service is down. Check the
`tailscale-default-metrics` `ProxyClass`, the generated metrics Service, and
ServiceMonitor labels. Metrics are debugging support, not the access path
itself.

## Metrics And Dashboards

This stack creates two kinds of observability.

The operator metrics Service is explicit Pulumi:

```text
Service name:   tailscale-operator-metrics
Namespace:      tailscale
Selector:       app=operator
Port:           metrics / 8080
ServiceMonitor: tailscale-operator
```

The proxy metrics behavior comes from `tailscale-default-metrics`. The
Tailscale CRD describes per-proxy metrics labels such as proxy type, parent
name, and parent namespace. Those labels are useful when one app's proxy is
misbehaving but the operator is otherwise healthy.

Grafana dashboard ConfigMaps are loaded from:

```text
pulumi/core/networking/tailscale/dashboards/tailscale-operator-overview.json
pulumi/core/networking/tailscale/dashboards/tailscale-proxy-metrics.json
```

If the dashboards are empty, check scrape discovery before editing the JSON:

```bash
kubectl get servicemonitor -n tailscale tailscale-operator -o yaml
kubectl get svc -n tailscale tailscale-operator-metrics -o yaml
kubectl get proxyclass tailscale-default-metrics -o yaml
```

The dashboard ConfigMap namespace is configurable separately from the Tailscale
namespace. The default monitoring release label is `kube-prometheus-stack`;
changing that label affects Prometheus discovery for the ServiceMonitors.

## Safe Exposure Changes

Before changing exposure, decide which stack owns the behavior.

Change an app stack when:

- a single app needs a new private hostname
- a Service annotation needs to be added or removed
- an ingress backend, port, host, or TLS host needs to change
- a chart value should publish an app Service through Tailscale
- a client-facing output or app base URL needs to match the private route

Change this Tailscale stack when:

- the operator chart version changes
- the OAuth client configuration changes
- API server proxy behavior changes
- default proxy metrics behavior changes
- ServiceMonitor or dashboard ownership changes
- Tailscale CRDs and generated bindings need to move together

For a new private HTTP UI, the conservative app-stack pattern is:

```text
1. create or identify a ClusterIP Service
2. verify its selector and endpoints locally
3. add a Tailscale Ingress with a stable short hostname
4. include TLS hosts for the same hostname
5. preview the app stack
6. after an approved apply, test from a real tailnet client
```

For a new TCP or service-level endpoint:

```text
1. create or identify a ClusterIP Service
2. keep the port name and number clear
3. add tailscale.com/expose and tailscale.com/hostname annotations
4. preview the app stack
5. after an approved apply, test the real client protocol from the tailnet
6. document the hostname and port without committing private suffixes
```

For this operator stack, run the cheap checks before preview:

```bash
just sync pulumi/core/networking/tailscale
just check-python
just lint
git diff --check
just preview pulumi/core/networking/tailscale stack=mx
```

For an app-stack exposure change, run the same cheap gates and preview the app
stack that owns the Service or Ingress:

```bash
just check-python
just lint
git diff --check
just preview pulumi/<area>/<service> stack=mx
```

Do not apply exposure changes casually. A hostname is a client contract. OAuth
credential rotation can stop reconciliation. Chart upgrades can change labels
and break ServiceMonitor discovery. CRD changes need generated bindings. Public
exposure is a separate security decision, not a shortcut for private access.

After an approved apply, verify one existing private service in addition to the
new or changed one. That catches operator-wide regressions that a single happy
path can miss.
