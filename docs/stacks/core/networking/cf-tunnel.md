# Cloudflare Tunnel

Source: `pulumi/core/networking/cf-tunnel`

Cloudflare Tunnel is the public-edge ingress path for this cluster. It is the
piece to reach for when a service deliberately needs public DNS, Cloudflare
policy, external webhook reachability, OAuth callbacks from systems outside the
tailnet, or internet-facing product traffic.

The important word is deliberately. A public route is not just another way to
reach a pod. It changes the service's audience, failure modes, logging surface,
and security contract. If the only callers are the operator, trusted devices,
or machines that can join the tailnet, use the Tailscale path instead. If a
third party on the public internet must reach the service, Cloudflare Tunnel is
the repo-managed bridge from the public edge back into Kubernetes.

## Request Path

Start with the request path, because most tunnel incidents are really broken
chains. A request only works when every hop can forward to the next one:

```text
internet client or webhook sender
  -> public DNS
  -> Cloudflare edge
  -> Cloudflare Tunnel for this account and tunnel name
  -> cloudflared connector inside the cluster
  -> route created from a Kubernetes Ingress
  -> Kubernetes Service
  -> ready EndpointSlice entries
  -> pod IP and target port
  -> app process
```

The first half of that chain is public-edge infrastructure. The last half is
ordinary Kubernetes service discovery. Keeping those halves separate makes
debugging much calmer. Cloudflare can be healthy while the Service has no
endpoints. The app can be healthy while DNS points at the wrong hostname. A
valid Ingress can still route to the wrong service port.

When a public URL fails, resist the urge to begin by changing tunnel
credentials or adding another route. First prove the backend Service has ready
endpoints and that the app is listening on the port the route expects. Then
move outward through the Ingress, controller, tunnel transport, and Cloudflare
edge.

## What This Stack Owns

`pulumi/core/networking/cf-tunnel` owns the shared controller layer. It does
not own application routes.

Pulumi creates:

```text
Namespace:             cloudflare-tunnel by default
Helm chart:            cloudflare-tunnel-ingress-controller
Chart version:         0.0.23
Helm release name:     cloudflare-tunnel-b6e117c1
Repository:            https://helm.strrl.dev
Tunnel name:           mx0 by default
Metrics path:          /metrics
Metrics interval:      30s
Monitoring selector:   release=kube-prometheus-stack by default
```

The stack passes these Cloudflare values into the chart:

```text
cloudflareTunnelApiToken   Pulumi secret config
cloudflareAccountId        Pulumi config
tunnelName                 Pulumi config, defaulting to mx0
```

Those are configuration inputs, not documentation values. Do not paste token,
account, tunnel, kubeconfig, or private hostname values into docs, commits, PR
text, or chat. It is fine to document the config key names and the shape of the
contract.

The stack also creates a `ServiceMonitor` named `cloudflare-tunnel` in the
tunnel namespace. Prometheus discovers the controlled `cloudflared` metrics
service through these labels:

```text
app.kubernetes.io/component=controlled-cloudflared
app.kubernetes.io/name=cloudflare-tunnel-ingress-controller
```

Finally, the stack loads two Grafana dashboard JSON files as ConfigMaps with
`grafana_dashboard=1` so the monitoring stack can import them:

```text
dashboards/cloudflare-tunnel-overview.json
dashboards/cloudflare-tunnel-transport.json
```

That is the boundary. Change this stack when the shared tunnel controller,
chart, credentials wiring, metrics discovery, or dashboards need to change.
Change the app stack when a hostname, path, Service, port, or public exposure
decision needs to change.

## What App Stacks Own

An app stack owns the actual route intent. In this repo, a Cloudflare-routed
HTTP app uses a Kubernetes Ingress whose class is `cloudflare-tunnel`.

A minimal shape looks like this:

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: public-webhook
  namespace: app-namespace
spec:
  ingressClassName: cloudflare-tunnel
  rules:
    - host: app.example.invalid
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: app-service
                port:
                  number: 80
```

Use placeholder domains like `example.invalid` in docs. Do not copy real public
hostnames unless they are already intended to be public documentation.

The app stack still owns:

```text
the application Deployment or chart
the Kubernetes Service
the Service selector
the Service port and targetPort
the Ingress host, path, class, and backend reference
the app's own authentication and authorization
the external API contract for the hostname
```

The Cloudflare Tunnel controller can only route to what Kubernetes exposes. It
cannot make pods Ready, repair a Service selector, guess a target port, add app
auth, or make an app listen on the correct interface. For that reason,
Cloudflare-routed app pages should always include backend endpoint checks next
to public URL checks.

The `pulumi/apps/stitch` stack is the current example of this pattern. Its
chart values enable an ingress, set the ingress class to `cloudflare-tunnel`,
and provide the webhook host from config. That same app also has private
Tailscale service exposure, which is allowed when the two routes serve different
audiences. The public route is for external webhook delivery; the private route
is for trusted operator access.

## Layers

Think in three layers: DNS, tunnel, and origin.

The DNS and edge layer is everything the public client sees before traffic
enters the tunnel. It includes the public hostname, Cloudflare zone behavior,
Cloudflare Access or WAF policy if used, TLS at the edge, caching behavior if
enabled, and Cloudflare's own edge logs. Failures here often look like the
hostname not resolving, the wrong Cloudflare zone receiving the request, a
policy challenge, or an edge response before the request reaches Kubernetes.

The tunnel layer is the shared connector path. The controller authenticates to
Cloudflare with configured account and token data, keeps the tunnel route state
in sync with Kubernetes ingress intent, and runs `cloudflared` connectors that
hold outbound connections from the cluster to Cloudflare. Failures here often
look like controller auth errors, tunnel registration failures, config push
errors, no healthy edge locations, or QUIC transport instability.

The origin layer is Kubernetes and the app. It includes the Ingress object, the
Ingress class, the Service backend, EndpointSlices, pod readiness, container
ports, app listeners, app base URL settings, and app-level auth. Most 502s land
here. The public edge can reach the tunnel, but the tunnel cannot get a usable
response from the origin.

Layered debugging keeps changes small. If the origin is broken, fix the app
stack. If the tunnel cannot register, inspect this stack and Cloudflare
credentials. If DNS is wrong, correct the Cloudflare-side hostname or policy
state. Do not blur those together.

## Routing Model

The route should live with the app that owns the backend. That keeps the public
contract close to the Service name, port, path, and auth model it depends on.

For a new public route, the app stack should answer these questions before it
adds `ingressClassName: cloudflare-tunnel`:

```text
Who is expected to call this URL?
Is the caller a person, browser, webhook sender, OAuth provider, or API client?
Can the caller use Tailscale instead?
What authentication protects the app after Cloudflare forwards the request?
Should Cloudflare Access, WAF rules, or other edge policy apply?
Which Service and port are the true backend?
Does the backend already have ready endpoints?
Is the hostname an external API contract that clients may store?
Who owns changes to the hostname?
What metrics or logs will show failure or abuse?
```

Good route changes are boring. They preserve stable hostnames where clients
depend on them, update one owner stack, preview the changed stack, and verify
the backend before testing the public URL. Risky route changes mix hostname
renames, Service renames, port changes, controller upgrades, and Cloudflare
policy edits in one pass. Split those unless the migration requires them to
move together.

Use this search when you need to find route intent in the repo:

```bash
rg -n 'cloudflare-tunnel|className.*cloudflare|ingressClassName.*cloudflare' pulumi docs
```

Use these live checks when you need to compare repo intent with cluster state:

```bash
kubectl get ingress -A | rg -i 'cloudflare|<hostname>|<service>'
kubectl describe ingress -n <app-namespace> <ingress-name>
kubectl get svc,endpoints,endpointslice -n <app-namespace> <service-name>
```

Treat a route as working only after the full path works from the expected kind
of caller. A pod being Ready is useful evidence, but it is not an end-to-end
public route test.

## Public Exposure Discipline

Cloudflare Tunnel makes public routes easy to create. That convenience is why
the review bar should be higher than "the URL loads."

Use Cloudflare Tunnel for:

- public websites or public product endpoints
- webhooks from providers outside the tailnet
- OAuth redirect or callback URLs from external services
- third-party integrations that cannot join the tailnet
- routes that intentionally depend on Cloudflare Access, WAF, DNS, edge TLS, or
  edge logs

Use Tailscale instead for:

- admin surfaces
- personal tools
- dashboards
- database UIs
- notebook servers
- workflow UIs
- internal APIs
- raw TCP systems used only from trusted devices

A public route needs an access decision. It should be clear whether Cloudflare
policy protects it, whether the app itself requires auth, whether anonymous
traffic is acceptable, and whether the backend can tolerate internet-shaped
traffic. If the only reason for public exposure is convenience from a laptop,
the private path is usually the better fit.

## 502 Debugging

A 502 means a proxy could not get a usable response from its upstream. With
Cloudflare Tunnel, the upstream might be the tunnel connector or the Kubernetes
origin behind it. The symptom is public, but the cause is often a normal
Kubernetes backend problem.

Start at the backend:

```bash
kubectl get svc,endpoints,endpointslice -n <app-namespace> <service-name>
kubectl describe svc -n <app-namespace> <service-name>
kubectl get pods -n <app-namespace> --show-labels
kubectl logs -n <app-namespace> -l <app-label-selector> --tail=200
```

If `endpoints` or `endpointslice` is empty, fix the origin before changing the
tunnel. Common causes are:

- the Service selector no longer matches pod labels
- pods exist but are not Ready
- the app listens on a different port than the Service `targetPort`
- the Service is in the wrong namespace
- an app stack renamed the Service but the Ingress still points at the old name
- an operator-owned Service was reconciled with labels that no longer match the
  intended backend

If endpoints exist, inspect the Ingress:

```bash
kubectl describe ingress -n <app-namespace> <ingress-name>
kubectl get ingress -n <app-namespace> <ingress-name> -o yaml
```

Check the class, host, path, backend service name, and backend service port.
The class should be `cloudflare-tunnel`. The host should match the public URL
the caller is using. The service name and port should match the Service that
has endpoints.

Then inspect the tunnel controller:

```bash
kubectl get deploy,pods,svc,servicemonitor -n cloudflare-tunnel
kubectl logs -n cloudflare-tunnel -l app.kubernetes.io/name=cloudflare-tunnel-ingress-controller --tail=200
```

Controller auth errors, tunnel registration failures, or config push failures
point toward this shared stack or Cloudflare-side configuration. Backend
connection refused, timeout, host mismatch, or protocol errors point back to
the origin route or app.

Use `curl` to classify the public response, but keep hostnames generic in docs:

```bash
curl -svI https://<public-hostname>
```

Useful questions while reading the response:

```text
Did public DNS reach Cloudflare?
Did Cloudflare return a policy/access response before the origin?
Is the status code from Cloudflare, the tunnel, or the app?
Does the app log show the request?
Does the response change when you hit a health path versus the main path?
```

Avoid covering up a 502 by creating a second route. That may make a new URL
work while leaving the original external contract broken. The durable fix is
usually smaller: correct the Service selector, restore the expected port, make
the app listen on the Service target, update the Ingress backend, or preserve
the hostname clients already use.

## DNS And Edge Checks

DNS issues are separate from Kubernetes issues. If the backend and controller
look healthy but the public name still fails, check the public edge layer.

Safe checks:

```bash
dig +short <public-hostname>
curl -svI https://<public-hostname>
```

When using Cloudflare UI or API tooling, verify shape rather than sharing
private details:

```text
the hostname belongs to the expected zone
the route points at the expected tunnel
the route policy matches the intended audience
TLS mode and app protocol expectations are compatible
no stale hostname points at an old route
edge logs show the request reaching the expected hostname
```

Do not paste Cloudflare account IDs, tunnel IDs, API tokens, full private
hostnames, or secret-bearing API output into documentation. Summaries like
"route points at the expected tunnel" or "Cloudflare policy is blocking before
origin" are enough for most repo docs.

## Dashboard Guide

This stack ships two dashboards. They are not decorative; they are the main
way to separate request failures from tunnel transport failures.

`Cloudflare Tunnel Overview` is for request and controller health. It includes:

```text
HA connections
request rate
error rate
responses by status code
request errors
origin connect latency p50 and p95
active edge locations
config push errors
tunnel register successes and failures
concurrent requests
TCP and UDP session counts
```

Use it when a URL is returning errors, when a route change was just made, or
when you need to know whether Cloudflare is reaching the connector. A rising
error rate with normal tunnel registration usually points toward a route or
origin problem. Config push errors or tunnel register failures point toward the
controller, credentials, or Cloudflare-side state. High origin connect latency
means the edge and connector are alive but the path to the backend is slow or
failing.

`Cloudflare Tunnel Transport` is for the connection between `cloudflared` and
Cloudflare. It includes:

```text
QUIC total connections
QUIC closed connections
MTU drops
average and latest RTT
receive and send throughput
TCP and UDP active sessions
congestion window
QUIC MTU
dropped and lost packets
buffered packets
received and sent frames
```

Use it when requests are intermittent, latency changes suddenly, or the
overview dashboard suggests the tunnel is alive but unstable. MTU drops, packet
loss, elevated RTT, or frequent connection churn point to transport behavior.
Those signals are different from an app returning 500s or a Service having no
endpoints.

If the dashboards are empty, debug metrics discovery before editing dashboard
JSON:

```bash
kubectl get servicemonitor -n cloudflare-tunnel cloudflare-tunnel -o yaml
kubectl get svc,pods -n cloudflare-tunnel --show-labels
kubectl get configmap -n cloudflare-tunnel -l grafana_dashboard=1
```

Check that the ServiceMonitor selector still matches chart labels, that the
metrics port is named `metrics`, that the path is `/metrics`, and that the
monitoring release label still matches the Prometheus stack. Chart upgrades can
break dashboards by changing labels even when traffic still works.

## Safe Route Changes

For a route change, start by naming the owner:

```text
controller behavior or dashboards -> pulumi/core/networking/cf-tunnel
public hostname, path, service, or port -> the app stack
Cloudflare policy, DNS, or zone behavior -> Cloudflare-side configuration
```

Then make one class of change at a time when possible. A safe app route change
usually looks like:

```text
1. Confirm the service really needs the public edge.
2. Identify the existing hostname, path, Service, and port.
3. Confirm the Service has ready endpoints before changing the route.
4. Edit the owning app stack.
5. Run the repo checks.
6. Preview the owning app stack.
7. After an intentional apply, test the real public URL and app logs.
```

Useful commands:

```bash
just sync pulumi/<area>/<app>
just check-python
just lint
git diff --check
just preview pulumi/<area>/<app> stack=mx
```

For a controller or dashboard change in this stack:

```bash
just sync pulumi/core/networking/cf-tunnel
just check-python
just lint
git diff --check
just preview pulumi/core/networking/cf-tunnel stack=mx
```

Do not treat a chart version bump as a simple dependency update. This chart is
part of the public ingress path. Before upgrading, check for changed chart
values, changed Kubernetes labels, changed metrics names, changed controller
behavior, and any migration notes. After an intentional apply, verify at least
one existing Cloudflare-routed service end to end, not just the controller pod.

Safe route changes preserve contracts. If a public hostname is already used by
an external provider, script, browser bookmark, or OAuth configuration, treat
it like API. Rename only with a migration plan.

## Common Failure Patterns

Public URL returns 502 and the Service has no endpoints. The tunnel is probably
not the first fix. Repair selector, readiness, namespace, or port drift in the
app stack.

Public URL returns 502 and endpoints exist. Check the Ingress host, path,
backend service port, target protocol, app listener, and app logs. The route
may be reaching the wrong port or speaking HTTP to a backend that expects
something else.

Controller logs show auth or registration errors. Check Pulumi config presence,
token validity, account association, and tunnel name locally. Do not print
secret values.

Route was edited but public behavior did not change. Check that the Ingress
class is `cloudflare-tunnel`, that the controller saw the Ingress, and that
Cloudflare-side route state no longer points at stale config.

Dashboards are empty after traffic succeeds. Check ServiceMonitor labels,
metrics service labels, metrics port naming, and Grafana dashboard ConfigMap
labels. This can happen after chart label changes.

Overview dashboard shows config push errors. Inspect controller logs and recent
Ingress changes. A malformed route, invalid host, credential issue, or
Cloudflare-side rejection can prevent the controller from pushing the intended
config.

Transport dashboard shows packet loss, MTU drops, or frequent closed
connections. Separate this from app health. The origin might be fine while the
tunnel transport is unstable.

Requests reach the app but are rejected. The tunnel did its job. Debug app
auth, webhook signatures, allowed hosts, CSRF settings, base URL settings, or
provider configuration.

Requests never reach the app. Work outward from Service endpoints to Ingress,
controller logs, dashboard signals, Cloudflare route state, and DNS.

## Working Notes

Keep public-edge documentation practical and non-secret. It is useful to name
resource kinds, namespaces, labels, config keys, dashboard names, and generic
commands. It is not useful to copy real account values, tokens, private
hostnames, kubeconfig content, full Pulumi outputs, or secret-bearing logs.

When reporting an incident or route change, include the layer that failed:

```text
DNS or Cloudflare edge
tunnel controller or connector
Kubernetes Ingress
Kubernetes Service or EndpointSlice
pod readiness or app listener
app authentication or external provider config
```

That wording keeps follow-up action obvious. "Cloudflare is broken" is too
broad to act on. "The Ingress points at a Service with no ready endpoints" or
"the controller is reporting config push errors after the route change" gives
the next person a real handle.
