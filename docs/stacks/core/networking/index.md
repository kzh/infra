# Networking

Networking docs should make one thing easy to answer: how does a request reach
the service, and where can it fail?

This repo has two main exposure layers. Use
[Tailscale Operator](/stacks/core/networking/tailscale) for private services
that should only be reachable by devices and identities on the tailnet. Use
[Cloudflare Tunnel](/stacks/core/networking/cf-tunnel) when a service
deliberately needs the public edge, public DNS, Cloudflare policy, external
webhook reachability, or internet-facing product traffic.

Both paths eventually converge on the same Kubernetes primitives: a Service
selects pods, EndpointSlices record the ready backends, and the app process
answers on the selected port. The earlier hops are different enough that the
first debugging question should always be "which exposure layer owns this
request?"

## Request Paths

Think about request paths as chains, not as isolated resources. A healthy pod is
not enough. A valid DNS record is not enough. A reconciled ingress object is not
enough. The request only works when every hop forwards to the next one.

For a private HTTP service exposed through Tailscale ingress, the path is:

```text
tailnet client
  -> tailnet hostname
  -> Tailscale proxy for the ingress
  -> Kubernetes Service named by the ingress backend
  -> EndpointSlice entries for ready pods
  -> pod IP and app port
  -> app process
```

For a private TCP or service-level exposure through Tailscale annotations, the
path is similar but skips HTTP ingress semantics:

```text
tailnet client
  -> tailnet hostname and port
  -> Tailscale proxy for the Service
  -> Kubernetes Service port
  -> EndpointSlice entries for ready pods
  -> pod IP and target port
  -> app process
```

For a public service exposed through Cloudflare Tunnel, the public edge and the
cluster tunnel connector sit in front of the same Kubernetes backend:

```text
internet client or webhook sender
  -> public DNS
  -> Cloudflare edge
  -> cloudflared connector managed by the tunnel controller
  -> Kubernetes ingress or Service route
  -> EndpointSlice entries for ready pods
  -> pod IP and app port
  -> app process
```

The common tail of all three paths is where many incidents hide:

```text
Kubernetes Service -> selector -> ready pod endpoints -> target port -> app
```

If that tail is broken, switching from Tailscale to Cloudflare or changing DNS
will usually create another broken route to the same backend.

## Choosing Private Or Public

Default to private when the audience is the operator, the owner, trusted
devices, or other machines already on the tailnet. Dashboards, notebooks,
database UIs, admin panels, workflow tools, one-person services, internal APIs,
and raw TCP systems normally belong here. The usual shape is a `ClusterIP`
Service plus either `ingressClassName: tailscale` for HTTP or
`tailscale.com/expose: "true"` on a Service for TCP or service-level exposure.

Choose Cloudflare Tunnel when the public edge is part of the requirement. Good
reasons include public websites, external webhooks, OAuth callbacks from
systems outside the tailnet, third-party integrations that cannot join the
tailnet, or routes that intentionally rely on Cloudflare Access, WAF, DNS, or
edge logs. A public route should come with an access decision: who is expected
to reach it, what authentication protects the app, what Cloudflare policy is in
front of it, and who owns the hostname.

Avoid public exposure for convenience alone. If the only caller is a laptop,
phone, server, or workflow that can join the tailnet, Tailscale is the smaller
security and operations surface. A public hostname creates a different contract:
internet clients can find the route, scanners can reach the edge, abuse and
availability become public concerns, and application auth mistakes matter more.

Some services may have both private and public paths, but that should be an
explicit product decision. Keep one canonical user URL where possible. If there
are separate public and private routes, document which one is for people, which
one is for automation, and whether they hit the same backend Service. Duplicate
routes are easy to forget during chart upgrades, selector changes, and port
renames.

## Debug From The Backend Out

When a URL fails, prove the backend before spending time on the edge. This
short loop catches selector drift, readiness problems, wrong namespaces, and
port mismatches:

```bash
kubectl get ingress,svc,endpoints,endpointslice -n <namespace>
kubectl describe svc -n <namespace> <service>
kubectl get pods -n <namespace> --show-labels
kubectl describe pod -n <namespace> <pod>
```

For a specific Service, compare the selector to pod labels:

```bash
kubectl get svc -n <namespace> <service> -o jsonpath='{.spec.selector}'
kubectl get pods -n <namespace> --show-labels
```

Then check whether the Service has endpoints:

```bash
kubectl get endpoints -n <namespace> <service>
kubectl get endpointslice -n <namespace> -l kubernetes.io/service-name=<service>
```

If endpoints are empty, the route layer is usually not the first thing to fix.
Look for a selector that no longer matches, pods that are not Ready, an app
listening on a different port than the Service targetPort, or an app stack that
renamed the backend Service without updating the ingress.

If endpoints exist, move one hop outward. For Tailscale, inspect the ingress or
annotated Service that requested private exposure, then the operator/proxy
state. For Cloudflare, inspect the app ingress/route intent, then the tunnel
controller logs and Cloudflare-side route state.

## Debugging 502s

A 502 normally means an upstream proxy reached something but did not get a
usable backend response. In this repo, that often means the Tailscale proxy or
Cloudflare connector exists, but the Kubernetes backend is wrong, empty, not
ready, or speaking a different protocol than the route expects.

Start with the backend:

```bash
kubectl get svc,endpoints,endpointslice -n <namespace> <service>
kubectl describe ingress -n <namespace> <ingress>
kubectl logs -n <namespace> -l <app-label-selector> --tail=200
```

For Tailscale-routed HTTP, also check:

```bash
kubectl get ingress -A | rg -i 'tailscale|<hostname>|<service>'
kubectl logs -n tailscale -l app=operator --tail=200
tailscale status
tailscale ping <tailnet-hostname>
```

For Cloudflare-routed HTTP, also check:

```bash
kubectl get pods -n cloudflare-tunnel
kubectl logs -n cloudflare-tunnel -l app.kubernetes.io/name=cloudflare-tunnel-ingress-controller --tail=200
curl -I https://<public-hostname>
```

Read the failure by layer. Empty endpoints point at selectors/readiness. A
connection refused error points at targetPort or app listener behavior. A TLS
or host mismatch points at ingress hostnames, app base URL settings, or backend
protocol expectations. Tunnel controller auth errors point at Cloudflare config
or credentials, but do not paste token or account values into docs, commits, or
chat.

Avoid "fixing" a 502 by adding a second route before proving the backend. A new
route can hide the original problem and leave dead Services behind. The durable
fix is usually smaller: correct the Service selector, restore the expected port
name, update the ingress backend, preserve a hostname, or make the app stack own
the Service that the routing layer expects.

## Debugging Zero Endpoints

Zero endpoints means Kubernetes does not have ready backend addresses for the
Service. It is a backend selection problem, even if the symptom appears as a
public 502 or a tailnet timeout.

Use the Service selector as the source of truth:

```bash
kubectl get svc -n <namespace> <service> -o yaml
kubectl get pods -n <namespace> --show-labels
kubectl get endpointslice -n <namespace> -l kubernetes.io/service-name=<service> -o yaml
```

Common causes:

- the chart changed pod labels but the Service selector stayed on the old label
- the app stack renamed a Service and the ingress still points at the old name
- pods exist but are not Ready, so they are withheld from normal endpoints
- the Service is in the wrong namespace for the backend pods
- the Service port or `targetPort` no longer matches the app container
- an operator-owned Service was replaced or reconciled differently than Pulumi
  expected

Do not patch around selector drift by hand unless the user explicitly wants a
temporary live repair. For repo-managed services, put the fix in the owning
Pulumi stack, run the cheap checks, and preview the changed stack. If an
operator keeps rewriting a Service, the durable fix may be to create a
Pulumi-owned Service with the selector and port contract the route needs.

## Debugging Tailnet Access

Tailnet access has two sides: the Kubernetes resources that requested exposure
and the client device trying to use that private name.

Check Kubernetes first:

```bash
kubectl get ingress,svc -A | rg -i 'tailscale|<service>|<hostname>'
kubectl describe ingress -n <namespace> <ingress>
kubectl describe svc -n <namespace> <service>
kubectl get proxyclass tailscale-default-metrics
kubectl logs -n tailscale -l app=operator --tail=200
```

Then check from the client:

```bash
tailscale status
tailscale ping <tailnet-hostname>
curl -I https://<tailnet-hostname>
```

If `tailscale ping` is slow, relayed, or fails from only one device, the problem
may be local device state, ACLs, DNS, or tailnet routing rather than
Kubernetes. If the hostname never appears in `tailscale status`, check whether
the ingress or Service annotation exists and whether the operator reconciled it.
If the name resolves but the app fails with 502, go back to Service endpoints.

The Tailscale stack also enables the API server proxy. When repairing kubeconfig
access through that path, protect the existing local context before changing it:

```bash
previous_context="$(kubectl config current-context)"
cp ~/.kube/config ~/.kube/config.backup-$(date +%Y%m%d%H%M%S)
tailscale configure kubeconfig <operator-hostname>
kubectl config use-context "$previous_context"
kubectl get --raw /version
```

That command can update the current context. Restore the prior context after
verification unless the user asked to switch.

## Debugging Cloudflare Tunnel Access

Cloudflare Tunnel adds public DNS, Cloudflare policy, and the tunnel connector
in front of Kubernetes. It is useful when the public edge is intentional, but it
also means there are more layers to classify.

Check the app backend first:

```bash
kubectl get ingress,svc,endpoints,endpointslice -n <namespace>
kubectl describe ingress -n <namespace> <ingress>
kubectl describe svc -n <namespace> <service>
```

Then check the tunnel controller:

```bash
kubectl get pods -n cloudflare-tunnel
kubectl logs -n cloudflare-tunnel -l app.kubernetes.io/name=cloudflare-tunnel-ingress-controller --tail=200
kubectl get servicemonitors -n cloudflare-tunnel
```

If the backend has endpoints and the tunnel controller is healthy, move to the
Cloudflare route, hostname, policy, and DNS state. Keep secret values out of
terminal transcripts and docs. Summaries like "controller cannot authenticate"
or "route not reconciled" are enough for handoff notes.

For public incidents, separate origin health from edge behavior in the report:
whether the app Service had endpoints, whether the controller had healthy pods,
whether public DNS reached Cloudflare, what HTTP status the edge returned, and
which Pulumi stack owns the route.

## Safe Network Changes

Networking changes are user-facing even when the diff is small. Hostnames,
Service names, port names, selectors, and ingress classes become contracts with
people, scripts, dashboards, and third-party systems.

Before changing exposure, answer these questions in the owning app stack:

- is the service private, public, or intentionally both?
- which stack owns the app Service and route?
- is the backend Service `ClusterIP` unless there is a strong reason otherwise?
- does the Service selector still match the pod labels rendered by the chart?
- do the Service ports match the container ports and app protocol?
- will a hostname or resource rename require a migration or alias?
- what existing route should be tested after apply?

Use the repo workflow for code changes:

```bash
just sync pulumi/<area>/<service>
just check-python
just lint
git diff --check
just preview pulumi/<area>/<service> stack=mx
```

For changes to the shared networking stacks:

```bash
just preview pulumi/core/networking/tailscale stack=mx
just preview pulumi/core/networking/cf-tunnel stack=mx
```

Do not run `pulumi up`, `pulumi destroy`, or `just up` unless the user
explicitly asks for an apply or destructive action. If a preview fails, classify
the blocker before editing more code: missing config, ESC reference, live-state
drift, provider behavior, chart migration, or a real program bug.

Prefer durable Pulumi changes over one-off live patches. A live patch can be
useful to restore service during an incident, but the repo should still end up
owning the intended Service selector, route, hostname, and port contract. When
an operator owns the conflicting resource, either configure that operator
through supported values or add a separate Pulumi-owned resource with a stable
name and clear ownership.

After apply, verify the actual request path. For private routes, test from a
tailnet client and check `tailscale ping`. For public routes, test the public
hostname and the tunnel controller. In both cases, verify the backend Service
endpoints before declaring the route healthy.
