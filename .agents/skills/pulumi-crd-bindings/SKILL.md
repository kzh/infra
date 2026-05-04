---
name: pulumi-crd-bindings
description: Generate repo-local typed Pulumi Python bindings for Kubernetes CRDs in the kzh/infra Pulumi monorepo using crd2pulumi. Use when adding, regenerating, or auditing CRD bindings from Helm charts, converting raw Kubernetes CustomResource usage to first-class generated types, or wiring new pulumi/lib/*_crds packages for Pulumi stacks. Do not use for manually authoring CustomResourceDefinition resources.
---

# Pulumi CRD Bindings

## Scope

Use the repo-local generator. Keep CRD definition installation owned by Helm/operator stacks; these bindings are for Pulumi programs that create CR instances.

Do not automatically rewrite application code from `k8s.apiextensions.CustomResource` to generated classes unless the user explicitly asks. The usual default is to generate the typed package and show the import/class path so the user can replace the resource call themselves.

## Generate

For an existing registered package, prefer the wrapper target:

```bash
just generate-monitoring-crds
just generate-tailscale-crds
just generate-kuberay-crds
just generate-spark-crds
just generate-clickhouse-crds
just generate-crds
```

For a new chart/package, use the generic target:

```bash
just generate-crd-package \
  foo_crds \
  "Foo Operator CRDs" \
  foo-chart \
  https://example.com/helm-charts \
  1.2.3 \
  foos.example.com,bars.example.com \
  pulumi/core/operators/foo \
  foo-release-name
```

Arguments:

- `python_name`: generated package folder under `pulumi/lib`, usually `*_crds`.
- `title`: README title for the generated package.
- `chart`, `repo`, `version`: pinned Helm chart source.
- `crd_names`: comma-separated CRD names to include. Use `-` only when the full chart CRD bundle is wanted.
- `owner_dir`: Pulumi project directory that owns the operator/chart. The vendored YAML lands in `<owner_dir>/crds/<chart>-<version>.crds.yaml`.
- `release_name`: optional Helm template release name.

Prefer filtering to only CRDs the repo instantiates. Full chart bundles can be large and may expose `crd2pulumi` schema bugs unrelated to the CRs being used.

## After Generation

Inspect the generated module/class path:

```bash
find pulumi/lib/foo_crds -name '*.py' | rg 'Foo|Bar'
rg -n 'type_token|class Foo' pulumi/lib/foo_crds
```

When the user wants the Pulumi stack wired too:

1. Add the generated package to the stack `pyproject.toml`.
2. Add `[tool.uv.sources]` pointing to the relative `pulumi/lib/<python_name>` path.
3. Run `uv sync` in that Pulumi project.
4. Import the generated class from the generated module path.
5. Replace the raw `CustomResource` constructor while preserving Pulumi resource name, Kubernetes `metadata.name`, namespace, spec, opts, and dependencies.

## Validate

Always run the cheap gates after generator or wiring changes:

```bash
just check-python
just lint
git diff --check
```

If a stack resource was changed, run a targeted preview:

```bash
cd <pulumi-project>
pulumi preview --stack mx --diff
```

Report whether the preview shows creates, updates, replacements, or deletes. Do not run `pulumi up` unless the user explicitly asks.

## Troubleshooting

- If `crd2pulumi` tries to use the chart version as a Kubernetes plugin version, keep the generated provider package version pinned to the Kubernetes provider version used by the repo.
- If `crd2pulumi` panics or cannot parse a full chart bundle, regenerate with a filtered `crd_names` list.
- If generated Python is not Ruff-clean, keep `pulumi/lib` excluded from lint/format; do not hand-edit generated SDK files.
