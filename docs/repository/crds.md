# CRDs

Kubernetes starts with a fixed API: Pods, Services, Deployments, Secrets,
Namespaces, and the other built-in resource kinds. A CustomResourceDefinition
adds another API type to that same API server. After a CRD is registered,
Kubernetes can store and validate objects with a new group, version, and kind,
such as `monitoring.coreos.com/v1 ServiceMonitor` or
`sparkoperator.k8s.io/v1alpha1 SparkConnect`.

The CRD is only the API definition. It says what objects can exist and what
their schema looks like. It does not, by itself, make those objects useful. An
operator or controller watches the custom resources and reconciles real cluster
behavior from them. In this repo, that distinction is the most important rule:
operator stacks own CRD installation and controllers; generated Pulumi packages
only help Python programs create custom resources.

Think of a CRD-backed feature as three separate layers:

| Layer | What it is | Repo owner |
| --- | --- | --- |
| API definition | The Kubernetes `CustomResourceDefinition` YAML. | The operator or platform project that installs the chart. |
| Controller | The operator Deployment, webhooks, RBAC, and chart values that reconcile custom resources. | The same operator or platform project. |
| Custom resources | The `RayCluster`, `ProxyClass`, `ServiceMonitor`, `SparkConnect`, `ClickHouseInstallation`, MySQL cluster, or similar object. | The Pulumi project that declares the desired resource. |

Do not blur those layers. A generated Python class is not the CRD. Importing
`SparkConnect` from a generated package does not install the Spark operator
CRDs. Deleting a generated package from `pulumi/lib` does not remove anything
from the cluster. Removing or renaming a live CRD in an operator stack is a real
Kubernetes API migration and can remove access to every custom resource of that
kind.

## Repo Ownership

CRD source YAML lives beside the project that owns the operator or chart. The
shared generated Python packages live under `pulumi/lib/*_crds` because more
than one Pulumi project may import them.

| Generated package | Source CRD YAML | Main typed resources | Current consumers |
| --- | --- | --- | --- |
| `pulumi/lib/mysql_operator_crds` | `pulumi/core/operators/mysql/crds/mysql-operator-2.2.8.crds.yaml` | `InnoDBCluster`, `MySQLBackup`, `MySQLClusterSetFailover` | MediaWiki and WordPress app stacks. |
| `pulumi/lib/monitoring_crds` | `pulumi/ops/monitoring/crds/prometheus-operator-crds-29.0.0.crds.yaml` | `ServiceMonitor`, `PodMonitor` | Monitoring resources in service stacks such as Cloudflare Tunnel, Tailscale, KubeRay, Airflow, and Postgres. |
| `pulumi/lib/tailscale_crds` | `pulumi/core/networking/tailscale/crds/tailscale-operator-1.96.5.crds.yaml` | `ProxyClass` | Tailscale networking stack. |
| `pulumi/lib/kuberay_crds` | `pulumi/core/operators/kuberay/crds/kuberay-operator-1.6.1.crds.yaml` | `RayCluster` | KubeRay operator stack. |
| `pulumi/lib/spark_operator_crds` | `pulumi/data/analytics/spark/crds/spark-operator-2.5.0.crds.yaml` | `SparkConnect` | Spark analytics stack. |
| `pulumi/lib/clickhouse_operator_crds` | `pulumi/data/analytics/clickhouse/crds/altinity-clickhouse-operator-0.27.0.crds.yaml` | `ClickHouseInstallation` | ClickHouse analytics stack. |

The generated packages are artifacts. Do not hand-edit files under
`pulumi/lib/*_crds`. If the generated code is wrong or stale, change the CRD
source, chart version, filter list, or generator recipe, then regenerate. The
root lint recipe deliberately excludes `pulumi/lib` because generated SDK code
does not need to follow the handwritten Python style rules.

## When Bindings Matter

Pulumi can always create a custom resource through
`k8s.apiextensions.CustomResource`: pass an `apiVersion`, a `kind`, and a
dictionary-shaped body. That works, but it leaves a lot of important behavior as
plain strings and nested dictionaries. Typos in group/version/kind names, stale
field names, and shape changes are harder to see during review.

Generated bindings turn the CRD schema into a local Pulumi Python package. A
consumer can import a resource class directly:

```python
from pulumi_spark_operator_crds.sparkoperator.v1alpha1 import SparkConnect
```

That import path encodes the API group and version. The resource class gives the
Pulumi program a clearer graph node than a raw `CustomResource`, and generated
input classes make nested specs easier to review. The binding also creates a
normal Python dependency, so each stack records the local package it uses in
`pyproject.toml` and `uv.lock`.

Bindings are worth maintaining when this repo owns durable CRD-backed resources:
database clusters, monitoring scrape objects, operator-specific clusters,
Tailscale classes, and analytics services. They are less important when a stack
only installs an operator chart and never creates custom resources itself. Do
not convert every raw custom resource just because a CRD exists. Generate or
wire a binding when it makes the resource safer to maintain and the repo is
going to keep owning that API surface.

Generated bindings are not a complete safety net. They are generated from the
CRD schema at one point in time. The live API server may have a different CRD if
the operator stack is stale or drifted. The operator can also reject or ignore a
spec that passes Kubernetes schema validation. Treat the binding as a better
Pulumi interface, not as proof that the live operator will reconcile the object.

## How Generation Works

The root `Justfile` is the entrypoint. It has one MySQL-specific generator and a
generic Helm chart generator for the other packages.

```bash
just generate-mysql-crds
just generate-monitoring-crds
just generate-tailscale-crds
just generate-kuberay-crds
just generate-spark-crds
just generate-clickhouse-crds
just generate-crds
```

`just generate-crds` runs every known package generator. Use it for broad CRD
refreshes. Use a narrower target when you are changing one operator or one
consumer stack.

The MySQL target runs `pulumi/core/operators/mysql/scripts/generate_crds.sh`.
If no version is passed, it reads the MySQL operator chart version from the
operator stack config, pulls that chart, renders its CRDs with Helm, writes the
vendored CRD YAML under `pulumi/core/operators/mysql/crds/`, and regenerates
`pulumi/lib/mysql_operator_crds`.

The generic targets call `just generate-crd-package`, which wraps
`scripts/generate_crd_package.sh`:

```bash
just generate-crd-package \
  foo_crds \
  "Foo Operator CRDs" \
  foo-operator \
  https://example.invalid/helm-charts \
  1.2.3 \
  foos.example.com,bars.example.com \
  pulumi/core/operators/foo \
  foo-operator
```

The arguments are:

| Argument | Meaning |
| --- | --- |
| `python_name` | Generated package directory under `pulumi/lib`, usually ending in `_crds`. |
| `title` | Short title written into the generated package README. |
| `chart`, `repo`, `version` | Pinned Helm chart source used to render CRDs. |
| `crd_names` | Comma-separated CRD names to keep, or `-` to keep every CRD rendered by the chart. |
| `owner_dir` | Pulumi project that owns the operator or chart; the CRD YAML is written to `<owner_dir>/crds/<chart>-<version>.crds.yaml`. |
| `release_name` | Helm template release name, when the chart needs one. |

The script runs `helm template --include-crds`, filters to the selected CRD
names with `yq`, writes the vendored CRD YAML, clears the target package
directory, runs `crd2pulumi`, and normalizes the generated Python package
dependencies to this repo's Pulumi provider floor. The generated provider
package version is currently pinned to `4.31.0`. That version is the generated
Pulumi package version, not the operator chart version.

Prefer a filtered `crd_names` list. Helm charts often ship CRDs that this repo
does not instantiate. Feeding the full bundle into `crd2pulumi` creates larger
diffs and can expose schema issues in APIs that are irrelevant to the stack you
are maintaining.

## Checking Generated State

To ask whether the committed CRD artifacts match the generator recipes, run:

```bash
just check-crds
```

This regenerates all CRD packages and then runs a path-scoped `git diff` over
the CRD source directories and generated packages. A failure means at least one
generated artifact is stale or the generator is no longer reproducible.

After any CRD generation or CRD-backed consumer change, run the cheap repo
gates:

```bash
just check-python
just lint
git diff --check
```

For a changed stack, also preview the stack that owns or consumes the resource:

```bash
just preview pulumi/data/analytics/spark stack=mx
```

When you need the full resource diff, run Pulumi directly inside the project:

```bash
cd pulumi/data/analytics/spark
pulumi preview --stack mx --diff
```

Do not apply from this workflow unless the task explicitly asks for a live
change.

## Adding Or Updating A Package

Start from ownership, not from the generated code. Identify the operator or
chart that owns the Kubernetes API. Its project gets the vendored CRD YAML under
`crds/`. The shared generated package goes under `pulumi/lib`.

For a new durable binding:

1. Choose the owner project and chart version.
2. Decide which CRDs this repo actually instantiates.
3. Add a `generate-...-crds` wrapper in the root `Justfile`.
4. Add the package to `generate-crds` and the path list in `check-crds`.
5. Regenerate and review the CRD YAML diff and generated package diff.
6. Wire only the stacks that need the binding.

Consumer wiring is normal Python packaging. Add the generated import name to
the stack `pyproject.toml`, add a local `[tool.uv.sources]` entry pointing to
the relative `pulumi/lib/<package>` path, then run:

```bash
just sync pulumi/data/analytics/spark
```

After that, replace the raw custom resource only if the task includes that
consumer change. Preserve the Pulumi logical resource name, Kubernetes
`metadata.name`, namespace, labels, spec, `opts`, and real dependencies unless
you are intentionally migrating them. A binding change should not accidentally
become a resource rename.

## Changing CRD-Backed Resources Safely

A CRD-backed resource has three contracts that can break independently:
Kubernetes schema, operator reconciliation, and Pulumi identity.

Schema changes are API changes. A chart upgrade can add required fields, remove
old fields, tighten enum values, change defaults, or move a resource to a new
version. The Python program can still compile while the live API rejects the
object. Always review the vendored CRD YAML diff and the generated binding diff,
not just the chart version.

Operator behavior is a second contract. The CRD may accept a spec that the
operator no longer reconciles the same way. Check chart release notes and the
operator's expected migration path when changing chart versions, storage fields,
selectors, service templates, or anything with finalizers.

Pulumi identity is the third contract. The Pulumi resource name and Kubernetes
`metadata.name`/namespace decide whether Pulumi updates an object in place or
creates a new one. Changing `apiVersion` or `kind` can also become a new
resource from Kubernetes' point of view. For data-bearing CRs such as database
clusters, treat replacements and deletes as high-risk until the preview proves
otherwise.

Use staged migrations when compatibility is uncertain. If both old and new CRDs
accept the current spec, refresh the operator and CRDs first, then update the
custom resources in a second previewable step. If the old CRD will not accept
the new spec, do not update the consumer before the API exists. If the new CRD
will not accept the old spec, plan the transition explicitly rather than hoping
one preview can hide the ordering problem.

Never delete a CRD casually. Deleting a CRD removes that API from the cluster
and can delete all custom resources of that type. Removing an operator chart
resource, changing a Helm release name, or changing CRD ownership can therefore
have much wider impact than a normal Deployment replacement.

Before merging a CRD-backed change, be able to answer:

```text
Which stack owns the CRD definition and operator?
Which stacks create custom resources of that kind?
Did the CRD YAML change?
Did the generated package change?
Did any Pulumi logical resource name, Kubernetes metadata.name, namespace, apiVersion, or kind change?
Does preview show creates, updates, replacements, or deletes?
Are replacements intentional and safe for this resource?
```

## Common Failure Modes

If `crd2pulumi` fails on a chart bundle, reduce the input to the CRDs this repo
uses through the `crd_names` argument. The goal is a maintainable package for
repo-owned resources, not a complete SDK for every API a chart happens to ship.

If a consumer cannot import a generated package, check `pyproject.toml`,
`[tool.uv.sources]`, and `uv.lock`, then run `just sync <project>`. Generated
packages are local path dependencies; they do not come from PyPI.

If preview or apply reports that Kubernetes has no match for a kind, the live
cluster probably does not have that CRD registered on the selected context, or
the program is using the wrong group/version. Inspect the operator stack and the
vendored CRD YAML before changing consumer code.

If a Helm upgrade reports that a same-name CRD already exists, treat it as an
ownership or migration problem. Do not work around it by creating duplicate CRDs
from a consumer stack. The operator stack owns the CRD definition, and the fix
belongs there.
