# Pulumi Projects

This repository is a collection of independent Pulumi Python projects. Each project can be synced, previewed, applied, or destroyed on its own.

## Layout

```text
pulumi/
  core/        Cluster primitives: networking, operators, security
  data/        Databases, storage, streaming, analytics, workflow systems
  apps/        End-user and internal applications
  ops/         Operational infrastructure such as monitoring
```

Project-local support assets should live beside the project that consumes them:

- `dashboards/` for Grafana dashboards loaded into ConfigMaps.
- `images/` for Dockerfiles and runtime image assets built by local recipes.

## Inventory

| Project | Purpose |
| --- | --- |
| `pulumi/core/networking/cf-tunnel` | Cloudflare Tunnel ingress controller, ServiceMonitor, and Grafana dashboards |
| `pulumi/core/networking/tailscale` | Tailscale operator, metrics, ProxyClass, and Grafana dashboards |
| `pulumi/core/operators/cnpg` | CloudNativePG operator |
| `pulumi/core/operators/kuberay` | KubeRay operator, development RayCluster, ingress, and Grafana dashboards |
| `pulumi/core/operators/mysql` | MySQL operator |
| `pulumi/core/security/cert-manager` | cert-manager |
| `pulumi/core/security/vault` | Vault |
| `pulumi/data/workflow/airflow` | Apache Airflow workflow orchestration with Tailscale ingress |
| `pulumi/data/analytics/clickhouse` | ClickHouse operator, ClickHouseInstallation, Tailscale access, and Grafana dashboards |
| `pulumi/data/analytics/jupyterhub` | JupyterHub and its singleuser image build |
| `pulumi/data/analytics/mlflow` | MLflow with external PostgreSQL integration |
| `pulumi/data/analytics/slurm` | SchedMD Slinky Slurm operator and minimal Slurm cluster |
| `pulumi/data/analytics/spark` | Spark operator and Spark UI ingress |
| `pulumi/data/analytics/superset` | Superset |
| `pulumi/data/databases/cockroach` | CockroachDB |
| `pulumi/data/databases/convexdb` | Self-hosted Convex backend and dashboard |
| `pulumi/data/databases/postgres` | PostgreSQL via CloudNativePG |
| `pulumi/data/storage/rustfs` | RustFS object storage |
| `pulumi/data/streaming/redpanda` | Redpanda |
| `pulumi/data/workflow/n8n` | n8n |
| `pulumi/data/workflow/temporal` | Temporal |
| `pulumi/ops/monitoring` | Prometheus Operator CRDs and kube-prometheus-stack |
| `pulumi/apps/coder` | Coder |
| `pulumi/apps/golink` | Tailscale golink |
| `pulumi/apps/hermes` | Hermes Agent with persistent local state and localhost-only dashboard |
| `pulumi/apps/immich` | Immich |
| `pulumi/apps/litellm` | LiteLLM Proxy with ChatGPT subscription models, persistent token storage, and Tailscale ingress |
| `pulumi/apps/mediawiki` | MediaWiki with MySQL Operator storage and Tailscale ingress |
| `pulumi/apps/stitch` | Stitch |
| `pulumi/apps/wordpress` | WordPress |

## Root Commands

The root `Justfile` is intentionally thin; it wraps project-local commands without changing how Pulumi projects are executed.

```bash
just projects
just sync pulumi/apps/hermes
just preview pulumi/apps/hermes stack=mx
just up pulumi/apps/hermes stack=mx
just preview-all
just check-python
just lint
just format
```
