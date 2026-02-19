# Infrastructure

Personal infrastructure-as-code for a home/edge Kubernetes cluster, written in Python with Pulumi and managed per-project with the `uv` toolchain. Workloads are deployed as independent Pulumi projects; some lightweight services are deployed to Modal.

This README is a practical, end‑to‑end guide for getting from zero to a working stack.

## Prerequisites

- CLI: `pulumi`, `kubectl`, `helm`, `uv` (and optionally `modal`)
- Python: 3.12 for Pulumi projects; 3.10+ for Modal apps
- Access: `pulumi login` to your backend; correct kube context set (`kubectl config current-context`)

## Quickstart

Deploy any Pulumi project independently:

```bash
cd pulumi/<category>/<service>
uv sync
pulumi stack select <stack>  # or: pulumi stack init <stack>
# set required config for the project
pulumi preview && pulumi up
```

Modal app deploy (optional):

```bash
cd modal/<service>
modal deploy
```

## Repository Structure

- `pulumi/` — Pulumi projects by domain
  - `core/` — cluster/platform primitives
    - `networking/` — ingress + edge (Cloudflare Tunnel, Tailscale)
    - `operators/` — operators (CloudNativePG)
    - `security/` — security primitives (cert-manager, Vault)
  - `ops/` — monitoring/observability (kube‑prometheus‑stack)
  - `data/` — data plane and services
    - `databases/` — Postgres, CockroachDB
    - `analytics/` — ClickHouse, JupyterHub, Spark, Superset
    - `streaming/` — Redpanda
    - `ml/` — Chroma
    - `workflow/` — n8n, Temporal
  - `apps/` — end‑user applications
    - `media/` — Immich
    - `stitch/` — Stitch app
- `modal/` — Modal apps (e.g., `golink/`)
- `pulumi/lib/` — small helper library used by projects
- See `AGENTS.md` for stack wiring patterns and runbooks

## Stacks (Per‑Environment)

Use Pulumi stacks per environment (e.g., `dev`, `prod`). Each project has its own stack namespace and config. For cross‑stack wiring patterns and runbooks (CNPG → Postgres → Apps; ESC environment binding; Tailscale exposure), see `AGENTS.md`.

## Typical Workflows

### 1) Bring up platform (once per cluster)

```bash
# Tailscale operator
cd pulumi/core/networking/tailscale
uv sync && pulumi stack select <stack>
pulumi config set tailscale:TS_CLIENT_ID <id>
pulumi config set --secret tailscale:TS_CLIENT_SECRET <secret>
pulumi up

# Cloudflare Tunnel ingress controller
cd pulumi/core/networking/cf-tunnel
uv sync && pulumi stack select <stack>
pulumi config set cloudflare-tunnel:cloudflareAccountId <id>
pulumi config set --secret cloudflare-tunnel:cloudflareTunnelApiToken <token>
pulumi up

# cert-manager
cd pulumi/core/security/cert-manager
uv sync && pulumi stack select <stack>
pulumi config set namespace cert-manager
pulumi up

# CloudNativePG operator
cd pulumi/core/operators/cnpg
uv sync && pulumi stack select <stack>
pulumi up

# Monitoring stack
cd pulumi/ops/monitoring
uv sync && pulumi stack select <stack>
pulumi up
```

### 2) Deploy a data service (examples)

```bash
# PostgreSQL (CloudNativePG cluster)
cd pulumi/data/databases/postgres
uv sync && pulumi stack select <stack>
pulumi config set namespace postgresql
pulumi preview && pulumi up

# ClickHouse
cd pulumi/data/analytics/clickhouse
uv sync && pulumi stack select <stack>
pulumi preview && pulumi up
```

### 3) Deploy an app (examples)

```bash
# Immich (media)
cd pulumi/apps/media/immich
uv sync && pulumi stack select <stack>
pulumi config set namespace immich
# wire to Postgres via StackReference (required)
pulumi config set immich:postgres_stack <org>/postgresql/<stack>
# optional: pulumi config set immich:library_storage_size 500Gi
# optional: explicit Redis image pinning (recommended for long-lived clusters)
pulumi config set immich:redis_image_registry docker.io
pulumi config set immich:redis_image_repository bitnami/redis
pulumi config set immich:redis_image_tag latest
pulumi config set immich:redis_image_digest sha256:1c41e7028ac48d7a9d79d855a432eef368aa440f37c8073ae1651879b02c72f4
pulumi preview && pulumi up

# How Immich connects to Postgres

In‑cluster pods use the Kubernetes Service DNS. Local Pulumi providers use the
Tailscale hostname exported by the Postgres stack.

In‑cluster path (pod → service → CNPG):

  [immich pod]
      └── DB_HOSTNAME=postgresql-cluster-rw.<ns>.svc.cluster.local:5432
            └── [CNPG cluster]

Local Pulumi path (dev machine → tailnet → CNPG):

  [pulumi@mac]
      └── host=ts_hostname (e.g., "postgresql")
            └── [tailscale proxy pod]
                  └── [ClusterIP: postgresql-cluster-rw-ext]
                        └── [CNPG cluster]

Postgres extensions are managed in the Postgres stack. Example (mx):

  cd pulumi/data/databases/postgres
  uv sync && pulumi stack select mx
  pulumi config set postgresql:app_databases '["immich"]'
  pulumi config set postgresql:extensions '["vector","cube","earthdistance"]'
  pulumi preview --diff && pulumi up

# n8n (workflow)
cd pulumi/data/workflow/n8n
uv sync && pulumi stack select <stack>
pulumi config set n8n:namespace n8n
pulumi config set --secret n8n:postgresPassword <password>
# optional: pulumi config set n8n:image n8nio/n8n:<tag>
pulumi preview && pulumi up

# Stitch (app)
cd pulumi/apps/stitch
uv sync && pulumi stack select <stack>
# set PORT, POSTGRES_HOST, k8s_namespace, TWITCH_CLIENT_ID, WEBHOOK_URL, etc.
pulumi preview && pulumi up
```


## Conventions

- Python formatting and files: `.editorconfig` (4‑space Python; 2‑space YAML)
- Resource naming: hyphenated names; labels like `{"app": <name>}`
- Ingress classes: Tailscale (`tailscale`) for internal, Cloudflare Tunnel (`cloudflare-tunnel`) for public
- Readiness: prefer `depends_on` and `pulumi.com/waitFor` annotations for CRDs and stateful services
- Secrets: always set via `pulumi config set --secret` and consume from config or K8s Secret; never hard‑code

## Safety & Rollback

- Confirm kube context before applying: `kubectl config current-context`
- Always dry‑run: `pulumi preview`
- Stuck updates: `pulumi cancel` (in another terminal)
- Rollback: checkout a known‑good commit and `pulumi up`
- Scoped destroys: use `pulumi destroy` on the specific project+stack only

## Troubleshooting

- Namespaces missing: many projects create their own `Namespace`; if a Helm chart installs into a new namespace, ensure `depends_on` is set (this repo does so for critical stacks)
- Ingress routing: verify ingress classes exist and are reconciling (`kubectl get ingressclass`), check `tailscale.com/*` annotations when exposing services
- Postgres consumers: prefer consuming outputs from the Postgres stack rather than re‑deriving values; update stacks if needed
- Immich Redis pull failures: if chart defaults reference a removed upstream tag, set explicit `immich:redis_image_*` config values (including digest) and run `pulumi up` to roll the StatefulSet

## Commit & PR Guidelines

- Conventional Commits (examples):
  - `feat: add n8n deployment`
  - `refactor: consolidate pulumi projects into category structure`
  - Scoped prefixes (ok): `pulumi: deploy glance`, `modal: deploy golink`
- PRs should capture:
  - Summary and rationale
  - Commands run and outcomes (paste `pulumi preview` summary)
  - Config/secrets touched and rollback notes
