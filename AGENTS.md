# Repository Guidelines

This repository manages personal infrastructure-as-code with Pulumi (Python, uv toolchain) and Modal. Pulumi projects define Kubernetes resources and Helm releases; Modal projects run small managed services.

## Project Structure
- `pulumi/` — Python Pulumi projects by domain:
  - `apps/` (end‑user apps) — e.g., `web/glance`, `media/immich`, `dev/stitch`.
  - `core/` (cluster primitives) — `networking/{tailscale,cf-tunnel}`, `security/{cert-manager,vault}`, `operators/cnpg`.
  - `data/` (databases/analytics/ML/streaming) — `databases/{postgres,cockroach}`, `analytics/{clickhouse,superset,jupyterhub}`, `ml/chroma`, `workflow/{n8n,temporal}`.
  - `ops/` — monitoring stack.
- `modal/` — Modal apps (e.g., `golink/`).
- Root: `.editorconfig`, `.gitignore`, `README.md`.

## Prerequisites
- CLI: `pulumi`, `kubectl`, `helm`, `uv` (Python package manager), and optionally `modal`.
- Python: 3.12 for Pulumi projects, 3.10+ for Modal (`modal/golink`).
- Access: Logged into your Pulumi backend (`pulumi login`) and Kubernetes context selected (`kubectl config current-context`).

## Environments & Stacks
- Use Pulumi stacks per environment (e.g., `dev`, `prod`).
- Select or create: `pulumi stack select <stack>` or `pulumi stack init <stack>` inside each project.
- Secrets live in stack config; never hard‑code credentials in code.

## Build, Run, Deploy
- Pulumi workflow (example: Glance):
  - `cd pulumi/apps/web/glance`
  - `uv sync` — install project deps from `pyproject.toml`/`uv.lock`.
  - `pulumi config set namespace glance` — required in most app/data projects.
  - `pulumi preview` — review changes; expect focused diffs.
  - `pulumi up` — apply.
- Modal workflow (example: golink):
  - `cd modal/golink`
  - Ensure a Modal Secret named `golinks` and a Volume `golinks-data` exist.
  - `modal deploy modal/golink/golink.py` — deploy the app.

## Configuration & Secrets (common keys)
- Namespaces: many projects require `namespace` (e.g., `immich`, `glance`, `spark`, `n8n`, `superset`, `jupyterhub`). Example: `pulumi config set namespace myns`.
- Networking:
  - Tailscale operator: `TS_CLIENT_ID`, `TS_CLIENT_SECRET` (secret).
  - Cloudflare tunnel: `cloudflareAccountId`, `cloudflareTunnelApiToken` (secret), optional `tunnelName`.
- Databases:
  - Postgres cluster: optional `namespace` (default `postgresql`), optional `ts_hostname`.
  - Stitch app: `POSTGRES_HOST`, `k8s_namespace`, `postgresql:port` (scoped key), plus app secrets `TWITCH_CLIENT_SECRET`, `WEBHOOK_SECRET`, `DISCORD_TOKEN`. Non‑secret keys include `PORT`, `TWITCH_CLIENT_ID`, `WEBHOOK_URL`, `WEBHOOK_PORT`, `DISCORD_CHANNEL`.
- Apps:
  - Plausible: `databaseUrl` (secret), `clickhouseUrl` (secret), optional `baseUrl`.
  - n8n: `namespace`, optional `postgresPassword`.
  - Chroma: optional tuning keys `image_version`, `storage_size`, `storage_class`, `cpu_*`, `memory_*`.
- Commands:
  - Plain: `pulumi config set <key> <value>`; Secret: `pulumi config set --secret <key> <value>`; Scoped: `pulumi config set <scope:key> <value>`.

## Coding Style & Conventions
- Follow `.editorconfig`: Python 4‑space indent; YAML 2‑space; LF endings; final newline. Markdown trailing whitespace allowed.
- Python:
  - Prefer small, explicit `__main__.py` modules; keep resource graphs readable.
  - Use `labels = {"app": <name>}` consistently; prefer hyphenated resource names (`cf-tunnel`, `cert-manager`).
  - Encode readiness via Pulumi options: use `depends_on` and annotations like `pulumi.com/waitFor` (see Postgres and monitoring projects).
- Project layout: `pulumi/<category>/<service>` and `modal/<service>`.

## Testing & Verification
- Dry‑runs are tests: always run `pulumi preview` before `pulumi up`.
- Post‑apply checks:
  - `kubectl get ns <ns>`; `kubectl get all -n <ns>`.
  - Verify ingress classes: `tailscale` or `cloudflare-tunnel` present and routing.
  - For DB stacks, confirm exported outputs: run `pulumi stack output` (e.g., Postgres exports `host`, `port`, `username`, `password`, `uri`).
- Optional Python tests: place under `tests/` as `test_*.py`; run with `uv run pytest` if added.

## Commit & Pull Request Guidelines
- Commits: Use Conventional Commits. Examples:
  - `feat: add n8n deployment`
  - `refactor: consolidate pulumi projects into category structure`
  - Scoped prefixes are fine: `pulumi: deploy glance`, `modal: deploy golink`.
- PRs should include:
  - Summary, scope, and rationale; link issues if any.
  - Exact commands run and outcomes (paste `pulumi preview` summary; screenshots optional).
  - Config/secrets touched (`pulumi config set …`, new Modal secrets) and rollback notes.

## Architecture Notes
- Ingress: Tailscale operator exposes internal services (`ingressClassName: tailscale`). Cloudflare Tunnel provides public ingress for selected apps (`ingressClassName: cloudflare-tunnel`).
- Datastores: CloudNativePG and Bitnami charts back Postgres/ClickHouse; some apps consume secrets/outputs or `pulumi.Config()` values to connect.
- Monitoring: kube‑prometheus‑stack is deployed with CRDs handled separately to control lifecycle.

## Safety & Rollback
- Validate kube context before applying: `kubectl config current-context`.
- Cancel a stuck update: `pulumi cancel` (in another terminal).
- Roll back by re‑applying a known good commit + `pulumi up`; destroy with care via `pulumi destroy` (scoped to project+stack).
- Never commit secrets or stack files outside `.gitignore` patterns (`Pulumi.*.yaml/json` are ignored).

## Adding a New Pulumi Project (quick recipe)
1) Create folder `pulumi/<category>/<service>/` with `pyproject.toml`, `Pulumi.yaml` (runtime python, `toolchain: uv`), and `__main__.py`.
2) Pin deps (`pulumi`, `pulumi-kubernetes`, and any providers) and run `uv lock` / `uv sync`.
3) Model resources with explicit `labels`, ingress class, and `depends_on` where ordering matters.
4) Initialize stack, set required config (start with `namespace`), then `pulumi preview && pulumi up`.

## Modal‑Specific Tips
- Secrets: create a Secret named as expected by code (e.g., `golinks`) and reference it via `modal.Secret.from_name`.
- Volumes: ensure named volumes exist (`golinks-data`) to persist app state.
- Deploy: `modal deploy <path/to/app.py>`; scale/limits are encoded in decorators.
