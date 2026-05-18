# ConvexDB

Source: `pulumi/data/databases/convexdb`

ConvexDB in this repo is a self-hosted Convex deployment on Kubernetes. It is
not a plain database endpoint that clients should connect to with SQL. It is an
application backend with a client API, a human dashboard, an admin-key workflow,
and durable state underneath.

The shortest useful mental model is:

```text
client apps and tools
        |
        v
Convex API ingress -> Convex backend Deployment -> PostgreSQL database
                                             \-> backend PVC

operators and developers
        |
        v
Convex dashboard ingress -> dashboard Deployment -> Convex API URL
```

The Convex backend is the important runtime surface. Client applications talk to
that API. The dashboard is a separate web UI that points at the API. PostgreSQL
and the backend PVC are backing state, not the ordinary app interface.

That distinction matters operationally. A ready dashboard pod does not prove the
backend is usable. A ready backend pod does not prove the application workflow
has the right admin key. A ready PostgreSQL cluster does not prove Convex and its
PVC are in a restorable state. Treat the stack as one stateful product, not as
three unrelated pods.

## What Pulumi Builds

The `convexdb` project is a Python Pulumi project using `uv`. It creates the
Kubernetes resources for Convex and also provisions the PostgreSQL database that
Convex uses.

The stack currently does these things:

- Creates the `convexdb` namespace.
- Reads the shared PostgreSQL stack through `StackReference`.
- Creates a Convex-specific PostgreSQL role and database with the PostgreSQL
  provider.
- Generates a PostgreSQL password for that role with `pulumi-random`.
- Writes a Kubernetes Secret named `convexdb-postgres` containing the
  `POSTGRES_URL` that the backend pod reads.
- Writes a Kubernetes Secret named `convexdb-postgres-ca` containing CA material
  mounted into the backend pod.
- Creates a `convexdb-storage` PersistentVolumeClaim for backend local data.
- Runs a `convexdb-backend` Deployment with a single replica and `Recreate`
  rollout strategy.
- Runs a separate `convexdb-dashboard` Deployment.
- Creates `ClusterIP` Services for the backend and dashboard.
- Exposes the backend API and dashboard through separate private Tailscale
  ingresses.
- Exports URLs, resource names, image settings, PostgreSQL identifiers, the PVC
  name, and a command for generating an admin key.

The important defaults are:

```text
Namespace:               convexdb
Instance name:           convexdb
PostgreSQL stack ref:    kzh/postgresql/mx
PostgreSQL database:     convexdb
PostgreSQL user:         convexdb
Storage class:           local-path
Storage size:            10Gi
PVC:                     convexdb-storage
Backend Deployment:      convexdb-backend
Dashboard Deployment:    convexdb-dashboard
Backend Service:         convexdb-backend
Dashboard Service:       convexdb-dashboard
Backend API port:        3210
Backend site port:       3211
Dashboard port:          6791
API ingress host:        convexdb-api
Dashboard ingress host:  convexdb
API URL shape:           https://convexdb-api
Dashboard URL shape:     https://convexdb
Site URL setting:        https://convexdb-api/http
```

The `mx` stack imports PostgreSQL CA material and the PostgreSQL read-write
service FQDN from ESC. Do not remove the `environment:` import from stack config
when auditing this project. It is part of the live configuration surface.

Images are pinned by digest unless config overrides them. The code supports
three image paths:

- `backend_image` and `dashboard_image` can set each image explicitly.
- `image_tag` can move both images to the same tag when explicit image values
  are not set.
- the source has digest-pinned defaults.

Backend and dashboard images should normally move together. If you intentionally
move only one, preview carefully and verify the dashboard still speaks the
backend API it expects.

## The Self-Hosted Convex Mental Model

In hosted Convex, the platform hides much of the backend, storage, dashboard,
and credential machinery. In this repo, those pieces are explicit Kubernetes and
PostgreSQL resources.

The backend pod is the Convex service. It has the API port, stores local backend
data under `/convex/data`, reads PostgreSQL connection material from a Secret,
and receives origin settings from environment variables:

```text
CONVEX_CLOUD_ORIGIN=https://convexdb-api
CONVEX_SITE_ORIGIN=https://convexdb-api/http
INSTANCE_NAME=convexdb
DISABLE_METRICS_ENDPOINT=true by default
PG_CA_FILE=/convex/certs/ca.crt
POSTGRES_URL=from the convexdb-postgres Secret
```

The dashboard pod is a separate browser-facing web surface. The stack sets:

```text
NEXT_PUBLIC_DEPLOYMENT_URL=https://convexdb-api
```

That means the dashboard is not self-contained. A browser using the dashboard
must also be able to reach the API URL. If the dashboard page loads but actions
inside it fail, inspect the backend and the API ingress before assuming the
dashboard Deployment is the broken part.

The stack does not define application Convex functions, schema files, client app
code, or a deployment pipeline for those app assets. This project provides the
backend and dashboard infrastructure. The app-specific workflow belongs to the
owning app repo or service, and should point at this stack's exported API URL.

## Backend, Dashboard, And Admin Key

The backend and dashboard are intentionally different surfaces.

The backend is the runtime API. Use `api_url` when configuring a client, a
developer tool, or an app deployment process that needs to talk to this Convex
deployment.

The dashboard is for humans. Use `dashboard_url` for inspection and development
work. The stack only sets the dashboard's deployment URL. It does not create a
dashboard-specific authentication layer in Kubernetes, and it does not store an
admin key in a Kubernetes Secret. Access to the dashboard and API is primarily
through the private Tailscale ingress path configured here. If Convex itself
prompts for additional credentials, use the Convex admin-key workflow rather
than inventing a repo-local secret.

The admin key is generated on demand from the backend pod. Pulumi exports the
command to run:

```bash
cd pulumi/data/databases/convexdb

pulumi stack output --stack mx admin_key_command
```

The output is a `kubectl exec` command shaped like:

```text
kubectl exec -n <namespace> deploy/<backend-deployment> -- /convex/generate_admin_key.sh
```

Run the printed command only in a trusted terminal. The result is sensitive
credential material. Do not paste it into this docs site, chat, issues, commit
messages, PR text, shell snippets, or application source code.

Store the generated key in the secret system used by the app or operator that
needs it. If an app workflow uses a local environment variable, set it in that
local session. If a Kubernetes workload needs it, put it in that workload's
owning Pulumi stack or secret manager. This ConvexDB stack intentionally does
not turn the generated admin key into a repository constant.

Be precise about rotation. The Pulumi code proves that the image contains a
`/convex/generate_admin_key.sh` script and that the stack exposes a command to
run it. The code does not describe the script's full rotation semantics. Before
treating a newly generated key as a rotation event for production consumers,
verify the behavior against the running backend and update every consumer that
depends on the old key.

## PostgreSQL State

ConvexDB depends on the shared PostgreSQL stack, but it owns its own PostgreSQL
role and database.

There are two PostgreSQL paths in the code:

```text
Pulumi provider path:
  local Pulumi process -> PostgreSQL provider -> PostgreSQL admin connection

Backend runtime path:
  convexdb-backend pod -> POSTGRES_URL Secret -> PostgreSQL read-write service
```

The Pulumi provider runs while previewing or applying the stack. It uses
connection material exported by the PostgreSQL stack, choosing the PostgreSQL
Tailscale hostname when available and falling back to the exported host. That is
how Pulumi creates the Convex role and database.

The backend pod runs inside Kubernetes. Its `POSTGRES_URL` is built from the
Convex role name, the generated role password, the PostgreSQL service host, and
the PostgreSQL port. The `mx` config currently gets the service host from ESC as
the PostgreSQL read-write service FQDN. If that value is not configured, the code
falls back to the PostgreSQL stack's `rw_service_fqdn` output.

This difference is intentional. Local Pulumi needs a path from the operator
machine to PostgreSQL. The backend pod should use in-cluster service DNS.

The backend also mounts PostgreSQL CA material:

```text
Secret:       convexdb-postgres-ca
Key:          ca.crt
Mount path:   /convex/certs/ca.crt
Env var:      PG_CA_FILE=/convex/certs/ca.crt
```

The source sets `PG_CA_FILE`, but the generated `POSTGRES_URL` does not add a
query string such as `sslmode=...`. The separate `postgres_sslmode` config value
is used by the Pulumi PostgreSQL provider, not by the backend URL builder. If
you are debugging certificate or TLS behavior, check both the Convex backend
logs and the PostgreSQL stack's current connection expectations instead of
assuming this page can infer the runtime library's exact behavior.

Do not write directly to the Convex PostgreSQL database as an ordinary client
workflow. Direct SQL inspection can be useful during diagnosis, but Convex owns
the schema and invariants. Manual changes to backing tables can make the API and
dashboard fail in ways Pulumi cannot preview.

## PVC State

The backend mounts one persistent volume:

```text
PVC:          convexdb-storage
Mount path:   /convex/data
Access mode:  ReadWriteOnce
Class:        local-path by default
Size:         10Gi by default
```

That PVC is part of Convex state. PostgreSQL is not the whole system.

The backend Deployment uses one replica and a `Recreate` strategy, which matches
the single-writer shape of a `ReadWriteOnce` local-path volume. Do not raise the
replica count or change the rollout strategy without first proving that the
backend image and storage layer support that change.

Storage edits need extra care:

- Increasing `storage_size` may be a normal PVC expansion if the storage class
  supports it, but the preview and cluster events are the source of truth.
- Changing `storage_class_name` can force a new storage path instead of moving
  existing bytes.
- Renaming the PVC or namespace changes the identity of the data volume from
  Kubernetes' point of view.
- Deleting the PVC is a data reset unless you have a restore plan.

If Convex behaves strangely after a pod replacement, inspect both PostgreSQL and
the PVC. A pod can be freshly created and still be attached to old, missing, or
incompatible local data.

## Access Paths

Read the stack outputs first:

```bash
cd pulumi/data/databases/convexdb

pulumi stack output --stack mx namespace
pulumi stack output --stack mx api_url
pulumi stack output --stack mx dashboard_url
pulumi stack output --stack mx backend_service
pulumi stack output --stack mx dashboard_service
pulumi stack output --stack mx pvc
pulumi stack output --stack mx postgres_db_name
pulumi stack output --stack mx postgres_db_user
```

Use `api_url` for clients and tools. Use `dashboard_url` in a browser. Both are
private Tailscale ingress URLs in the current stack shape.

The backend Service exposes two named ports:

```text
convexdb-backend:3210  api
convexdb-backend:3211  site
```

The API ingress routes to the backend Service's `api` port. The code also sets
`CONVEX_SITE_ORIGIN` to `<api_url>/http`. There is no separate Tailscale ingress
for backend port `3211` in this stack.

The dashboard Service exposes:

```text
convexdb-dashboard:6791  http
```

The dashboard ingress routes to that Service. Because the dashboard's public
deployment URL points at `api_url`, a dashboard user needs working access to both
the dashboard host and the API host.

For in-cluster diagnosis, you can bypass ingress and look at Services and
endpoints:

```bash
NS="$(pulumi stack output --stack mx namespace)"

kubectl get svc,endpoints,ingress -n "$NS"
kubectl describe svc -n "$NS" "$(pulumi stack output --stack mx backend_service)"
kubectl describe svc -n "$NS" "$(pulumi stack output --stack mx dashboard_service)"
```

For local diagnosis when Tailscale ingress is suspect, a temporary port-forward
can separate "the pod/service works" from "the ingress path works":

```bash
NS="$(pulumi stack output --stack mx namespace)"

kubectl port-forward -n "$NS" svc/convexdb-backend 3210:3210
kubectl port-forward -n "$NS" svc/convexdb-dashboard 6791:6791
```

Run one port-forward per terminal. Stop it when done. This is only a diagnostic
path; it is not the service contract for applications.

## App Workflow

For an app or developer workflow, start from the exported API URL and the admin
key command. Do not derive hostnames by memory.

```bash
cd pulumi/data/databases/convexdb

pulumi stack output --stack mx api_url
pulumi stack output --stack mx dashboard_url
pulumi stack output --stack mx admin_key_command
```

A typical app workflow has four parts:

1. Configure the app or Convex tooling with the exported `api_url`.
2. Generate an admin key only when the tool or deployment process needs one.
3. Store that key outside the repo, in the owning app's secret path.
4. Validate the result through the API and dashboard.

This infrastructure project does not say which Convex CLI command, framework
integration, or app deployment command a specific application uses. Keep those
details in the app's own repo or runbook. This page's responsibility is the
backend contract: where the API is, where the dashboard is, where the durable
state lives, and which changes are risky.

If you need a Kubernetes workload to consume Convex, prefer wiring it through
that workload's Pulumi project:

- pass the Convex API URL as explicit config or a StackReference output,
- create the workload's own Secret for any admin key or app credential it needs,
- restart or reconcile the workload through its owning stack,
- avoid reading the ConvexDB namespace Secret directly from unrelated stacks.

That keeps ownership clear. The ConvexDB stack owns Convex infrastructure. App
stacks own their own runtime credentials and rollout behavior.

## Debugging From First Principles

Start by deciding which layer is failing:

```text
browser or client access   Tailscale ingress, DNS, dashboard/API URL
dashboard surface          dashboard pod, dashboard env, browser access to API
backend API                backend pod, probes, logs, POSTGRES_URL, PVC
PostgreSQL backing store   shared PostgreSQL stack, Convex role/database, CA
local backend state        convexdb-storage PVC and mount events
admin workflow             admin key generation and consumer secret wiring
```

Then read the actual objects:

```bash
cd pulumi/data/databases/convexdb
NS="$(pulumi stack output --stack mx namespace)"

kubectl get pods,deploy,svc,endpoints,ingress,pvc,secrets -n "$NS"
kubectl logs -n "$NS" deploy/convexdb-backend --tail=200
kubectl logs -n "$NS" deploy/convexdb-dashboard --tail=200
kubectl get events -n "$NS" --sort-by=.lastTimestamp
```

The backend probes call `/version` on port `3210`. If the backend is not ready,
debug the backend before the dashboard:

```bash
kubectl describe pod -n "$NS" -l app=convexdb,component=backend
kubectl describe pvc -n "$NS" convexdb-storage
kubectl get secret -n "$NS" convexdb-postgres convexdb-postgres-ca
```

Do not print Secret data while collecting a status report. It is usually enough
to prove that the expected Secret and key names exist, then inspect logs for the
specific failure class.

If the dashboard page loads but actions fail:

- Confirm the browser can reach both `dashboard_url` and `api_url`.
- Check `kubectl logs -n "$NS" deploy/convexdb-dashboard --tail=200`.
- Check backend logs next, because dashboard actions depend on the backend API.
- Verify the dashboard Deployment has `NEXT_PUBLIC_DEPLOYMENT_URL` set to the
  exported API URL.

If clients cannot reach the API:

- Check the `convexdb-api` ingress and its Tailscale status.
- Check `convexdb-backend` Service endpoints.
- Check the backend pod readiness and `/version` probe failures.
- Compare the client configuration with `pulumi stack output --stack mx api_url`.

If the backend cannot reach PostgreSQL:

- Check the PostgreSQL stack is healthy before editing Convex.
- Check that the Convex role and database are still managed by this stack.
- Check that `convexdb-postgres` exists and contains the expected key name.
- Check that `convexdb-postgres-ca` exists and is mounted.
- Remember that the local Pulumi provider path and in-cluster backend path are
  different.

If admin-key use fails:

- Regenerate or inspect the command from the Pulumi output.
- Confirm you are running the command against the current backend Deployment.
- Confirm the consuming app or tool is using the current key from its own secret
  path.
- Avoid solving this by adding the key to the ConvexDB docs page, stack config,
  or app source code.

## Common Failure Patterns

Dashboard loads, but the UI cannot talk to the deployment. The dashboard pod can
serve static UI while the browser cannot reach the API URL or while the backend
is failing. Check `api_url`, the API ingress, and backend logs.

Backend readiness fails. The readiness, liveness, and startup probes all call
`/version` on port `3210`. Check backend logs, PostgreSQL connection material,
PVC mount events, and the CA Secret.

Clients fail, but pods look ready. Compare the client URL with the exported
`api_url`. Then inspect the Tailscale ingress and backend Service endpoints.

Pulumi preview or apply cannot create the PostgreSQL database or role. That is
the local PostgreSQL provider path, not the backend pod path. Check the shared
PostgreSQL stack outputs and local access to its Tailscale/provider host.

Backend logs show database or certificate errors. The stack mounts CA material
and sets `PG_CA_FILE`, but the backend's exact connection behavior comes from
the Convex image. Check the runtime log message, the PostgreSQL stack's current
TLS expectations, and the `POSTGRES_URL` Secret shape without exposing the
Secret value.

After a restore, Convex starts but behaves inconsistently. PostgreSQL and the
PVC may have been restored from different points in time. Treat the two as a
coordinated state set.

Image changes cause dashboard/backend mismatch. The backend and dashboard images
are separately configurable. Move them together unless you have checked
compatibility.

## Backup And Restore Thinking

This stack does not define a complete backup system by itself. It defines state
that must be backed up or otherwise recoverable.

ConvexDB has at least two durable state surfaces:

```text
PostgreSQL database and role:  created through the PostgreSQL provider
Backend PVC:                   convexdb-storage mounted at /convex/data
```

Pulumi state and Kubernetes Secrets also matter because they contain or derive
credential relationships. A PostgreSQL database restore that brings back an old
role password can disagree with the generated password stored in Pulumi state
and the Kubernetes `POSTGRES_URL` Secret. A PVC restore without the matching
PostgreSQL database point can produce a backend that starts but cannot interpret
its state correctly.

Before a risky change, write down the restore boundary:

- Which PostgreSQL backup, dump, or snapshot covers the `convexdb` database?
- Which volume backup or storage-provider mechanism covers `convexdb-storage`?
- Are those two artifacts from the same maintenance window?
- Is the backend stopped, quiet, or otherwise protected from writes during the
  backup boundary?
- Which admin key or app credentials will consumers use after restore?
- Which stack owns each consumer that needs to be restarted or reconfigured?

For PostgreSQL, the answer may involve CloudNativePG backup facilities, a volume
snapshot, or a logical dump, depending on what the shared PostgreSQL stack is
using at the time. For the PVC, the answer depends on the storage class and the
cluster's volume backup support. The default `local-path` class is a warning to
think carefully: local-path storage is convenient, but a restore plan must know
which node and filesystem path actually hold the data.

Do not treat "revert the Git commit" as a restore plan for ConvexDB. Git can
restore Pulumi code. It cannot automatically restore PostgreSQL rows, a PVC,
generated credentials, or application data that changed while the new version
was running.

For a real restore, prefer a maintenance window where writes are controlled,
restore PostgreSQL and the PVC as one coordinated unit, then verify in this
order:

1. PostgreSQL stack and Convex database are present.
2. `convexdb-postgres` and `convexdb-postgres-ca` have the expected shape.
3. `convexdb-storage` is bound and mounted by the backend pod.
4. Backend `/version` readiness succeeds.
5. API URL responds through the Tailscale ingress.
6. Dashboard can talk to the API.
7. App workflows using the admin key and API URL still work.

Keep secret values out of the restore notes. Record where the secret lives and
which command retrieves or recreates it, not the value itself.

## Changing The Stack Safely

For documentation-only edits to this page, a Pulumi preview is usually not
necessary. For Pulumi code or stack configuration changes, preview before any
apply decision:

```bash
just sync pulumi/data/databases/convexdb
just check-python
just lint
git diff --check -- pulumi/data/databases/convexdb docs/stacks/data/databases/convexdb.md
just preview pulumi/data/databases/convexdb stack=mx
```

Do not run `pulumi up`, `pulumi destroy`, or `just up` from this guide unless an
operator has explicitly chosen to apply or destroy infrastructure.

Risky changes include:

- `namespace`: changes object identity and where Secrets, Services, ingresses,
  and the PVC live.
- `storage_class_name`: can create a new storage path rather than moving data.
- `storage_size`: may require PVC expansion support from the storage class.
- `instance_name`: affects the backend's `INSTANCE_NAME` and the default
  database/user names when those are not explicitly configured.
- `postgres_stack_ref`: changes the producer stack for PostgreSQL coordinates
  and credentials.
- `postgres_service_host`: changes the backend pod's database network path.
- `postgres_db_name` or `postgres_db_user`: changes the database identity. That
  is not a data migration by itself.
- `postgres_ca_cert`: changes mounted CA material for the backend.
- `backend_image`, `dashboard_image`, or `image_tag`: can change runtime
  behavior, schema expectations, and dashboard/API compatibility.
- `api_ingress_host` or `dashboard_ingress_host`: changes user-facing URLs and
  the origins Convex advertises to clients and the dashboard.
- `disable_metrics_endpoint`: changes backend exposure behavior. The stack does
  not create a ServiceMonitor or monitoring integration for Convex metrics.
- `load_monaco_internally`: only adds
  `NEXT_PUBLIC_LOAD_MONACO_INTERNALLY=true` to the dashboard environment. The
  stack does not otherwise manage dashboard editor assets.

When a change touches PostgreSQL identity, credentials, CA material, service
hostnames, images, or storage, do more than run the Convex preview. Check the
shared PostgreSQL contract and the app workflows that use Convex. The backend
may preview cleanly while a client still has an old URL or admin key.

Use Pulumi aliases or explicit migration steps for resource renames. A rename
that looks cosmetic in code can become a replacement in the cluster, and
replacements on stateful resources need a restore or migration plan.

## Operator Checklist

For routine inspection:

```bash
cd pulumi/data/databases/convexdb
NS="$(pulumi stack output --stack mx namespace)"

pulumi stack output --stack mx api_url
pulumi stack output --stack mx dashboard_url
kubectl get pods,svc,endpoints,ingress,pvc -n "$NS"
kubectl logs -n "$NS" deploy/convexdb-backend --tail=100
```

For a client or app workflow:

```bash
cd pulumi/data/databases/convexdb

pulumi stack output --stack mx api_url
pulumi stack output --stack mx admin_key_command
```

Generate the admin key only when needed, keep it out of the repo, and store it
where the consuming app expects secrets to live.

For a risky infrastructure change:

```text
1. Identify whether PostgreSQL, the PVC, URLs, images, or admin credentials are affected.
2. Verify the backup or rollback boundary for both PostgreSQL and convexdb-storage.
3. Run the cheap repo checks.
4. Run a targeted ConvexDB preview.
5. Preview affected producer or consumer stacks when contracts changed.
6. Apply only in an explicit change window.
7. Verify backend, API ingress, dashboard, and app workflow after apply.
```

The operating rule: use Convex through its API and dashboard, preserve the
PostgreSQL plus PVC state pair, handle the admin key as a credential, and preview
changes that alter any part of that contract.
