# Repository Layout

The layout is a map of ownership. A Pulumi project should live where its
behavior is owned, where its day-two changes are likely to happen, and where a
reviewer would naturally look when that system changes.

That means the path is not just storage. It answers the first questions a
future change needs answered: is this cluster plumbing, a data system, an app,
or operational infrastructure? Does this stack own the dashboard or just expose
metrics to one? Is this Python code hand-written behavior or generated CRD
bindings?

## Areas

All deployable Pulumi projects live under `pulumi/<area>/...`.

```text
pulumi/
  core/        cluster primitives used by other stacks
  data/        databases, storage, streaming, analytics, and workflow systems
  apps/        internal and user-facing applications
  ops/         monitoring and operational infrastructure
  lib/         generated Pulumi Python packages for CRDs
```

`core/` is for infrastructure that defines how the cluster works. Networking
stacks such as `pulumi/core/networking/tailscale` and
`pulumi/core/networking/cf-tunnel` belong there because other services depend on
their access paths. Operators such as CloudNativePG, MySQL Operator, KubeRay,
cert-manager, and Vault also belong under `core/` because they provide platform
capabilities for other stacks.

`data/` is for systems whose main job is storing, moving, scheduling, or
processing data. The subdirectories keep related lifecycles together:
`data/databases/postgres`, `data/storage/rustfs`, `data/streaming/kafka`,
`data/analytics/spark`, and `data/workflow/airflow` are all data-plane systems,
but each has a different operational shape.

`apps/` is for applications people or internal workflows use directly. Coder,
Hermes, LiteLLM, MediaWiki, Stitch, WordPress, Immich, and golink live here
because the app is the unit of ownership. They may depend on `core/` or `data/`
outputs, but those dependencies do not move the app into those areas.

`ops/` is for infrastructure whose purpose is operating the cluster. Monitoring
belongs here because it provides the observability system itself. Service-owned
dashboards do not automatically move here; they stay beside the service that
knows what the dashboard means.

`lib/` is not a deployment area. It contains generated Pulumi Python packages
for Kubernetes CRDs, such as `pulumi/lib/tailscale_crds` and
`pulumi/lib/spark_operator_crds`. These packages are shared code, not stack
ownership.

## Projects And Stacks

A project is the directory with `Pulumi.yaml` and `__main__.py`. A stack is one
configured instance of that project, such as `mx`.

Keep those concepts separate. The project path describes what the program owns:
`pulumi/data/analytics/spark` owns the Spark operator and Spark-specific access
resources. The stack name describes where that program is instantiated. Do not
create area names for environments, machine names, or one-off targets when a
Pulumi stack already carries that distinction.

A normal project has this shape:

```text
pulumi/<area>/<category>/<service>/
  Pulumi.yaml
  Pulumi.<stack>.yaml
  __main__.py
  pyproject.toml
  uv.lock
```

`Pulumi.yaml` names the project and its runtime. `__main__.py` is the source of
desired infrastructure behavior. `pyproject.toml` and `uv.lock` describe the
Python environment used to run that behavior. `Pulumi.<stack>.yaml` holds
stack-local configuration and should be treated as sensitive unless it is
clearly safe to show. ESC `environment:` imports are part of the real config
surface too; do not ignore them when reasoning about how a stack runs.

Some projects have local support directories:

```text
dashboards/  Grafana dashboard JSON owned by this service
images/      Dockerfiles and runtime image assets owned by this service
crds/        rendered source CRD YAML used for generated bindings
scripts/     project-local helpers for generation or maintenance
```

These directories are optional. Add them when the project owns that asset, not
because every project needs the same template.

## Local Assets

Put assets next to the stack that understands them.

Spark dashboards belong in `pulumi/data/analytics/spark/dashboards/` because
Spark owns the metrics and the dashboard semantics. Airflow dashboards belong in
`pulumi/data/workflow/airflow/dashboards/`. Hermes image assets belong in
`pulumi/apps/hermes/images/` because they are part of running Hermes, not a
repo-wide build system.

This rule keeps changes reviewable. If a service changes labels, ports,
ServiceMonitor selectors, or exported metrics, the dashboard diff can sit next
to the Pulumi diff that made it necessary. If a service image changes, the
Dockerfile and runtime files move with that service instead of becoming a root
directory scavenger hunt.

The same ownership rule applies when the asset is consumed somewhere else. A
Grafana deployment may load dashboards from many projects, but that does not
make every dashboard part of `pulumi/ops/monitoring`. Monitoring owns Grafana
itself; each service owns the dashboard that describes its own behavior.

## Generated Code

Generated CRD bindings have two homes:

```text
pulumi/<owner>/crds/      rendered source CRD YAML from the owning chart
pulumi/lib/*_crds/       generated Pulumi Python package imported by stacks
```

For example, the Tailscale operator CRD source lives with
`pulumi/core/networking/tailscale`, while the generated package lives under
`pulumi/lib/tailscale_crds`. Spark follows the same pattern with
`pulumi/data/analytics/spark/crds` and `pulumi/lib/spark_operator_crds`.

Do not hand-edit generated packages under `pulumi/lib`. Regenerate them from
the owning project or the root CRD recipes:

```bash
just generate-crds
just generate-tailscale-crds
just generate-spark-crds
just check-crds
```

If generated code looks wrong, fix the chart version, CRD selection, generator
script, or owning source CRD file. Editing the generated SDK hides the real
cause and leaves the next generation run to overwrite the change.

## Adding A Stack

Start by choosing the owner, not the implementation detail.

If the new system is a workflow engine, it probably belongs under
`pulumi/data/workflow`. If it is a streaming platform, use
`pulumi/data/streaming`. If it is an application with its own user-facing
surface, use `pulumi/apps`. If it installs an operator that other stacks depend
on, use `pulumi/core/operators` or another `core/` category that matches the
primitive it provides.

Then create the smallest project shape that can run independently:

```text
pulumi/data/workflow/example/
  Pulumi.yaml
  __main__.py
  pyproject.toml
  uv.lock
```

Add `dashboards/`, `images/`, `crds/`, or `scripts/` only when the project owns
those files. For example, a custom scheduler image for the example workflow
system would live in `pulumi/data/workflow/example/images/`. A dashboard for
that system would live in `pulumi/data/workflow/example/dashboards/`.

Keep the Pulumi program explicit. Read a nearby project before copying patterns:
an app with a single Helm release should not inherit complexity from an
operator stack, and a CRD-backed operator should not hide ordering or ownership
inside a generic helper. Use `StackReference` when another project owns an
output you need. Export only values that another stack or human actually needs.

Stack config should be deliberate. New stack files are ignored by default in
this repo, so add a `Pulumi.<stack>.yaml` only when the config is meant to be
tracked and does not contain secret values. Use Pulumi secrets, Kubernetes
Secrets, generated passwords, or ESC-backed configuration for sensitive inputs.

Before previewing, let the repo tell you whether the shape is valid:

```bash
just projects
just sync pulumi/data/workflow/example
just check-python
just lint
just preview pulumi/data/workflow/example stack=mx
```

For this checkout, broad preview automation is intentionally scoped to `mx`
stacks. Non-`mx` targets should not appear in the repository layout just to make
them easier to find.

## What Not To Centralize

Do not centralize service-owned dashboards under monitoring just because Grafana
loads them. Centralize the loader if needed; keep the dashboard with the service
that owns the metrics.

Do not put Dockerfiles or image runtime files at the repo root when only one
service uses them. Put them in that service's `images/` directory.

Do not create a shared helper module for a pattern that has appeared once. A
small amount of repetition is often clearer than an abstraction that hides
Pulumi resource names, dependency edges, or replacement behavior.

Do not hide infrastructure behavior in shell scripts when it belongs in
`__main__.py`. Scripts are useful for generation, local maintenance, and narrow
automation. The desired cluster state should remain visible in Pulumi.

Do not make stack config a global registry. A project should be understandable
from its own directory, its stack config, and the outputs it explicitly imports.
Cross-project contracts should be narrow and named, not discovered through a
central pile of constants.
