# Repository Guidelines

This repository is a Pulumi + Python infrastructure monorepo for a Kubernetes
cluster. Treat it as live infrastructure: inspect the current repo and stack
state before changing behavior, keep edits scoped, and verify with the
repository's own commands.

## First Checks

- Run `git status --short` before editing. The worktree is often active; do not
  revert or rewrite changes you did not make.
- Read `README.md`, `docs/PROJECTS.md`, and the target project's
  `Pulumi.yaml`/`__main__.py` before broad changes.
- For repo-wide Pulumi work, start with `just projects` to define scope.
- If the task matches a local skill under `.agents/skills`, use that workflow.
  The most commonly relevant ones here are `pulumi-best-practices`,
  `pulumi-esc`, `pulumi-crd-bindings`, `provider-upgrade`, and `package-usage`.

## Project Layout

Pulumi projects live under `pulumi/<area>/<service>/`:

- `pulumi/core/`: cluster networking, operators, and security primitives.
- `pulumi/data/`: databases, storage, streaming, analytics, and workflow tools.
- `pulumi/apps/`: end-user or internal applications.
- `pulumi/ops/`: monitoring and operational infrastructure.

Each project is intended to be stack-scoped and independently synced,
previewed, applied, or destroyed. Most projects contain `__main__.py`,
`Pulumi.yaml`, `pyproject.toml`, and `uv.lock`.

Keep project-owned assets beside the project that consumes them:

- Grafana dashboards belong in the owning project's `dashboards/` directory.
- Dockerfiles and runtime image assets belong in service-local `images/`
  directories.
- Generated CRD bindings live under `pulumi/lib/*_crds`; do not hand-edit
  generated SDK files.

## Commands

Use the root `Justfile` unless a project has a more specific local recipe.

```bash
just projects
just sync pulumi/apps/penpot
just preview pulumi/apps/penpot stack=mx
just up pulumi/apps/penpot stack=mx
just check-python
just lint
just format
just preview-all
```

Important command expectations:

- `just sync <project>` runs `uv sync` inside a single Pulumi project.
- `just check-python` syntax-checks Pulumi entrypoints without contacting the
  cluster.
- `just lint` runs Ruff checks and format verification, excluding generated
  CRD packages under `pulumi/lib`.
- `just preview-all` previews only stacks named `mx`, writes logs under
  `/tmp/pulumi-mx-previews-<timestamp>`, and may stop on failures that are
  caused by missing config or live-state drift rather than code regressions.
- Do not run `pulumi up`, `pulumi destroy`, or `just up` unless the user
  explicitly asks for an apply/destructive action.

## Python and Pulumi Style

- Use Python 3.12 for hand-written Pulumi projects and `uv` for dependency
  management.
- Keep Pulumi entrypoints explicit and readable. Add abstractions only when they
  remove real repeated complexity.
- Prefer stable Pulumi resource names and Kubernetes `metadata.name` values.
  Renames can force replacement, so use aliases or deliberate migration steps
  when preserving state matters.
- Pass Pulumi `Output` values directly as inputs when possible. Use `apply` for
  value transformation, not for creating resources.
- Use `StackReference` for cross-project dependencies and preserve the expected
  output contracts when refactoring.
- Use `ResourceOptions(depends_on=...)` for real ordering constraints. Do not
  use dependency edges as a substitute for passing outputs.
- Use `config.require_secret`, Pulumi secret outputs, generated passwords, or
  Kubernetes Secrets for sensitive values. Never hard-code credentials.

## Helm, CRDs, and Chart Upgrades

- Treat chart and image upgrades as migrations, not simple version bumps.
  Check chart values/schema changes, preserve selectors, and run previews.
- If preview/apply reports a same-name Kubernetes object already exists, inspect
  whether the chart renders a resource that needs `delete_before_replace=True`
  instead of reverting the upgrade.
- Use `pulumi.com/skipAwait` only for known resources where waiting is
  consistently wrong or noisy, and keep the reason obvious from nearby code.
- Keep CRD installation owned by the relevant operator/Helm stack. Generated
  typed packages under `pulumi/lib` are for creating custom resources from
  Pulumi code.
- Use the CRD generator targets instead of ad hoc generation:

```bash
just generate-crds
just generate-mysql-crds
just check-mysql-crds
just check-crds
just generate-monitoring-crds
just generate-tailscale-crds
just generate-kuberay-crds
just generate-spark-crds
just generate-clickhouse-crds
```

After CRD generation or CRD-backed stack wiring, run:

```bash
just check-python
just lint
git diff --check
```

Then run a targeted `pulumi preview --stack <stack> --diff` for any changed
stack.

## ESC and Stack Configuration

- ESC `environment:` imports are part of the real configuration surface. Include
  them when auditing config usage or drift; do not rely only on
  `Pulumi.*.yaml`.
- Treat `Pulumi.*.yaml`, Pulumi stack outputs, kube contexts, hostnames,
  tailnet/domain details, tokens, and secret names/values as sensitive unless
  they are clearly intended to be public.
- New stack files are ignored by default. Be deliberate before adding or
  modifying tracked stack configuration.
- If a preview fails after code changes, classify the blocker before editing
  more code: missing config, bad ESC reference, live cluster drift, provider
  behavior, or a real program bug.

## Validation Workflow

For most code changes:

```bash
just check-python
just lint
git diff --check
```

For a changed stack, also run a targeted preview:

```bash
just preview pulumi/<area>/<service> stack=<stack>
```

For broad sweeps, `just preview-all` is useful after the cheap gates pass. It is
intentionally `mx`-only for this checkout; non-`mx` stacks are not managed from
this repository. When reporting results, separate code regressions from
environment or live-state blockers and include the log directory, not
secret-bearing log excerpts.

## Security and Confidentiality

- Do not commit credentials, API tokens, private keys, decrypted Pulumi secret
  values, kubeconfig contents, or personal account identifiers.
- Do not paste full Pulumi outputs, ESC values, kubeconfig data, Notion database
  IDs, private URLs, or secret-bearing logs into docs, commit messages, or PR
  text.
- Prefer summaries such as "missing required secret config" over naming or
  exposing sensitive values.
- Before committing or opening a PR, review `git diff` for accidental secrets
  and environment-specific identifiers.
- If a task touches an external Notion/database workflow, verify the live
  database/page state first, keep copied content handling faithful to the
  request, and do not store personal rankings, database IDs, or private page
  content in this repository.

## Collaboration Notes

- The user values direct execution with live verification. If a preview is
  important to the change, run the preview rather than stopping at static checks.
- Broad cleanup is welcome when requested, but keep it behavior-preserving and
  avoid deleting functionality.
- When the repo is already dirty, patch narrowly around unrelated changes.
- For large repetitive work, parallelize investigation or log analysis when the
  user asks for speed, but keep file edits coordinated and non-overlapping.
