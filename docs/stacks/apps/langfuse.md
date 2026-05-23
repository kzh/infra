# Langfuse

Source: `pulumi/apps/langfuse`

Langfuse is the LLM observability surface for this cluster. Use it when you
want traces, prompts, generations, scores, and evaluations in a tool built for
LLM workflows instead of trying to read raw proxy logs or database rows.

## What Pulumi Builds

The stack installs the official Langfuse Helm chart from:

```text
https://langfuse.github.io/langfuse-k8s
```

The deployment is intentionally shaped for a private, single-user cluster:

```text
Namespace:          langfuse
Helm chart:         langfuse
Helm chart version: 1.5.31
Service:            langfuse-web, ClusterIP, port 3000
Private URL:        pulumi stack output url
Ingress class:      tailscale
Web replicas:       1
Worker replicas:    1
Telemetry:          disabled by default
Signup:             enabled by default for first-account setup
```

Pulumi creates generated Kubernetes Secrets for Langfuse application keys and
the Langfuse-owned Valkey password. It also creates a Langfuse PostgreSQL
database and role in the shared PostgreSQL stack, points Langfuse at the shared
ClickHouse service, and points object storage at RustFS. The Helm chart reads
credentials by `secretKeyRef`; secret values are not embedded directly in the
chart values.

## State

Langfuse is more than a web pod. Its useful state lives across shared platform
services plus one Langfuse-owned cache/queue service:

```text
PostgreSQL:  application metadata in the shared PostgreSQL stack
ClickHouse:  event and trace analytics in the shared ClickHouse stack
RustFS:      uploaded objects and exports in the shared RustFS stack
Valkey:      Langfuse-owned queue/cache state in this namespace
```

Pulumi also runs bootstrap Jobs that create the `langfuse` ClickHouse database
and the `langfuse` RustFS bucket if they do not already exist. The Valkey PVC is
the only dependency PVC this stack should create directly.

## Access

Read the exported URL:

```bash
cd pulumi/apps/langfuse
pulumi stack output --stack mx url
```

Open the private Tailscale URL from a device on the tailnet and create the first
account. Signup stays enabled by default so the initial account can be created
without a separate bootstrap job. After that, set `langfuse:sign_up_disabled`
to `true` and preview/apply the stack if you want to close public signup on the
private route.

If the Tailscale ingress is reached through a fully qualified tailnet hostname,
set `langfuse:publicUrl` to that exact browser URL. Langfuse uses it for auth
callbacks, while the ingress hostname can stay as the short service name.

## Checks

Preview and apply through the repo wrapper:

```bash
just sync pulumi/apps/langfuse
just preview pulumi/apps/langfuse stack=mx
just up pulumi/apps/langfuse stack=mx
```

Inspect the deployment:

```bash
kubectl get pods,svc,endpoints,ingress,pvc -n langfuse
kubectl logs -n langfuse deploy/langfuse-web --tail=100
kubectl logs -n langfuse deploy/langfuse-worker --tail=100
```

The web readiness path is `/api/public/ready`; the liveness path is
`/api/public/health`.
