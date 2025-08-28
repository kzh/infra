# Repository Guidelines

Agent‑focused operational handbook for this repo. Use it to navigate projects, run safe changes, and wire stacks via Pulumi and ESC without leaking secrets.

## Project Map
- `pulumi/` — Python Pulumi projects
  - `core/`: cluster primitives (e.g., `networking/{tailscale,cf-tunnel}`, `security/{cert-manager}`, `operators/cnpg`)
  - `data/`: databases/analytics/workflow (e.g., `databases/postgres`, `analytics/{clickhouse,superset,jupyterhub}`, `workflow/{n8n,temporal}`)
  - `apps/`: end‑user apps (e.g., `media/immich`, `web/glance`, `stitch`)
  - `ops/`: monitoring stack
- `modal/` — Modal apps (e.g., `golink/`)
- Root: `.editorconfig`, `.gitignore`, `README.md`

## Environment & Tools
- Python: 3.12 for Pulumi, 3.10+ for Modal. Install deps with `uv sync`.
- CLIs: `pulumi`, `esc`, `kubectl`, `helm`, `uv` (optional: `modal`).
- Access: `pulumi login` (backend), `kubectl config current-context` (cluster). For ESC, use the `esc` CLI.
- Style: follow `.editorconfig`. Keep Pulumi programs small; prefer clear labels and explicit `depends_on` where ordering matters.

## Agent Operating Rules
- Announce actions briefly and maintain a live plan (one `in_progress`).
- Prefer previews: `pulumi preview --diff` before any `pulumi up`.
- Never echo secrets; set with `pulumi config set --secret` or `esc env set --secret`.
- Scope changes: minimal diffs, no unrelated refactors, no `pulumi destroy` without explicit instruction.
- Verify kube context prior to apply; cancel stuck updates with `pulumi cancel`.

## Pulumi Fundamentals (quick)
- Model: Python declares desired state → engine diffs stack state → providers apply.
- Stacks & state: config lives in `Pulumi.<stack>.yaml`; ESC can be bound via `environment:`.
- Config & secrets: read with `pulumi.Config()`, prefer `require_secret`; Pulumi masks/encrypts secrets.
- Outputs & deps: pass `Output`s to wire dependencies; use `depends_on` for explicit ordering; combine with `Output.apply/all`.
- Kubernetes/Helm: use `pulumi_kubernetes.helm.v4.Chart`; manage CRDs deliberately; see `pulumi/lib/k8s.py:add_wait_annotation` for readiness.

## Cross‑Stack Wiring (Stacks, Outputs, ESC)
- Producer exports in Postgres stack (example):
  ```python
  pulumi.export("host", host)
  pulumi.export("port", 5432)
  pulumi.export("username", pulumi.Output.secret(user))
  pulumi.export("password", pulumi.Output.secret(pw))
  pulumi.export("ts_hostname", config.get("ts_hostname", "postgresql"))
  ```
- Consumer A — StackReference (Immich, required):
  ```python
  ref = pulumi.StackReference(cfg.require("postgres_stack"))
  host = pulumi.Output.all(ref.get_output("ts_hostname"), ref.get_output("host")).apply(lambda t: t[0] or t[1])
  port = ref.get_output("port")
  user = ref.get_output("username")
  password = ref.get_output("password")
  ```
- Consumer B — ESC (Stitch): map producer outputs → `pulumiConfig` via `fn::open::pulumi-stacks`, then bind the env in the stack file:
  ```yaml
  # ESC env
  values:
    stacks:
      fn::open::pulumi-stacks:
        stacks:
          postgresql:
            stack: postgresql/prod
    pulumiConfig:
      POSTGRES_HOST: ${stacks.postgresql.host}
      k8s_namespace: ${stacks.postgresql.k8s_namespace}
      postgresql:host: ${stacks.postgresql.ts_hostname}
      postgresql:port: ${stacks.postgresql.port}
      postgresql:username: ${stacks.postgresql.user}
      postgresql:password: ${stacks.postgresql.password}
      postgresql:sslmode: disable
  ---
  # Pulumi.<stack>.yaml
  environment:
    - stitch/prod
  ```
- When to choose: StackReference for programmatic consumers (Immich requires this); ESC for many consumers and zero‑code wiring.
- Validate: producer `pulumi stack output` → `esc open <env> --format detailed` → consumer `pulumi preview --diff`.

### Inside‑Cluster vs Local Pulumi — Postgres Host Selection
- Services running inside Kubernetes must use the in‑cluster Service DNS for Postgres (e.g., `postgresql-cluster-rw.<namespace>.svc.cluster.local`).
- Pulumi programs running on a developer machine (e.g., your Mac) should use the Tailscale hostname exported by the Postgres stack (default `ts_hostname: postgresql`).
- Rationale: pods do not resolve or route to tailnet hosts by default. Use Tailscale host only for local provider operations; use the cluster DNS inside pods.
- Example (Immich with StackReference): provider connects to `ts_hostname`, app env `DB_HOSTNAME` set to in‑cluster FQDN derived from the producer stack’s `k8s_namespace`.

## Tailscale Proxy (cluster access)
- Operator: `pulumi/core/networking/tailscale` deploys `tailscale-operator` (OAuth client/secret required via config).
- L4 Services: annotate Service with `tailscale.com/expose: "true"` and `tailscale.com/hostname: <name>` (Postgres uses an extra `*-rw-ext` Service).
- L7 HTTP: set `ingressClassName: tailscale` on Ingress (e.g., Immich, n8n).
- Flow: client@tailnet → MagicDNS host → proxy pod → Service/Ingress → pods.
- Checks: `kubectl -n tailscale get deploy,pods`; list exposed Services via jsonpath; `kubectl get ingress -A | rg tailscale`.

## Runbooks
- CNPG → Postgres → Apps
  - `cd pulumi/core/operators/cnpg && uv sync && pulumi stack select mx && pulumi preview`
  - `cd pulumi/data/databases/postgres && uv sync && pulumi stack select mx && pulumi preview`
  - Apps (e.g., Immich/Stitch): ensure namespace config; prefer StackReference/ESC; `pulumi preview`.
- Bind ESC to a stack
  - Edit `Pulumi.<stack>.yaml`:
    ```yaml
    environment:
      - <env-name>
    ```
  - Validate with `esc open <env> --format detailed` then `pulumi preview`.
- Modal deploy (example)
  - `cd modal/golink && modal deploy modal/golink/golink.py` (ensure Modal Secret/Volume exist).

## Verification & Safety
- Always: `pulumi preview --diff` before `pulumi up`.
- After apply: `kubectl get all -n <ns>`; confirm ingress classes; `pulumi stack output` (secrets remain redacted).
- Rollback: re‑apply a known good commit + `pulumi up`; cancel stuck updates with `pulumi cancel`.

## Commits & PRs
- Conventional Commits (e.g., `feat:`, `fix:`, `pulumi:`). Keep diffs focused.
- PRs: include summary, exact commands run (preview summary is enough), configs touched, and rollback notes.

---

## Agent Operating Guide (for LLM coding agents)

### Mission
Make surgical, reversible changes; announce actions; prefer previews over applies; never leak secrets.

### Golden Rules
- Announce actions: before running commands or patches, state intent in 1–2 sentences.
- Maintain a live plan: use the `update_plan` tool with one `in_progress` step.
- Search fast: prefer `rg` and read files in ≤250‑line chunks.
- Be idempotent: always run `uv sync` then `pulumi preview` before `pulumi up`.
- Guardrails: do not run `pulumi destroy`, `helm uninstall`, or delete files unless explicitly asked.
- Secrets: never print secret values. Use `pulumi config set --secret` or `esc env set --secret`. Redact outputs.
- Kube context: verify with `kubectl config current-context` before any apply.

### Tools You May Use
- Shell: `uv`, `pulumi`, `esc`, `kubectl`, `helm`, `rg`.
- Patching: `apply_patch` to add/edit files; keep changes minimal and aligned with existing style.
- Planning: `update_plan` to show progress; close plans when done.

### Playbooks
- Bind an ESC environment to a stack:
  1) Edit `Pulumi.<stack>.yaml` to include:
     ```yaml
     environment:
       - <env-name>
     ```
  2) Validate: `esc open <env> --format detailed` then `pulumi preview`.

- Deploy CNPG operator → Postgres cluster:
  1) `cd pulumi/core/operators/cnpg && uv sync && pulumi stack select mx && pulumi preview`
  2) `cd pulumi/data/databases/postgres && uv sync && pulumi stack select mx && pulumi preview`
  3) Post‑apply checks: `kubectl get all -n postgresql`; confirm Secret `postgresql-cluster-superuser` exists.

- Wire an app to Postgres (Stitch or Immich):
  - Stitch uses ESC `stitch/prod` to populate `pulumiConfig` (e.g., `POSTGRES_HOST`, `k8s_namespace`, `postgresql:port`). Bind the env in the stack file, then `pulumi preview`.
  - Immich: requires StackReference (`immich:postgres_stack`) and uses in‑cluster DB host for the pod and Tailscale host for local providers.

### Troubleshooting
- “file://~ does not support Pulumi ESC”: use `esc env ls|open` or switch Pulumi to Cloud (`pulumi login --cloud`).
- CRDs not found / timeouts: ensure CNPG operator applied; rerun preview with `--diff` to see pending CRDs.
- Postgres auth errors: verify `ts_hostname`, service endpoints, and that `pulumi_postgresql` provider connects to the right host/port.
- Ingress not reachable: check ingressClass (`tailscale` or `cloudflare-tunnel`) and controller health.

### Response & Diff Etiquette (in this CLI)
- Keep responses concise; use bullets and code blocks for commands/snippets.
- Echo only the essential command outputs; link to file paths instead of pasting large files.
- When editing, show intent and apply a focused patch; avoid unrelated refactors.

## Tailscale Proxy Guide

### What It Is
- Operator: runs in namespace `tailscale` (chart `tailscale-operator`, version set in `pulumi/core/networking/tailscale/__main__.py`).
- Auth: requires `TS_CLIENT_ID` and `TS_CLIENT_SECRET` in stack config.
- API server proxy: `apiServerProxyConfig.mode: "true"` exposes the Kubernetes API on the tailnet (guard with ACLs).

### Exposure Modes
- L4 Service (TCP/UDP): annotate a Service.
  - Annotations: `tailscale.com/expose: "true"`, `tailscale.com/hostname: <name>`.
  - In repo: Postgres extra Service `postgresql-cluster-rw-ext` gets these annotations; `ts_hostname` defaults to `postgresql`.
- L7 HTTP Ingress: set `ingressClassName: tailscale` on a normal Ingress.
  - In repo: Immich, n8n, Temporal web use this pattern.

### Traffic Flow (ASCII)
Service (L4)
  client@tailnet → MagicDNS `<hostname>` → Tailscale proxy pod → ClusterIP Service → app pods

Ingress (L7)
  client@tailnet → MagicDNS `<host>` → Tailscale ingress proxy → backend Service → app pods

### Snippets
- Service exposure (L4):
```yaml
metadata:
  annotations:
    tailscale.com/expose: "true"
    tailscale.com/hostname: my-svc
```

- Ingress exposure (L7):
```yaml
spec:
  ingressClassName: tailscale
  rules:
    - host: my-app
```

### Operational Checks
- Operator health: `kubectl -n tailscale get deploy,pods`
- Exposed services: `kubectl get svc -A -o jsonpath='{range .items[?(@.metadata.annotations.tailscale\.com/expose=="true")]}{.metadata.namespace}/{.metadata.name}{"\n"}{end}'`
- Tailscale ingresses: `kubectl get ingress -A | rg tailscale`
- Name resolution (on a tailnet client): `dig immich`, `dig postgresql`; then connect `psql -h postgresql -p 5432 ...`

### Troubleshooting
- No proxy pod created: ensure annotations present, operator healthy, and Service type is routable (ClusterIP/LoadBalancer).
- Can’t resolve name: verify Tailnet MagicDNS and that `tailscale.com/hostname` matches what you’re querying.
- HTTP 404 via tailscale ingress: check Ingress `rules.host` and backend Service name/port; ensure app is listening.
- API server over tailnet disabled/unwanted: set `apiServerProxyConfig.mode` to `"false"` in the operator values and redeploy.

## Pulumi Fundamentals for Agents

### Core Model
- Programs declare desired state in Python; the Pulumi engine diffs against the stack’s saved state and uses providers (e.g., Kubernetes, Helm) to create/update/delete resources. Always run `pulumi preview` before `pulumi up`.

### State & Stacks
- State backend: Pulumi Cloud or DIY (`file://`, `s3://`, etc.). Stack settings live in `Pulumi.<stack>.yaml` and may include `config`, `secrets` metadata, and `environment` (ESC binding).
- Safe ops: prefer `pulumi preview --diff`; avoid `pulumi destroy` unless explicitly approved.

### Configuration & Secrets
- Read config via `pulumi.Config()`; use `require_secret` for sensitive keys. Set with `pulumi config set --secret <key> <value>`; Pulumi masks secrets in outputs and encrypts them in state.

### Dependencies & Outputs
- Passing one resource’s `Output` to another captures a dependency automatically. Use `depends_on` for explicit ordering. Use `Output.apply`/`Output.all` to combine values without unwrapping.

### Cross‑Stack Wiring
- Use `pulumi.StackReference("<org>/<project>/<stack>")` to consume another stack’s exported outputs. In this repo, Immich can read Postgres exports via `immich:postgres_stack`.

### Kubernetes & Helm (v4)
- Prefer `pulumi_kubernetes.helm.v4.Chart` for Helm installs; supports OCI charts, better diffs, dependency ordering, and post‑render. Use `skip_crds=True` if CRDs are managed elsewhere. For readiness, use annotations or resource transforms (see repo’s `add_wait_annotation`).

### ESC Integration
- Bind ESC by adding `environment:` to `Pulumi.<stack>.yaml`. Keys under `pulumiConfig` flow into `pulumi.Config()`; `environmentVariables` are exported to the process. For ad‑hoc runs: `esc run <env> -- pulumi preview`.

### Minimal Playbook
1) `uv sync`
2) `pulumi stack select <stack>` (ensure ESC `environment:` set if needed)
3) `pulumi preview --diff`
4) If expected, `pulumi up`
5) Verify: `kubectl get all -n <ns>`; `pulumi stack output`

## Cross‑Stack Wiring (Stacks, Outputs, and ESC)

### Producer: Export Stable Outputs
- In the producer stack (e.g., `pulumi/data/databases/postgres`), export values you want other stacks to consume. Keep names stable and lowercase.
```python
# __main__.py (producer)
pulumi.export("host", host_output)
pulumi.export("port", 5432)
pulumi.export("username", pulumi.Output.secret("postgres"))
pulumi.export("password", pulumi.Output.secret(superuser_pw))
pulumi.export("ts_hostname", config.get("ts_hostname", "postgresql"))
```

### Consumer Pattern A: StackReference (direct)
- Good for programmatic wiring inside another Pulumi program (e.g., Immich).
```python
cfg = pulumi.Config()
pg_stack = cfg.require("postgres_stack")  # e.g., organization/postgresql/dev
pg = pulumi.StackReference(pg_stack)
host = pulumi.Output.all(pg.get_output("ts_hostname"), pg.get_output("host")) \
    .apply(lambda t: t[0] or t[1])
port = pg.get_output("port")
user = pg.get_output("username")
password = pg.get_output("password")  # remains secret
```
- Tip: Avoid cycles (A references B while B references A). Split shared infra into a base stack.

### Consumer Pattern B: ESC Environment (pulumi‑stacks provider)
- Centralize wiring for multiple stacks via ESC. Use `fn::open::pulumi-stacks` to read outputs and map them into `pulumiConfig` consumed by projects like Stitch.
```yaml
# esc env: stitch/prod
values:
  stacks:
    fn::open::pulumi-stacks:
      stacks:
        postgresql:
          stack: postgresql/prod
  pulumiConfig:
    POSTGRES_HOST: ${stacks.postgresql.host}
    k8s_namespace: ${stacks.postgresql.k8s_namespace}
    postgresql:host: ${stacks.postgresql.ts_hostname}
    postgresql:port: ${stacks.postgresql.port}
    postgresql:username: ${stacks.postgresql.user}
    postgresql:password: ${stacks.postgresql.password}
    postgresql:sslmode: disable
```
- Bind the ESC env in the consumer stack file:
```yaml
# Pulumi.mx.yaml
environment:
  - stitch/prod
```

### When to Use Which
- Use StackReference when a single consumer needs programmatic access (Immich).
- Use ESC when many stacks/apps need the same wiring or when you want zero code changes—just bind the environment.

### Validation Flow
1) Producer: `pulumi stack output` (confirm keys/values exist; secrets show as `[secret]`).
2) ESC: `esc open <env> --format detailed` (ensure `pulumiConfig` resolves and redacts secrets).
3) Consumer: `pulumi preview --diff` (verify config is present; check provider connects where applicable).

### Repo‑Specific Notes
- Postgres exports include `ts_hostname` enabling Tailscale access; consumers prefer it over `host` when present.
- Stitch expects scoped keys like `postgresql:port` and plain keys like `POSTGRES_HOST` and `k8s_namespace`—provided by ESC in `stitch/prod`.
- Immich requires StackReference (`immich:postgres_stack`). Direct admin and CNPG Secret fallbacks are removed.
