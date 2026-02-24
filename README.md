# Infrastructure

Pulumi + Python infrastructure monorepo for a Kubernetes cluster.

## Included Services

| Area          | Services                                                    |
| ------------- | ----------------------------------------------------------- |
| Core Platform | Networking: Cloudflare Tunnel, Tailscale                    |
|               | Workloads: Ray, Spark, Temporal                             |
|               | Security: cert-manager, Vault                               |
| Data          | Postgres (with `vchord`), CockroachDB, ClickHouse, Redpanda |
| Apps          | Immich, code-server, Penpot, n8n                            |

## Layout

```text
pulumi/
  core/        # cluster plumbing
  data/        # databases + analytics + workflow
  ops/         # monitoring
  apps/        # end-user apps
```

## Notes

- Projects are independent and stack-scoped.
- `uv` is used for Python env/dependency management.
- Secrets live in Pulumi config secrets, not in git.
