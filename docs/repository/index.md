# Repository Operating Guide

This repository is the desired-state source for a live Kubernetes cluster. It is
not a catalog of examples, and it is not a single application with one deploy
button. It is a Pulumi monorepo made of many small Pulumi Python projects. Each
project owns a slice of infrastructure, has its own dependency environment, and
can be synced, previewed, applied, or debugged without loading every other
project into memory.

The root of the repository provides shared habits: a common layout, a root
`Justfile`, shared generated CRD bindings, and documentation that explains how
to move through the system. The projects under `pulumi/` provide the actual
resource graphs. When the two disagree, trust the Pulumi project and the live
state first, then update the docs to match reality.

The most useful mental model is simple: a directory with a `Pulumi.yaml` is a
deployable unit, and the stack name selects which live instance of that unit you
are talking to. The repository helps you find the unit, build its Python
environment, preview its graph, and understand what it consumes from or exports
to the rest of the cluster.

## The Shape Of The Repo

The top-level `pulumi/` tree is organized by ownership rather than by technology
brand. This matters because the owner of a stack is also the owner of its
dashboards, local images, CRD source files, and operational docs.

```text
pulumi/
  core/        cluster primitives: networking, operators, security
  data/        databases, storage, streaming, analytics, workflow
  apps/        internal and user-facing applications
  ops/         monitoring and operational infrastructure
  lib/         generated Pulumi Python packages for Kubernetes CRDs
```

`core/` is where the cluster gains basic capabilities. Networking stacks expose
services through systems such as Tailscale or Cloudflare Tunnel. Operator stacks
install controllers such as CloudNativePG, the MySQL operator, KubeRay, or other
platform-level controllers. Security stacks own cluster-wide security
primitives, including certificate and secret-management building blocks. A data
or application stack should not quietly install its own copy of a shared
operator because that hides a platform dependency inside a leaf service.

`data/` is where shared data and compute systems live. Database stacks, object
storage, streaming systems, analytics engines, and workflow tools belong here
when they are products other stacks use or when they form part of the shared
data platform. A service like Airflow, Spark, Kafka, Postgres, RustFS, or Trino
belongs here because it is infrastructure for work performed by other systems,
even if it has a web UI.

`apps/` is for applications that a person or another product uses directly as an
application. These stacks can depend on `core/` and `data/`, but their primary
purpose is the app itself: its Deployment, persistence, ingress, app-specific
database, and user-facing runtime configuration.

`ops/` is for the infrastructure that helps operate the cluster. Monitoring
lives here because it provides the shared Prometheus and Grafana substrate. A
service-specific dashboard does not belong here merely because Grafana displays
it; that dashboard belongs next to the service whose labels, metrics, and
failure modes it describes.

`lib/` is different. It contains generated Python packages for Kubernetes CRDs.
Those packages are shared implementation artifacts, not a place for hand-written
service logic. If a generated type is wrong, change the CRD source or the
generation recipe, regenerate, and review the generated diff. Do not patch the
generated package by hand.

## What Belongs With A Project

A Pulumi project should be readable as a small local system. When you open a
project directory, the files should tell you what the stack owns, how it is
configured, which dependencies it needs, and which local assets move with it.

```text
Pulumi.yaml        project metadata and runtime
Pulumi.<stack>.yaml stack-local config when tracked for that stack
__main__.py        the Pulumi program and resource graph
pyproject.toml     Python package and Pulumi provider dependencies
uv.lock            locked dependency environment
dashboards/        Grafana dashboards owned by this service
images/            Dockerfiles and runtime image assets owned by this service
crds/              CRD source YAML when this project owns generation input
scripts/           project-local maintenance helpers
```

Keep assets next to the stack that consumes them. If Spark owns a dashboard,
that dashboard should live under the Spark project. If an app needs a custom
runtime image, the Dockerfile and support files should live under that app. This
keeps review local: a change to metrics, labels, selectors, image entrypoints,
or container ports can be reviewed beside the assets that depend on those
details.

Avoid global piles of service-owned assets. A central monitoring project should
provide the monitoring platform, not collect every dashboard just because
Grafana loads them. A repository root should not become the home for app image
build contexts. Shared helpers are useful only when they remove real repeated
complexity and preserve readability in each project that calls them.

## Projects, Stacks, And State

Pulumi separates the program from the stack. The program is the Python code in a
project. The stack is the named state instance Pulumi uses when it compares that
program with live infrastructure. In this repository, most normal operation is
against the `mx` stack, but the idea is general: the same project can have more
than one stack, and each stack has its own config and state.

Python execution builds a Pulumi resource graph. Pulumi then previews or applies
that graph against the selected stack and the live providers. Python statement
order is not the same as infrastructure dependency order. Real ordering comes
from resource inputs, outputs, providers, and explicit `depends_on` edges where
they are actually needed.

Pulumi resource names and Kubernetes `metadata.name` values are part of state.
Changing them can force replacements, and replacement risk depends on the
resource. Replacing a stateless Deployment may be acceptable. Replacing a PVC,
database cluster, generated Secret, Service identity, bucket, or operator-owned
custom resource can mean data loss, broken clients, or a migration that needs
explicit staging. Preview exists so you can see those consequences before an
apply.

Stack outputs are APIs. When Postgres exports connection information, when RustFS
exports S3 coordinates, when a networking stack exports host or proxy details,
or when an app exports a service URL, consumers may depend on the output name,
meaning, and secret-ness. Changing an output is not just tidying a variable; it
is changing a contract between stacks.

## How Projects Fit Together

The repository is easiest to reason about as layers of contracts rather than as
a flat list of services.

The lower layer is cluster capability. Operator stacks install controllers and
CRDs. Networking stacks provide ways for services to be reached. Security stacks
provide certificate and secret-management primitives. Monitoring provides the
shared metrics and dashboard substrate. These stacks tend to be producers:
their health determines whether higher-level stacks can reconcile.

The middle layer is shared data and compute. Database, storage, streaming,
analytics, and workflow stacks create services that other stacks consume through
`StackReference`, Kubernetes DNS, Secrets, or chart values. A database stack can
preview cleanly while one of its consumers is still broken because the consumer
expects a specific output, namespace, credential shape, CA value, Service name,
or port. When changing a producer, identify and preview the consumers that
depend on the contract you touched.

The leaf layer is applications. App stacks usually combine chart values or
manifests, persistence, runtime configuration, ingress, and references to shared
data services. Their correctness is not proved by a green Deployment alone. An
app can have ready pods but zero Service endpoints, a correct Service but a
wrong ingress port, a working ingress but a broken database credential, or a
healthy backend that is unreachable through the expected private route.

The practical dependency map changes as the repository evolves, so do not treat
any prose list as complete. Run `just projects` for the current project
inventory, then read the producer and consumer programs involved in the change.
Look for `StackReference`, imported generated CRD packages, chart values that
name other services, ServiceMonitor or PodMonitor selectors, and outputs used by
humans or scripts.

## The `mx` Boundary

In this checkout, broad repository workflows target stacks named exactly `mx`.
That boundary is intentional. It keeps repo-wide previews and sweeps focused on
the cluster this checkout manages by default, and it avoids mixing in
machine-specific or experiment-specific stacks that may exist elsewhere.

`just preview-all` is the clearest example. It runs through the Pulumi projects
from `just projects`, asks Pulumi which stacks exist for each project, and
previews only the stack named `mx`. It writes logs under a timestamped directory
like:

```text
/tmp/pulumi-mx-previews-<timestamp>
```

That means the boundary is the Pulumi stack name, not merely the presence of a
local `Pulumi.mx.yaml` file. Some stack config can live outside local YAML, and
some local files may not define the whole real configuration surface. ESC
environment imports and Pulumi Cloud state are part of the system too.

For targeted work, use the stack named by the task. For broad work, default to
`mx` unless the task explicitly asks for another scope. Do not widen a broad
preview because another stack name appears in local state, in Pulumi Cloud, or
in a remembered workflow. A different stack can be real and still be outside
the operating boundary for this repository pass.

## The Root Workflow

Use the root `Justfile` as the entrypoint for ordinary work. It is intentionally
thin: it lists projects, runs `uv sync` inside one project, previews one
project, applies one project when requested, runs cheap Python checks, and
executes broad `mx` previews.

Start by discovering scope:

```bash
git status --short
just projects
```

`git status --short` matters because this repository is often active. Other
people or other tasks may be editing unrelated pages, dashboards, stack code, or
lockfiles. Do not revert unrelated changes to make your own diff look clean.
`just projects` matters because the project list changes over time. It is a
better source of the current inventory than memory.

For a single project, sync before previewing when dependencies or lockfiles may
matter:

```bash
just sync pulumi/apps/hermes
```

For most code changes, run the cheap gates before contacting the cluster:

```bash
just check-python
just lint
git diff --check
```

These checks catch syntax errors, import problems, Ruff issues, formatting
drift, and whitespace mistakes. They do not prove the live graph is acceptable.
They make previews less noisy by removing basic local failures first.

Preview the project whose behavior changed:

```bash
just preview pulumi/apps/hermes mx
```

The optional stack is the second recipe argument in the current root wrapper.
If the `Justfile` changes, follow the file in front of you, but keep the
principle: preview the selected project against the selected stack before
claiming the infrastructure change is safe.

Apply only when the user or operator explicitly asked for a live change:

```bash
just up pulumi/apps/hermes mx
```

An apply is not a formatting step. It changes live infrastructure. After an
apply, verify the human-facing or system-facing behavior that motivated the
change. For a web service, that can mean checking pods, Services, endpoints, and
the route. For a workflow system, it may mean a smoke workflow. For a metrics
change, it may mean confirming the monitor target appears and the dashboard can
load useful data.

For broad preview work:

```bash
just preview-all
```

Report the log directory and classify failures. A failed broad preview may
indicate a code regression, but it may also be missing config, an ESC issue,
provider wait behavior, live-state drift, chart hook behavior, or a same-name
Kubernetes object conflict. Do not paste secret-bearing logs into docs or
messages. Summarize the category and point to the local log directory.

## Reading Before Editing

A safe change begins with reading the right layers. For a repository-wide docs
or workflow change, read the README, root `Justfile`, current docs, and
`just projects`. For a stack change, read the target project files:
`Pulumi.yaml`, `__main__.py`, `pyproject.toml`, `uv.lock`, stack config, and any
local `dashboards/`, `images/`, `crds/`, or `scripts/` directories relevant to
the edit.

Config deserves special attention. The visible `Pulumi.<stack>.yaml` file may
not contain every value. ESC environment imports, Pulumi secret config,
`StackReference` outputs, provider environment, kube context, and external CLI
auth can all affect preview behavior. If a preview says config is missing, first
understand where that config is expected to come from. Do not replace a required
value with a silent default just to quiet the preview.

When live behavior is involved, inspect live state instead of relying only on
the Pulumi program. Kubernetes state, Pulumi state, and application behavior can
disagree. A Service can have no endpoints. An ingress can point at the wrong
Service port. A pod can be ready while the application is misconfigured. A chart
can render a Job or hook that Pulumi sees differently from the operator that
created it.

## Moving Safely

Keep changes scoped to the owner. If a problem is in an app's Service selector,
fix the app stack that owns the Service. If a dashboard panel depends on labels
emitted by Spark, change the Spark dashboard next to Spark. If a generated CRD
binding is stale, update the CRD generation input and regenerate the package.
The closer the fix is to the owner, the easier it is to review and the more
likely it is to survive reconciliation.

Prefer durable Pulumi changes over one-off cluster patches. A live `kubectl`
patch can be useful for diagnosis, but if the desired behavior should persist,
encode it in the owning Pulumi project. Otherwise the next preview, apply,
operator reconciliation, or chart upgrade can undo the emergency fix.

Treat Helm and chart upgrades as migrations. Read chart values and schema
changes. Watch for release-name changes, hooks, immutable fields, selector
changes, CRD changes, and resources that already exist under the same
Kubernetes name. If the right fix is `delete_before_replace`, make it deliberate
and keep the reason clear near the resource. If the right fix is an alias, use
an alias. If the right fix requires cleanup of live resources, do not perform it
without an explicit apply or destructive-operation request.

Use Pulumi `Output` values directly where possible. Reach for `.apply()` when
you need to transform a value, not when you need to create resources. Creating
resources inside an `apply` hides graph shape and makes previews harder to
reason about.

Use explicit `depends_on` for real ordering constraints. Do not use dependency
edges as a substitute for passing actual outputs. If one resource needs a value
from another resource, pass the value. Pulumi can infer the graph from that
input, and future readers can see the contract directly.

Protect stateful resources. Before changing a database, object store, PVC,
workflow metadata store, generated credential, or network identity, name the
durable state and decide how it survives the change. A small-looking rename can
be a replacement. A replacement can be harmless for a controller and serious for
data.

## Secrets And Private Details

Treat configuration, outputs, kubeconfig material, private hostnames, private
URLs, account identifiers, secret names, and generated credentials as sensitive
unless they are clearly meant to be public. Documentation should explain
categories, contracts, and commands, not publish values.

If an operator needs a secret, point to the local retrieval path rather than
including the secret:

```bash
pulumi stack output --stack mx --show-secrets <output-name>
```

Before committing, inspect the diff yourself. Tooling can help, but a human
review should still look for decrypted secret values, token-shaped strings,
kubeconfig fragments, private URLs, overly specific hostnames, and copied
external-system content. Generated dashboard JSON deserves review too because
labels, annotations, links, and templating variables can leak environment
details.

## Adding Or Extending A Stack

Choose the area by ownership. Put platform primitives in `core/`, shared data
and compute systems in `data/`, applications in `apps/`, and operational
substrate in `ops/`. Create a normal Pulumi Python project with its own
`Pulumi.yaml`, `__main__.py`, `pyproject.toml`, and `uv.lock`. Keep local
dashboards, image assets, CRD source, and helper scripts beside the project.

Export only the outputs that humans or other stacks need. Mark secrets as
secrets. Give outputs stable names. A short output list is easier to support
than a dump of every internal value the chart rendered.

Wire dependencies through explicit contracts. If the new stack needs Postgres,
read the Postgres stack outputs through `StackReference` rather than copying
cluster-local details. If it needs private exposure, use the established
networking stack patterns. If it emits metrics, keep monitors and dashboards
with the service and make sure selectors match actual labels.

Add docs that explain first principles, not just command snippets. A useful page
answers what the stack is, what it owns, what it depends on, how it is exposed,
what data must survive, how to preview changes, how to verify health, and what
failure modes deserve caution.

## When Something Breaks

Follow the path layer by layer. For a URL, inspect the route, ingress or private
exposure mechanism, Kubernetes Service, endpoints, pods, readiness, and
application dependencies. For missing metrics, inspect ServiceMonitor or
PodMonitor selectors, Service labels, endpoint names, Prometheus targets, and
dashboard queries. For a preview failure, classify the failure before editing
more code.

Useful failure categories include missing config, bad ESC reference,
live-state drift, provider wait behavior, chart hook behavior, same-name object
conflict, immutable-field replacement, and real program bugs. The category
determines the fix. Missing config is not solved by weakening the program.
Same-name object conflicts are not solved by renaming stateful resources without
understanding ownership. Provider wait noise should not cause broad removal of
wait behavior that protects real readiness elsewhere.

When reporting, say what you checked. A precise summary is better than a broad
claim. Include the preview command, the stack, the log directory for broad
previews, the Kubernetes namespace inspected, whether Services had endpoints,
whether pods were ready, and what live smoke test was performed. If something is
inferred rather than verified, say so.

## How To Use These Docs

This page is the conceptual entry point. The sibling pages are narrower
references:

- [Layout](./layout.md) explains project anatomy and ownership.
- [Pulumi Model](./pulumi-model.md) explains outputs, config, resource names,
  Helm behavior, and Pulumi graph shape.
- [Workflow](./workflow.md) covers root commands and routine validation.
- [Configuration](./configuration.md) focuses on config sources, ESC, secrets,
  and `StackReference` contracts.
- [CRDs](./crds.md) covers generated CRD packages and regeneration commands.
- [Operations](./operations.md) covers live debugging patterns.
- [Observability](./observability.md) covers dashboards, monitors, and metrics.
- [Security](./security.md) covers what not to commit and how to write safe
  operational docs.

Read prose as guidance and commands as starting points. The final authority for
a change is the current project code, the selected Pulumi stack, the current
root `Justfile`, and the live system you actually inspected.
