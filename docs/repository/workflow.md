# Workflow

The day-to-day workflow in this repository is a steady loop:

```text
discover scope
read the owning project
sync its environment
run cheap checks
preview the intended stack
apply only when live change is requested
verify the service from the outside
report exactly what was checked
```

The root `Justfile` keeps that loop consistent across the monorepo. It does not
hide Pulumi from you; it gives you the same entrypoints everywhere so a Spark
change, a MediaWiki repair, and a Tailscale operator upgrade all start from a
familiar place.

This repo is live infrastructure. Treat the workflow as evidence gathering, not
ritual. Each command should answer a question: what project owns this behavior,
does the Python still load, what will Pulumi change, did the cluster actually
accept it, and can a human use the service afterward?

## Start With Ownership

Begin by finding the smallest project that owns the behavior.

```bash
just projects
```

Every directory with a `Pulumi.yaml` is an independently managed Pulumi project.
The broad layout is:

```text
pulumi/core/   cluster primitives: networking, operators, security
pulumi/data/   databases, storage, streaming, analytics, workflow
pulumi/apps/   internal and user-facing apps
pulumi/ops/    monitoring and operational infrastructure
pulumi/lib/    generated CRD bindings, not hand-written stack code
```

If the task names a stack, stay inside that boundary unless the code shows a
real dependency. If a URL is broken, the owning app is usually only one layer of
the path; the relevant trail may include Tailscale or Cloudflare routing,
Kubernetes Services and Endpoints, pods, and a database or object store. If a
producer stack changes an output, preview the consumers that read it through
`StackReference`.

For a target project, read the local files before changing behavior:

```text
Pulumi.yaml
__main__.py
pyproject.toml
uv.lock
Pulumi.<stack>.yaml, if present
dashboards/, images/, crds/, and scripts/ when they exist
```

Do not read only local stack YAML when debugging config. ESC environment imports,
Pulumi secret config, provider environment, and `StackReference` outputs are all
part of the real input surface.

## Work In An Active Tree

Assume other work is happening in the checkout.

```bash
git status --short
```

Run this before editing and again before reporting. If files outside your task
are already modified, leave them alone. If the file you need is already changed,
read it and patch around the existing work instead of replacing it wholesale.
Generated churn, dashboard moves, lockfile updates, and docs pages may belong to
another operator.

Useful habits:

```bash
git diff -- docs/repository/workflow.md
git diff --stat
git diff --check
```

`git diff --check` is both a whitespace gate and a small discipline: it forces a
look at what is actually staged or modified. Before commits or handoff, scan the
diff for credentials, kubeconfig fragments, decrypted Pulumi secret values,
private hostnames, private URLs, full stack outputs, and secret-bearing logs.

## Sync The Project

Use the root wrapper for project dependencies:

```bash
just sync pulumi/apps/hermes
```

This runs `uv sync` inside the project. Run it when dependencies or lockfiles
changed, when you are previewing a project for the first time in a session, or
when a provider import error suggests the environment is stale.

For broad dependency work, sync every affected project after lockfile changes.
For single-stack work, avoid turning a local fix into a repo-wide dependency
sweep unless the task calls for it.

## Run Cheap Gates

Most code changes should pass the fast checks before Pulumi contacts the
cluster:

```bash
just check-python
just lint
git diff --check
```

`just check-python` compiles each `__main__.py` and catches syntax or import
problems without a cluster call. `just lint` runs Ruff checks and formatting
verification over hand-written Pulumi code while excluding generated CRD
packages. `git diff --check` catches whitespace problems in every changed file.

These checks are not proof that infrastructure is correct. They prove the local
programs are loadable and the diff is mechanically clean enough to be worth
previewing.

After CRD generation, also use the CRD checks:

```bash
just check-crds
```

Generated CRD bindings under `pulumi/lib/*_crds` should change only through the
generation recipes. If a stack imports a regenerated package, preview that stack.

## Preview As The Main Evidence

Preview is where code, Pulumi state, provider behavior, stack config, and live
cluster state first meet.

```bash
just preview pulumi/apps/hermes stack=mx
```

Read the preview like an operational document, not a pass/fail light. Look for:

```text
creates that introduce new names, hosts, PVCs, buckets, or credentials
updates to selectors, ports, storage sizes, chart values, and image tags
deletes that remove user data, access paths, service identities, or monitors
replacements of PVCs, database clusters, Secrets, Services, and CRs
same-name conflicts from Helm or operator-rendered resources
provider waits that are noisy rather than meaningful
```

Replacement of a stateless Deployment may be expected. Replacement of a PVC,
database cluster, object store, Secret, Service identity, tailnet device, or
operator-owned custom resource deserves a pause and a reason.

When preview fails, classify the blocker before editing more code:

```text
missing config
bad ESC import
wrong stack name
provider authentication or kube context
live-state drift
provider await behavior
chart hook or migration issue
immutable field replacement
same-name Kubernetes object conflict
real program bug
```

That classification matters because the next step is different in each case.
Missing config usually means fixing the config surface. Live drift may call for
inspection or refresh. A Helm same-name conflict may need a deliberate
`delete_before_replace` or migration step. A real program bug belongs in code.

## Apply Boundaries

Do not apply by habit.

```bash
just up pulumi/apps/hermes stack=mx
```

Run `just up`, `pulumi up`, `pulumi destroy`, or destructive cleanup only when
the task explicitly asks for live infrastructure changes. Previewing is safe
enough for ordinary investigation; applying changes cluster state.

Before apply, be able to say:

```text
which project and stack are being changed
what the preview showed
which resources are stateful
which replacements are expected
which config or outputs are sensitive
what live check will prove the service still works
```

After apply, verify from the user's point of view. A successful Pulumi update
means reconciliation completed. It does not prove the route works, the Service
has endpoints, the login page loads, the workflow runs, or the dashboard has
data.

Common live checks:

```bash
kubectl get pods,svc,ingress,endpoints -n <namespace>
kubectl logs -n <namespace> deploy/<name> --tail=200
kubectl describe svc -n <namespace> <name>
tailscale ping <hostname>
curl -fsSI <url>
```

Pick checks that match the service. For Airflow, a smoke DAG tells you more than
a ready web pod. For Spark, UI and Connect paths are different surfaces. For a
dashboard change, verify the ConfigMap or sidecar load path before assuming
Grafana saw it.

## Broad Sweeps

Broad work starts with inventory and ends with clear scope.

```bash
just projects
just check-python
just lint
git diff --check
just preview-all
```

`just preview-all` previews stacks named `mx` that are managed by this checkout.
It discovers that boundary through Pulumi stack metadata, not by assuming every
project has a local `Pulumi.mx.yaml`. It writes logs under:

```text
/tmp/pulumi-mx-previews-<timestamp>
```

There is also a refresh mode for the cases where the question is specifically
about state drift:

```bash
just preview-all mode=refresh
```

Use broad preview results carefully. A failing stack in a sweep does not always
mean your code broke it. Separate:

```text
new regression from this diff
pre-existing missing config
bad ESC reference
provider or kube auth issue
live-state drift
chart/provider behavior
out-of-scope stack
```

Do not widen a broad run outside `mx` unless the task explicitly asks for a
cross-machine audit. Targeted work can use whatever stack the user named; broad
repository sweeps stay `mx` by default.

## Dependency And Upgrade Work

Treat chart, provider, image, and CRD upgrades as migrations. A version bump can
change resource names, selectors, value schemas, hook behavior, CRD validation,
wait behavior, and generated bindings.

A good upgrade pass usually reads:

```text
the owning project files
chart values or release notes when needed
the generated CRD diff, if CRDs changed
consumer stacks when outputs or shared contracts changed
the targeted preview
```

Do not quiet a preview by weakening required config or hiding resources behind
runtime logic. Pulumi should be able to show the intended graph. Pass `Output`
values directly into inputs when possible, and use `.apply()` for value
transformation rather than resource creation.

## Reporting Results

Report what you actually checked, with enough detail for the next person to pick
up the thread.

Good reports include:

```text
files changed
commands run
preview target and result
apply target and result, only if an apply was requested
live checks performed
known blockers and their category
log directory for broad preview runs
remaining risk or assumptions
```

Keep secrets and private environment details out of the report. Prefer
descriptions such as "missing required secret config" or "preview blocked by a
bad ESC import" over pasting sensitive values. For broad previews, include the
log directory and a summarized failure category, not secret-bearing excerpts.

Be precise about confidence. "The pod is ready" is different from "the service
has endpoints" and different again from "the UI loaded and the smoke workflow
completed." The best handoff says exactly which layers were proven and which
remain inferred.

## Commit And Handoff

Before committing, inspect the final diff. Look for unrelated edits, accidental
generated churn, config files that should not be tracked, private identifiers,
and secret material.

Good commit messages explain the infrastructure behavior change. If one commit
contains several related pieces, itemize the body:

```text
- update the chart version and values
- regenerate CRD bindings
- adjust the ServiceMonitor selector
- document the new validation command
```

The goal is not ceremony. The goal is that future operators can see what changed,
why it changed, how it was verified, and where to look if the live system later
disagrees with the repo.
