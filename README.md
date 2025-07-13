# Infrastructure

Personal infrastructure-as-code repository containing deployments for various services and platforms.

## Structure

- **`modal/`** - Modal platform deployments
  - `golink/` - URL shortening service

- **`pulumi/`** - Kubernetes infrastructure deployments
  - `apps/` - Applications organized by category
    - `ai/` - AI/ML applications (OpenWebUI)
    - `data/` - Data applications (JupyterHub, n8n, NocoDB, Plausible)
    - `media/` - Media applications (Immich, Penpot)
    - `tools/` - Utility applications (Glance, Stitch)
  - `data/` - Data infrastructure
    - `analytics/` - Analytics stack (ClickHouse, Spark, Superset)
    - `db/` - Databases (CockroachDB, Neo4j, PostgreSQL)
    - `ml/` - Machine learning (Chroma)
    - `streaming/` - Data streaming (Airbyte, Scylla)
    - `workflow/` - Workflow orchestration (Airflow)
  - `operators/` - Kubernetes operators
    - `cf-tunnel/` - Cloudflare Tunnel operator
    - `cnpg/` - CloudNativePG operator
    - `tailscale/` - Tailscale operator
  - `ops/` - Operations and monitoring
    - `monitoring/` - Monitoring stack
  - `platform/` - Platform services
    - `redpanda/` - Redpanda streaming platform
    - `temporal/` - Temporal workflow engine
  - `security/` - Security services
    - `certs/` - Certificate management
    - `connect/` - Tailscale Connect
    - `vault/` - HashiCorp Vault

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