# Infrastructure

Pulumi + Python infrastructure monorepo for a Kubernetes cluster.

## Included Services

| Area | Services |
| --- | --- |
| Core | Cloudflare Tunnel, Tailscale, CloudNativePG, KubeRay, MySQL Operator, cert-manager, Vault |
| Data | Airflow, ClickHouse, CockroachDB, Convex, Dagster, Flink, Kafka, Marimo, MLflow, n8n, Postgres, Redpanda, RustFS, Slurm, Spark, Superset, Temporal |
| Ops | Monitoring |
| Apps | Coder, golink, Hermes, Immich, LiteLLM, MediaWiki, Stitch, WordPress |

## Layout

```text
pulumi/
  core/        # cluster plumbing
  data/        # databases + analytics + workflow
  ops/         # monitoring
  apps/        # end-user apps
```

Project-local assets live with the project that consumes them. Grafana dashboards are under each owning project's `dashboards/` directory, and Docker build assets are under service-local `images/` directories.

See [docs/index.md](docs/index.md) for the VitePress handbook and stack inventory.

## Root Commands

The root `Justfile` provides light wrappers around the per-project workflows:

- `just projects`: list Pulumi project directories.
- `just sync <project>`: run `uv sync` inside a project.
- `just preview <project> stack=<stack>`: run `pulumi preview`.
- `just up <project> stack=<stack>`: run `pulumi up`.
- `just preview-all`: preview every `mx` stack.
- `just check-python`: syntax-check all Pulumi Python entrypoints.
- `just lint`: run Ruff checks and formatting verification.
- `just format`: format Pulumi Python entrypoints with Ruff.

## Docs

The documentation site is built with VitePress:

- `npm run docs:dev`: start the local docs server.
- `npm run docs:build`: build the static docs site.
- `npm run docs:preview`: preview the built docs site.

## Notes

- Projects are independent and stack-scoped.
- `uv` is used for Python env/dependency management.
- Secrets live in Pulumi config secrets, not in git.
