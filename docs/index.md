# Infrastructure Handbook

This handbook is the front door for `kzh/infra`: a Pulumi and Python monorepo
that describes a Kubernetes-centered infrastructure estate. The repository is
not a collection of examples and it is not just a place where YAML happens to
live. It is the durable control surface for services, operators, databases,
networking, monitoring, and applications that are expected to keep working after
the next reconciliation loop.

The most useful first principle is ownership. Every meaningful thing in this
repo should have a clear home. A Pulumi project owns a bounded slice of
infrastructure. Service-owned dashboards live beside the service that emits the
metrics. Runtime image assets live beside the service that builds or deploys
them. Generated CRD bindings live under `pulumi/lib/` because they are shared
SDK artifacts, but they are still generated code, not a place for hand-written
behavior.

The second principle is proof. Documentation can tell you how to think about a
stack, but it cannot prove what is deployed right now. The code, Pulumi state,
stack configuration, ESC imports, and live Kubernetes state are the final
evidence. Use the docs to find the owner, understand the shape of the system,
choose the right checks, and avoid changing the wrong layer.

![Infrastructure topology icon](/topology.svg)

If you are new here, do not try to memorize the whole tree. Start by asking
what kind of work you are doing. Are you trying to understand the repository
itself, use a service, debug a broken path, or change live infrastructure? The
answer tells you which page to read first and which evidence to collect next.

## What This Repo Is

At the lowest level, `kzh/infra` is a set of Pulumi Python projects. A project
is normally a directory with a `Pulumi.yaml`, an entrypoint named `__main__.py`,
Python dependency files, and sometimes stack-local assets such as dashboards,
images, CRD source, or scripts. Each project can be synced, previewed, and, when
explicitly intended, applied independently.

At the operating level, this repo is a cluster handbook. It explains not only
what gets deployed, but how the pieces relate: which operators reconcile custom
resources, which data systems hold durable state, which services depend on
PostgreSQL or object storage, which paths expose private or public traffic, and
which checks are worth running before you trust a change.

At the human level, this repo is meant to reduce guesswork. A person should be
able to open the page for Spark, Airflow, Tailscale, MediaWiki, or Monitoring
and learn what the stack is for, where to find the owning code, how the service
is normally used, what state matters, and how to start debugging without jumping
straight to a one-off patch.

The repo is intentionally stack-scoped. A broad workflow in this checkout should
stay on the `mx` stack boundary unless a task names another boundary. That keeps
large previews and maintenance passes aimed at the infrastructure this checkout
is meant to manage.

## How To Start Reading

The fastest path through the repo is usually:

```text
intent -> owner -> desired state -> configuration -> preview -> live evidence
```

Start with intent. Decide whether you are trying to understand, operate, or
change something. Understanding work can begin in prose. Operations work needs
the service guide plus live checks. Change work needs the service guide, the
Pulumi program, the stack configuration surface, and a preview before anyone
should trust the result.

Then find the owner. In this repo, ownership normally follows the directory
layout:

| Area | What it owns |
| --- | --- |
| `pulumi/core/` | Cluster primitives: networking, operators, and security layers. |
| `pulumi/data/` | Databases, storage, streaming, analytics, and workflow systems. |
| `pulumi/apps/` | Internal and user-facing applications. |
| `pulumi/ops/` | Monitoring and operational infrastructure. |
| `pulumi/lib/` | Generated Pulumi Python packages for Kubernetes CRDs. |

Once you know the owner, read the project as a small system. `Pulumi.yaml` tells
you the project identity. `__main__.py` tells you the desired infrastructure
behavior. `pyproject.toml` and `uv.lock` tell you which Python packages and
providers the project is using. `Pulumi.<stack>.yaml` and ESC imports are part
of the real configuration surface, and they should be treated as sensitive even
when they do not visibly contain decrypted secrets.

The docs are there to make that reading easier. They should tell you why the
stack exists and how to reason about it before you inspect the exact code. They
should not replace inspection. If a page and the Pulumi program disagree, trust
the program, then fix the page.

## How The Handbook Is Organized

The top navigation has two main doors: [Repository](/repository/) and
[Stacks](/stacks/). Use the repository pages when you are trying to understand
how the monorepo works. Use the stack pages when you care about a specific
service.

[Repository Overview](/repository/) is the conceptual map. It explains the repo
as live infrastructure, the `mx` boundary, and the major dependency contracts
between platform, data, and application stacks.

[Layout](/repository/layout) explains where projects and assets belong. It is
the page to read before adding a stack, moving dashboards, adding an image
build, or deciding whether something belongs in `core`, `data`, `apps`, or
`ops`.

[Pulumi Model](/repository/pulumi-model) is for the Pulumi-specific mental
model: projects, stacks, outputs, references, resource names, and the difference
between passing values through the graph and adding ordering edges.

[Workflow](/repository/workflow) is the day-to-day operating path. It covers
scope discovery, `uv` sync, cheap checks, targeted previews, broad previews, and
the apply boundary.

[Configuration](/repository/configuration) is for stack config, ESC imports,
secrets, and output contracts. Read it before removing config keys, renaming
outputs, or assuming a value is unused.

[CRDs](/repository/crds) explains generated Pulumi bindings for Kubernetes
custom resources. Read it before touching `pulumi/lib/*_crds` or regenerating
typed packages from Helm chart CRDs.

[Observability](/repository/observability) explains how metrics, dashboards,
and monitoring resources fit into the repo. Use it when a dashboard is empty,
when a ServiceMonitor or PodMonitor changes, or when a stack begins exporting
new metrics.

[Operations](/repository/operations) is for failures and evidence. It is the
page to read when a URL returns an error, a service looks healthy but cannot be
used, a preview reports replacements, or live state and code appear to disagree.

[Security](/repository/security) covers confidentiality and review posture:
where secrets belong, what not to paste into docs or commits, and how to think
about private URLs, stack outputs, kubeconfig fragments, and generated values.

The [Service Guides](/stacks/) are grouped by the role a stack plays. `core`
contains platform foundations such as networking, operators, and security.
`data` contains durable systems, analytics tools, streaming services, and
workflow engines. `apps` contains user-facing or application-specific services.
`ops` contains monitoring.

That grouping matters during debugging. A broken app often points at more than
one page. If a database-backed app is down, read the app guide and the database
or operator guide. If a URL is broken, read the app guide and the relevant
networking guide. If a dashboard is blank, read the monitoring guide and the
service guide for the stack that owns the dashboard.

## Using The Docs For Real Work

For orientation, start with prose. Read the relevant handbook page until you can
say what the stack owns, what it depends on, what state matters, and what a
normal user path looks like. Then move to the code. The docs should make the
code less surprising; the code should make the docs concrete.

For a targeted change, read in this order:

```text
service guide
target Pulumi.yaml
target __main__.py
target pyproject.toml and uv.lock
stack config and ESC imports
nearby dashboards, images, CRDs, or scripts
```

Then run the repo's own commands. The exact project path matters:

```bash
just projects
just sync pulumi/apps/hermes
just preview pulumi/apps/hermes stack=mx
just check-python
just lint
git diff --check
```

The example uses Hermes only to show command shape. Substitute the actual
project you are changing. Do not run `just up`, `pulumi up`, or destructive
commands unless the task explicitly calls for a live apply or teardown.

For a debugging task, let the service guide give you the first questions, not
the final answer. A healthy pod does not prove a service is reachable. A
successful deployment does not prove a login flow works. A present dashboard
does not prove the scrape target is emitting data. Move from the symptom to the
owning stack, then to Kubernetes resources, logs, endpoints, ingress or
Tailscale exposure, storage, and dependent services as needed.

For a broad maintenance pass, begin with scope. `just projects` tells you which
Pulumi projects exist now. The repository pages explain which commands are
cheap local checks and which commands contact Pulumi or the cluster. Broad
previews should use the repository's `mx`-scoped workflow unless the work
explicitly says otherwise. When reporting failures, separate code regressions
from missing config, provider behavior, live-state drift, and chart migration
issues.

For a new stack, read [Layout](/repository/layout), then read a nearby service
guide in the same area. A good stack page should explain why the service exists,
what the Pulumi project deploys, how a person uses it, what state must be
protected, what other stacks consume or feed it, and how to validate a change.
The docs page should live where the VitePress sidebar expects it, but the
runtime assets should live next to the Pulumi project that consumes them.

## How To Treat The Docs

Treat docs as a map, not a substitute for looking. A map is still valuable: it
can tell you that MediaWiki depends on MySQL, that most private service access
goes through Tailscale, that monitoring owns Prometheus and Grafana, and that a
CRD-backed resource should be considered together with its operator. But a map
does not know what changed five minutes ago in a dirty worktree or a live
cluster.

When you find a mismatch, fix the smallest honest thing. If code changed and the
page is stale, update the page. If a page claims an ownership boundary that the
repo no longer follows, repair the wording. If a preview fails because of live
drift or missing configuration, report that as drift or configuration, not as a
documentation problem.

Avoid copying secret-bearing data into docs. Do not paste decrypted Pulumi
secret values, kubeconfig contents, private tokens, private URLs, full stack
outputs, or private account identifiers. Prefer operational descriptions such
as "requires the stack secret config" or "uses the private service exposure
path" over recording values that belong in Pulumi, ESC, Kubernetes Secrets, or a
local operator session.

Good documentation in this repo has a practical bias. It should help the next
person choose the right owner, understand the risk of a change, run the right
checks, and know what evidence would make the work trustworthy.

## A Short Reading Guide

If you are here to understand the repo, read [Repository Overview](/repository/)
first, then [Layout](/repository/layout), then [Workflow](/repository/workflow).

If you are here to use a service, start at [Service Guides](/stacks/) and choose
the stack by job: storage, workflow, analytics, app, networking, or monitoring.

If you are here because something is broken, start with the affected service
guide, then read [Operations](/repository/operations). Pull in networking,
database, storage, or monitoring pages when the symptom crosses stack
boundaries.

If you are here to change infrastructure, read the service guide and the
repository workflow, inspect the owning Pulumi project, run the cheap gates, and
preview the exact stack before treating the change as ready.

If you are here to add documentation, keep the same standard: explain the mental
model, name the owner, describe how a human uses the system, identify durable
state and dependencies, and show the checks that prove the page is still useful
when the cluster is real.
