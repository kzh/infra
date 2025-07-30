# Infrastructure

Personal infrastructure-as-code repository containing deployments for various services and platforms.

## Structure

- **`modal/`** - Modal platform deployments
  - `golink/` - URL shortening service

- **`pulumi/`** - Kubernetes infrastructure deployments
  - `apps/` - End-user applications
    - `dev/` - Development tools (Stitch)
    - `media/` - Media applications (Immich)
    - `web/` - Web applications (Glance, Plausible)
  - `core/` - Core infrastructure components
    - `networking/` - Network infrastructure (Cloudflare Tunnel, Tailscale)
    - `operators/` - Kubernetes operators (CloudNativePG)
    - `security/` - Security infrastructure (cert-manager, Vault)
  - `data/` - All data infrastructure
    - `analytics/` - Analytics stack (Airbyte, ClickHouse, JupyterHub, Spark, Superset)
    - `databases/` - Databases (CockroachDB, PostgreSQL)
    - `ml/` - Machine learning (Chroma)
    - `streaming/` - Data streaming (Redpanda)
    - `workflow/` - Workflow orchestration (n8n, Temporal)
  - `ops/` - Operations and monitoring
    - `monitoring/` - Monitoring stack

## Usage

Each Pulumi project can be deployed independently:

```bash
cd pulumi/<category>/<service>
pulumi up
```

Modal deployments:

```bash
cd modal/<service>
modal deploy
```