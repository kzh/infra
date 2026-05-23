# Coder

Source: `pulumi/apps/coder`

Coder is the development workspace control plane for this cluster. From first
principles, it separates "where I type and run commands" from "the laptop in
front of me." A user signs in to Coder, chooses a workspace template, and Coder
uses that template to create a Kubernetes-backed environment with compute,
storage, networking, and an editor or SSH path.

That separation is the whole point. The laptop only needs a browser, SSH, or
the Coder CLI. The actual development machine can be reproducible, remote,
closer to cluster services, easier to rebuild, and less dependent on whatever
tools happen to be installed locally.

This repo owns the Coder platform pieces: the Coder server, its database
connection, the Kubernetes namespaces and permissions it needs, the Service,
and the private ingress path. Workspace templates are the next layer up. They
define what a workspace is: image, resources, volumes, startup scripts, exposed
ports, editor setup, and any template parameters. At the time of this source
read, `pulumi/apps/coder` installs and exposes the Coder control plane; it does
not contain a checked-in template directory.

## The Mental Model

Think about Coder as three related systems, not one pod:

```text
control plane    Coder web/API server, database, Service, ingress, auth
templates        Terraform definitions stored and versioned by Coder
workspaces       Kubernetes resources created from templates for users
```

The control plane answers the web UI, stores users/templates/workspace records,
and coordinates builds. Templates describe how to create the actual developer
environment. Workspaces are the resulting pods, PVCs, Services, agents, and
other resources that run user work.

That distinction is the most useful debugging tool on this page. The Coder UI
can be healthy while every new workspace fails to schedule. A workspace pod can
be running while the Coder agent cannot connect back. A workspace can be
"up-to-date" with its template while editor extensions inside the workspace are
still old. Do not collapse all of those into "Coder is broken." Ask which plane
is broken.

## What This Stack Creates

The Pulumi project is a Python 3.12 project using `uv`, `pulumi`,
`pulumi-kubernetes`, and `pulumi-postgresql`. The project is intentionally
small: it wires a Helm release into this cluster's database and private routing
conventions.

In order, the program:

1. Reads stack config and the shared PostgreSQL stack reference.
2. Creates the Coder application namespace.
3. Optionally creates the `coder` database in shared PostgreSQL.
4. Builds a PostgreSQL connection URL and stores it in a Kubernetes Secret.
5. Optionally creates a separate namespace for user workspaces.
6. Installs the upstream Coder Helm chart.
7. Creates a Pulumi-owned Kubernetes Ingress instead of using chart-managed
   ingress.
8. Exports the important resource names and access settings.

Important defaults from `pulumi/apps/coder/__main__.py`:

```text
Application namespace:        coder
Workspace namespace:          coder-workspaces
Helm chart:                   coder
Helm chart version:           2.33.3
Helm repository:              https://helm.coder.com/v2
Service type:                 ClusterIP
Ingress enabled:              true
Ingress class:                tailscale
Database name:                coder
Database Secret:              coder-db-url
Coder server resources:       100m/256Mi request, 500m/1Gi limit
Workspace namespace create:   true
Workspace permissions:        true
Workspace deployments:        true
Default GitHub auth provider: disabled
```

`Pulumi.mx.yaml` supplies the real stack values for the `mx` stack, including
the PostgreSQL producer stack, the private ingress host, and the access URL.
Do not paste those private values into docs, screenshots, tickets, or commit
messages. Use the local Pulumi outputs when you need them.

## Control Plane Wiring

The Coder server runs in the application namespace, normally `coder`. The Helm
chart creates the Deployment, ServiceAccount, Service, RBAC, and other chart
resources inside that namespace. This Pulumi program passes chart values rather
than rendering those resources by hand.

The Coder server pod is given an explicit modest resource budget in this repo:
`100m` CPU and `256Mi` memory requested, with limits of `500m` CPU and `1Gi`
memory. That is intentionally separate from workspace resource settings.
Workspace CPU, memory, images, volumes, and startup behavior come from Coder
templates stored in Coder, not from this Pulumi stack.

The chart's Service is configured as `ClusterIP`. That means the Service is not
directly exposed outside the cluster. External access goes through the Ingress
created by this Pulumi project.

The stack sets `CODER_PG_CONNECTION_URL` from the `coder-db-url` Secret. It also
sets `CODER_ACCESS_URL` when an access URL is configured or derived from the
ingress host. That URL matters because Coder uses it for redirects, callbacks,
agent connection behavior, and links shown to users. If the UI loads but login,
workspace agent connections, or generated links point to the wrong place, check
`access_url` before changing templates.

The stack disables Coder's default GitHub OAuth provider by setting
`CODER_OAUTH2_GITHUB_DEFAULT_PROVIDER_ENABLE=false`. That does not mean Coder
has no authentication story; it means this deployment is not relying on the
chart's default GitHub provider being silently enabled. Add or change auth
deliberately, with the provider settings and secrets handled through supported
Coder/Pulumi paths.

## Database Wiring

Coder's durable control-plane state lives in PostgreSQL. The program requires a
`postgres_stack` config value and reads these outputs from that producer stack:

```text
rw_service_fqdn
port
username
password
ts_hostname
host
```

There are two database paths to keep separate:

```text
Pulumi provider path   how Pulumi connects to PostgreSQL to create the database
Coder runtime path     how the Coder pod connects to PostgreSQL from Kubernetes
```

The PostgreSQL provider host prefers the producer stack's Tailscale hostname
when present and falls back to the regular host output. That is for Pulumi's
management connection while the program is running.

The runtime connection URL uses the PostgreSQL read-write service FQDN, port,
username, password, database name, and SSL mode. The URL is stored as the
`url` key in the `coder-db-url` Secret in the Coder namespace. Treat the
contents of that Secret as credential material. It is fine to check that the
Secret exists and that the Coder pod references it; do not dump the decoded URL
into docs or chat.

If `create_database` is true, Pulumi creates the Coder database through the
PostgreSQL provider. If it is false, the stack still builds the runtime
connection URL, but it assumes the database already exists.

If the database is unavailable, the Coder UI usually fails at the control-plane
level. Look at Coder server logs, the existence of `coder-db-url`, and the
PostgreSQL producer stack contract before debugging workspace pods.

## Namespace And Workspace Permissions

By default this stack separates the Coder control plane from user workspaces:

```text
coder              Coder server, Service, Secret, Ingress, chart resources
coder-workspaces   user workspace pods and template-created resources
```

That separation is useful because the control plane has one lifecycle and user
workspaces have another. Restarting the Coder server should not be treated as
the same thing as deleting workspace pods or PVCs.

The Coder Helm values set `serviceAccount.workspacePerms` and
`serviceAccount.enableDeployments`. When the workspace namespace differs from
the app namespace, the program also passes it through
`serviceAccount.workspaceNamespaces`. In plain language: the Coder server needs
permission to create and manage resources for user workspaces in the namespace
where those workspaces live.

If users can open the UI but workspace builds fail with Kubernetes permission
errors, check this layer:

```bash
cd pulumi/apps/coder
APP_NS="$(pulumi stack output --stack mx namespace)"
WORK_NS="$(pulumi stack output --stack mx workspace_namespace)"

kubectl get serviceaccount,role,rolebinding -n "$APP_NS"
kubectl get serviceaccount,role,rolebinding -n "$WORK_NS"
kubectl get pods,pvc,events -n "$WORK_NS"
```

Be careful with namespace changes. Renaming the workspace namespace is not just
a cosmetic change. Existing workspace records, template assumptions, PVCs, and
Kubernetes resources may still refer to the old namespace.

## Ingress And Access URL

The chart's own ingress is disabled:

```text
coder.ingress.enable = false
```

This repo creates the Kubernetes Ingress separately. The nearby comment in
`__main__.py` explains why: the stack avoids Helm await behavior that is noisy
for Tailscale ingress status. The Pulumi-owned Ingress has these default
annotations:

```text
pulumi.com/skipAwait: true
pulumi.com/patchForce: true
```

For the Tailscale ingress class, the stack creates an Ingress with a default
backend pointing at the `coder` Service on port `80` and a TLS host based on the
first label of the configured ingress host. For non-Tailscale ingress classes,
the stack creates normal host rules and can optionally include a wildcard host
and TLS Secret references.

That distinction matters for workspace apps. Coder can expose services from a
workspace through its own app/proxy features, and some deployments use wildcard
domains for that. In this stack, `ingress_wildcard_host` is only wired into the
non-Tailscale ingress branch. Do not assume setting a wildcard host changes the
Tailscale ingress shape.

When debugging access, keep these checks in order:

```bash
cd pulumi/apps/coder
APP_NS="$(pulumi stack output --stack mx namespace)"

pulumi stack output --stack mx access_url
pulumi stack output --stack mx ingress_resource
kubectl get ingress -n "$APP_NS"
kubectl describe ingress -n "$APP_NS" coder
kubectl get svc,endpoints -n "$APP_NS" coder
kubectl get pods -n "$APP_NS" -l app.kubernetes.io/name=coder
```

If the Ingress resolves but the Service has no endpoints, routing is not the
root problem. Check the Coder pod readiness and labels. If endpoints exist but
the browser cannot connect, move outward to the ingress controller and tailnet
route.

## How A User Starts Workspaces

For normal use, start in the web UI:

1. Open the stack's `access_url`.
2. Sign in.
3. Choose a template.
4. Fill in template parameters, if any.
5. Create or start the workspace.
6. Connect through the browser editor, local SSH, or a supported local editor
   path.

The template is the important user-facing contract. It decides what image runs,
what CPU and memory are requested, what persistent storage exists, what startup
script runs, which ports or apps are exposed, and what editor path is offered.
The Pulumi stack can make Coder available, but a template makes it useful.

If a workspace is over-reserved or over-limited, fix that in the Coder template
and then update or rebuild the affected workspace. Patching a live workspace
Deployment can reduce immediate cluster overcommit, but the next Coder template
update or workspace rebuild may recreate the original resource values.

The Coder CLI is useful once the local machine is authenticated:

```bash
cd pulumi/apps/coder
export CODER_URL="$(pulumi stack output --stack mx access_url)"

coder login "$CODER_URL"
coder templates list
coder create my-workspace --template <template-name>
coder list
coder show my-workspace --details
coder ssh my-workspace
coder stop my-workspace
coder start my-workspace
```

For SSH integration:

```bash
coder config-ssh --dry-run
coder config-ssh
ssh my-workspace.coder
```

Use `coder update <workspace>` when a workspace should move to the active
template version. That updates the workspace against its Coder template; it does
not update every tool, editor extension, package cache, or cloned repo inside
the workspace. If the UI says a workspace is current but the editor still feels
old, inspect the editor/runtime inside the workspace as a separate layer.

If the CLI fails before it can list workspaces, check local state before
debugging the cluster:

```bash
which -a coder
coder version
coder whoami
coder list
```

Client/server version mismatches and expired local auth can look like a broken
deployment from the user's chair. They are local access problems until proven
otherwise.

## What Templates Should Do

Coder templates are Terraform definitions. They are infrastructure, and they
deserve the same review posture as Pulumi code. A template should make the
workspace predictable, fast enough to start, and clear enough for a user to
understand what changed when a new version lands.

A practical template answers these questions:

```text
what base image runs?
what CPU, memory, and GPU resources are requested?
what files persist across restarts?
what startup commands run every time?
what secrets or external auth paths are required?
what ports/apps are exposed?
how does a user update an existing workspace?
how does an operator debug a failed build?
```

Keep startup scripts small. Every package install, clone, model download, and
extension update in a startup script adds time and another failure point. If all
workspaces need the same toolchain, prefer a maintained image over a long
startup script. If only one project needs something special, make that explicit
in the template rather than hiding it in a general-purpose script.

Treat template storage as user state. If a template creates a PVC for home
directories or repo checkouts, that PVC may matter more than the pod. Replacing
a pod is routine. Deleting a PVC can delete work.

Treat template names and parameters like API. Users automate against them,
copy settings between workspaces, and build habits around them. Renames and
parameter removals should have a migration story.

If templates are later added to this repo, keep them close to their owning
project or clearly link to their source. Do not let important templates exist
only as unexplained UI state.

## Day-To-Day Operational Check

Pod readiness is not enough for Coder. The product is the full loop:

```text
open Coder
create or start a workspace
connect to the workspace
run a command inside it
stop the workspace
start it again
confirm expected files or state remain
```

That sequence catches problems a green Deployment misses:

```text
bad access URL
broken login redirect
missing workspace namespace permissions
bad template image
slow image pulls
PVC mount problems
agent connection failures
editor/runtime drift inside the workspace
```

Use Kubernetes checks for the platform and Coder CLI checks for the user path.
They answer different questions.

```bash
cd pulumi/apps/coder
APP_NS="$(pulumi stack output --stack mx namespace)"
WORK_NS="$(pulumi stack output --stack mx workspace_namespace)"

kubectl get pods,svc,endpoints,ingress -n "$APP_NS"
kubectl get pods,pvc,events -n "$WORK_NS"
coder list
coder show <workspace> --details
coder logs <workspace>
```

If a workspace has multiple builds, `coder logs -n <build-number> <workspace>`
can target an older or specific build. Use `coder logs -f <workspace>` while
watching a fresh start.

## Debugging By Symptom

The UI is unreachable. Start with the control plane: Ingress, Service,
endpoints, Coder pod readiness, and Coder server logs. Then move outward to the
Tailscale ingress controller or other ingress class.

```bash
kubectl get ingress,svc,endpoints,pods -n "$APP_NS"
kubectl logs -n "$APP_NS" -l app.kubernetes.io/name=coder --tail=200
```

The UI loads but redirects or links are wrong. Check `access_url`,
`ingress_host`, and any auth provider callback settings. Coder is sensitive to
the URL users and agents are told to use.

The Coder pod loops or reports database errors. Check that the `coder-db-url`
Secret exists, that the pod references it, and that the PostgreSQL producer
stack still exports the expected outputs. Do not print the decoded connection
URL as part of routine debugging.

```bash
kubectl get secret -n "$APP_NS" "$(pulumi stack output --stack mx db_secret_name)"
kubectl logs -n "$APP_NS" -l app.kubernetes.io/name=coder --tail=200
```

Templates are missing or template updates fail. Use Coder's template commands
and provisioner information before changing Kubernetes:

```bash
coder templates list
coder templates pull <template-name> /tmp/<template-name>
coder templates versions list <template-name>
coder provisioner list
```

Workspace creation fails before a pod exists. Look at Coder build logs and
template/provisioner errors. The problem may be Terraform syntax, template
variables, a missing external auth path, or permissions to create Kubernetes
resources.

```bash
coder logs <workspace>
coder show <workspace> --details
kubectl get events -n "$WORK_NS" --sort-by=.lastTimestamp
```

The workspace pod is `Pending`. Check scheduling, resource requests, node
capacity, storage class behavior, PVC binding, taints, and tolerations. This is
a Kubernetes placement problem until the events say otherwise.

```bash
kubectl describe pod -n "$WORK_NS" <workspace-pod>
kubectl get pvc -n "$WORK_NS"
kubectl get events -n "$WORK_NS" --sort-by=.lastTimestamp
```

The workspace pod is in `ImagePullBackOff` or `ErrImagePull`. Check the image
name, registry access, image pull secrets, architecture, and tag. The Coder
server can be completely healthy while a template points at an image the
cluster cannot pull.

The workspace pod is running but the editor or SSH path cannot connect. Check
the Coder agent logs from the UI or CLI, then inspect pod logs and network
reachability back to Coder. The agent must be able to reach the Coder control
plane URL it was configured with.

```bash
coder show <workspace> --details
coder logs <workspace>
kubectl logs -n "$WORK_NS" <workspace-pod> --tail=200
```

SSH works but browser editor behavior is odd. Separate Coder connectivity from
the editor process inside the workspace. Check the template's editor setup,
workspace startup output, extension directories, and any per-user dotfiles or
post-start scripts. A Coder workspace can be alive while the editor layer needs
attention.

Workspace update says the workspace is current, but tools inside it are old.
`coder update` updates the workspace to the active template version. It does
not prove the image was rebuilt, package caches were refreshed, editor
extensions were updated, or dotfiles ran successfully. Inspect the running
workspace environment directly.

## State And Recovery

Coder has more than one kind of state:

```text
PostgreSQL database   users, templates, workspace records, settings
workspace resources   pods, PVCs, Services, Secrets, template-created objects
user files            whatever templates store on persistent volumes
local CLI state       login tokens, SSH config, local Coder binary
```

Losing the PostgreSQL database means losing the control plane's memory of Coder
objects. Losing a workspace PVC means losing the user's workspace files even if
Coder still has a record for the workspace. Breaking local CLI auth can make a
healthy deployment look unreachable from one machine.

When recovering, identify which state plane is damaged before taking action.
Do not delete workspace PVCs or namespaces as generic cleanup. Do not rotate or
replace database credentials casually. Do not treat local CLI repair as proof
that the cluster was broken.

## Safe Changes In This Repo

For documentation-only edits to this page, stay in this file. Other docs pages
may be changing in parallel.

For code or config changes to the Coder stack, use the repo workflow:

```bash
git status --short
just sync pulumi/apps/coder
just check-python
just lint
git diff --check
just preview pulumi/apps/coder stack=mx
```

Do not run `pulumi up`, `pulumi destroy`, or `just up` unless the user has
explicitly asked for an apply or destroy operation.

Think about Coder changes by blast radius:

```text
chart version             migration; read release notes and preview carefully
access_url/ingress_host   login, redirects, agents, links, workspace apps
workspace namespace       all workspace resources and permissions
workspace permissions     ability to create pods/PVCs/deployments
database settings         control-plane state and startup
service type              exposure model and ingress assumptions
auth settings             login path and account access
template changes          every workspace created or updated from that template
```

Renames are especially sensitive. A Pulumi resource name, Kubernetes
`metadata.name`, namespace, database name, template name, or hostname may be a
contract even if it looks small in a diff. Use aliases or staged migrations
when preserving state matters.

After a real apply, validate the user path, not just the preview:

```text
open the Coder URL
confirm login works
start an existing workspace
create a fresh test workspace if appropriate
connect with the expected editor or SSH path
run a command inside the workspace
stop and restart it
confirm expected persistent state remains
```

If the change only touched routing, still test a workspace connection. If the
change only touched workspace permissions, still open the UI. The two planes
meet through the agent and access URL, so half-tests miss common failures.

## Secrets And Personal Material

The docs should describe categories and commands, not private values. Keep
these out of docs, issues, commit messages, and screenshots:

```text
decoded database URLs
Pulumi secret outputs
private access URLs
tailnet-specific hostnames
OAuth client secrets
session tokens
private SSH keys
workspace files with credentials
```

If a workspace needs credentials, prefer Coder's supported secret mechanisms,
Kubernetes Secrets, external auth, or a reviewed template path. Avoid pasting
secrets into startup scripts. If a personal SSH key must be copied into a
workspace for a one-off task, use normal SSH/SCP paths, set strict file
permissions, and treat any exposed key as compromised.

## Quick Reference

Read non-secret stack outputs:

```bash
cd pulumi/apps/coder

pulumi stack output --stack mx namespace
pulumi stack output --stack mx workspace_namespace
pulumi stack output --stack mx access_url
pulumi stack output --stack mx ingress_resource
pulumi stack output --stack mx helm_chart_version
```

Inspect the control plane:

```bash
APP_NS="$(pulumi stack output --stack mx namespace)"

kubectl get pods,deploy,svc,endpoints,ingress,secrets -n "$APP_NS"
kubectl logs -n "$APP_NS" -l app.kubernetes.io/name=coder --tail=200
kubectl describe ingress -n "$APP_NS" coder
```

Inspect workspace resources:

```bash
WORK_NS="$(pulumi stack output --stack mx workspace_namespace)"

kubectl get pods,pvc,svc,secrets -n "$WORK_NS"
kubectl get events -n "$WORK_NS" --sort-by=.lastTimestamp
kubectl describe pod -n "$WORK_NS" <workspace-pod>
```

Use Coder as a user:

```bash
export CODER_URL="$(pulumi stack output --stack mx access_url)"

coder login "$CODER_URL"
coder templates list
coder list
coder show <workspace> --details
coder logs <workspace>
coder ssh <workspace>
```

Preview stack changes:

```bash
just sync pulumi/apps/coder
just check-python
just lint
git diff --check
just preview pulumi/apps/coder stack=mx
```
