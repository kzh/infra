# n8n

Source: `pulumi/data/workflow/n8n`

n8n is the visual automation system in this repo. Think of it as glue for
small integration paths: a webhook arrives, a schedule fires, a manual button is
clicked, a few nodes call external systems, and n8n records what happened. It is
useful when seeing and editing the flow is more valuable than creating a full
application service.

It is not the default home for every kind of workflow. Use Airflow or Dagster
when the important shape is a scheduled data pipeline, asset graph, backfill, or
task dependency model. Use Temporal when the important shape is durable
application state with long waits, signals, retries, and worker code. Use n8n
when the important shape is visual integration wiring: webhooks, notifications,
API stitching, lightweight internal automations, and small operational tools.

The biggest operational rule is simple: the UI makes workflows easy to change,
but those workflows are still production state once something depends on them.
Treat important n8n workflows like code-adjacent infrastructure. Name them
clearly, know which credentials they use, test the real trigger path, export or
document the important ones, and preserve the database and PVC when changing the
stack.

## What This Pulumi Stack Builds

This stack is a small raw Kubernetes deployment. It does not use the n8n Helm
chart. The Pulumi program declares a Namespace, PersistentVolumeClaim, Secret,
Deployment, Service, and Tailscale Ingress directly with `pulumi-kubernetes`.

The current `mx` stack config is:

```yaml
config:
  n8n:namespace: n8n
  n8n:image: n8nio/n8n:2.20.9
  n8n:postgres_stack: kzh/postgresql/mx
```

The Pulumi program also has a `n8n:db_name` config option. It is not set in the
current stack file, so the code defaults the application database name to
`n8n`.

The important Kubernetes shape is:

```text
Namespace:           n8n
Labels:              app=n8n
Image:               n8nio/n8n:2.20.9
Replicas:            1
Deployment:          n8n
Init container:      busybox:1.37.0
PVC:                 n8n, 4Gi, ReadWriteOnce
PVC mount:           /home/node/.n8n in the n8n container
Service:             n8n, ClusterIP, port 5678 -> targetPort 5678
Ingress:             n8n, ingressClassName=tailscale
Ingress host:        n8n
Ingress TLS hosts:   n8n
In-pod protocol:     http
In-pod n8n port:     5678
```

The Deployment has one main container and one init container. The init container
mounts the PVC at `/data` and runs `chown 1000:1000 /data`, so the n8n process
can write to the same volume when it is mounted at `/home/node/.n8n`. The main
container starts with:

```text
sleep 5; n8n start
```

There are no custom liveness probes, readiness probes, resource requests, or
resource limits in this project today. A `Ready` pod mostly proves that the
container is running. It does not prove that a workflow can reach its
destination, that webhook URLs are right, or that execution history is healthy.

The stack also has no Pulumi exports of its own. To find the live route or
namespace, read stack config and Kubernetes objects rather than expecting
`pulumi stack output` from the n8n project.

## How The Database Wiring Works

n8n uses PostgreSQL in this repo. The n8n stack reads the shared PostgreSQL
producer through a StackReference:

```python
pgref = pulumi.StackReference(config.require("postgres_stack"))
pg_host = pgref.require_output("rw_service_fqdn")
pg_port = pgref.require_output("port")
pg_user = pgref.require_output("username")
pg_password = pgref.require_output("password")
```

For `mx`, that StackReference points at `kzh/postgresql/mx`.

The PostgreSQL project currently includes `n8n` in
`postgresql:app_databases`, so the shared PostgreSQL stack creates an
application database named `n8n`. The n8n project then passes the producer
outputs into the Deployment as n8n database environment variables:

```text
DB_TYPE=postgresdb
DB_POSTGRESDB_HOST=<postgres rw service FQDN from the PostgreSQL stack>
DB_POSTGRESDB_PORT=<postgres port from the PostgreSQL stack>
DB_POSTGRESDB_DATABASE=n8n
DB_POSTGRESDB_USER=<postgres username from the PostgreSQL stack>
DB_POSTGRESDB_PASSWORD=<from Kubernetes Secret n8n-db-credentials>
```

The password is not written directly into the Deployment spec. Pulumi creates a
Kubernetes Secret named `n8n-db-credentials` in the n8n namespace and stores the
database password under the key `DB_POSTGRESDB_PASSWORD`. The Deployment reads
that key with `valueFrom.secretKeyRef`.

Do not print or copy the secret value into docs, tickets, screenshots, or commit
messages. For debugging, it is usually enough to verify that the Secret exists,
that the Deployment references it, and that n8n logs do or do not show database
connection errors.

## The Visual Workflow Mental Model

An n8n workflow is a graph. A trigger starts the graph, nodes receive and emit
items, expressions map fields between nodes, credentials let nodes authenticate
to external systems, and each execution records the path that actually ran.

The trigger is the entry point. It might be a webhook, a schedule, a manual
editor run, or a service-specific trigger. The trigger answers "why did this
workflow start?" If you cannot explain the trigger in one sentence, the workflow
will be hard to operate later.

Nodes are the steps. A node may transform JSON, branch on a condition, call an
HTTP API, post a message, create a ticket, run code, or talk to another system
through an integration. A node should have a narrow job and a name that makes
its side effect obvious. `POST incident note` is more useful than `HTTP Request`
when you are reading execution history under pressure.

Edges show data flow and control flow. They are not just decoration. If a node
has two possible paths, name the branch by the business condition it represents.
If a node retries or can run twice, decide whether the external side effect is
safe to repeat.

Items are the data moving through the graph. A single webhook request can
produce one item, many items, or no useful item depending on the trigger and
transforms. When debugging a failed execution, inspect the input and output at
the node where the data first becomes wrong, not only the node where the final
error appears.

Expressions are code-adjacent. They can be small and useful, but complex
expressions become hidden application logic. If a workflow needs large
conditionals, complicated data shaping, or a lot of custom JavaScript, consider
whether that logic belongs in a small service or a repo-owned script instead.

## Webhooks And Executions

Webhook workflows need extra care because they are part of another system's
runtime path. A webhook is not just an n8n feature; it is an API surface. The
caller needs a reachable URL, the request shape must match what the workflow
expects, retries must be safe, and the response must mean something useful to
the caller.

For webhook workflows, distinguish the editor test path from the active
production path. A test webhook can prove the node logic while you are editing.
The production webhook proves that an active workflow can receive traffic
through the real route. Both are useful, but they answer different questions.

This stack exposes n8n through a Tailscale Ingress with host `n8n` and routes
HTTP traffic to the in-cluster Service on port `5678`. From a tailnet-connected
client that can resolve the Tailscale host, the UI route is:

```text
https://n8n
```

Inside the pod, n8n is configured with `N8N_PROTOCOL=http` and `N8N_PORT=5678`.
The stack does not currently set `N8N_HOST`, `N8N_EDITOR_BASE_URL`, or
`WEBHOOK_URL`. That means generated editor and webhook URLs are left to n8n's
default or request-derived behavior. If the UI works but generated webhook URLs
point at the wrong host or scheme, fix that in the Pulumi Deployment
environment and preview the change. Do not hand-edit the live Deployment as the
durable fix.

When testing a webhook, prove each layer in order:

```text
caller can resolve and reach the Tailscale host
Ingress routes host n8n to Service n8n on port 5678
Service has ready endpoints from pods labeled app=n8n
n8n receives the request on the expected webhook path
the workflow parses the payload correctly
the destination accepts the side effect
execution history records the result
retry behavior is acceptable
```

Executions are the operational history. They tell you which trigger fired, which
nodes ran, which node failed, what data moved through the graph, and what the
external system returned. They can also contain sensitive request bodies,
headers, payload fields, endpoint names, and fragments of external responses.
Treat execution screenshots and exports as sensitive unless you have inspected
them.

The current Pulumi stack does not configure execution pruning, queue mode,
external binary storage, or separate workers. If execution history grows too
large, solve that as an n8n configuration and retention problem. Increasing the
database or PVC size may buy time, but it does not define the retention policy.

## Credentials

n8n credentials are application state. A credential is usually a named token,
OAuth connection, password, API key, or service account that a node uses to talk
to another system. The workflow definition normally references a credential; it
should not embed the raw secret.

Use n8n's credential system for workflow-level credentials, and keep the
credential names human-readable. A future operator should be able to tell which
external account a workflow is using without seeing the secret value. Good
credential names describe the system, purpose, and scope. They do not include
the secret itself.

There are two separate credential paths in this stack:

```text
Infrastructure credential    PostgreSQL password from the PostgreSQL stack,
                             stored in Kubernetes Secret n8n-db-credentials.

Workflow credentials         Credentials created and managed inside n8n for
                             nodes that call external services.
```

Do not mix those up. The Kubernetes Secret lets n8n connect to its own
database. It is not where arbitrary workflow API tokens should be placed unless
you deliberately extend the Deployment to inject a repo-managed secret.

The n8n database and the `/home/node/.n8n` PVC both matter for credential
recovery. n8n stores workflow metadata and credentials in PostgreSQL, while the
local n8n user directory can contain app config and encryption material. This
repo does not inject a fixed `N8N_ENCRYPTION_KEY` through Pulumi. Because the
PVC is persisted at `/home/node/.n8n`, losing or replacing it can be more than a
cache loss. If you ever introduce an explicit encryption key, manage it as a
Pulumi secret or Kubernetes Secret and plan the migration carefully.

When rotating a workflow credential, update the credential in n8n, run the
workflow path that uses it, and leave a note in the workflow or runbook about
what changed. Do not paste old or new token values into the repo.

## Persistence, Storage, And What Must Be Backed Up

Treat this as a stateful application even though the Kubernetes surface is
small.

The PostgreSQL database is the main application store. It holds n8n state such
as workflow definitions, credentials, users, settings, and execution history.
The database is created by the shared PostgreSQL stack because `n8n` appears in
`postgresql:app_databases`. The n8n stack connects to the PostgreSQL read-write
service FQDN exported by that producer stack.

The PVC is the local n8n user directory. This stack creates a `ReadWriteOnce`
claim named `n8n`, requests `4Gi`, and mounts it at `/home/node/.n8n`. Local app
config, encryption-related state, and file-backed artifacts may live there
depending on how n8n is configured and how workflows process data.

There is no n8n-specific backup CronJob, backup controller resource, or export
job in this Pulumi project. A real backup plan needs to cover at least:

```text
PostgreSQL database named n8n
PVC named n8n in the n8n namespace
sanitized workflow exports or written runbooks for important UI-authored flows
credential recovery or rotation notes that do not expose secret values
```

Workflow exports are useful, but they are not a full backup of the service.
They help you review and recreate workflow structure. They do not replace the
database, execution history, credential state, or local app config.

Before any risky change, decide what recovery means. If the goal is "restore
the UI and all workflows exactly as they were," preserve PostgreSQL and the PVC.
If the goal is "recreate the important automations cleanly," sanitized workflow
exports plus credential rotation notes may be enough, but that is a different
recovery target.

## Access And Routing

The route is private to the Tailscale ingress path. The Ingress has
`ingressClassName: tailscale`, host `n8n`, TLS for host `n8n`, and a single `/`
Prefix route to Service `n8n` on port `5678`.

The Service is `ClusterIP`, not `LoadBalancer` or `NodePort`. The in-cluster
path is:

```text
Tailscale Ingress -> Service n8n:5678 -> Pod port 5678 -> n8n process
```

Start with this inspection from the project directory:

```bash
cd pulumi/data/workflow/n8n

NS="$(pulumi config get n8n:namespace --stack mx)"
kubectl get deploy,pods,svc,endpoints,ingress,pvc -n "$NS"
kubectl describe ingress -n "$NS" n8n
```

If the browser returns a 502 or connection error, check Service endpoints before
editing ingress:

```bash
kubectl get endpoints -n "$NS" n8n -o wide
kubectl get pods -n "$NS" -l app=n8n -o wide
kubectl logs -n "$NS" deploy/n8n -c n8n --tail=200
```

An Ingress can be correct while the Service has no endpoints. A Service can have
endpoints while n8n cannot reach PostgreSQL. The UI can load while a webhook URL
is wrong. Keep those layers separate while debugging.

## Operating The UI

When creating a workflow, start by naming the operational contract, not the
implementation. A name like `github issue webhook to incident note` is more
useful than `test automation`. The first tells you what starts it and what it
changes.

For every workflow that matters, keep these facts discoverable:

```text
what starts it
whether it is active
what external systems it calls
which n8n credentials it uses
what side effects it creates
how duplicate delivery or retries behave
where to inspect failures
how to replay or safely stop it
whether a sanitized export exists
```

Manual workflows are good for operator-triggered tasks and one-off internal
tools. They are easiest to reason about because a human chooses when they run.
The risk is that the input assumptions live only in the operator's head. Put
required input shape and safety notes in the workflow description or a runbook.

Scheduled workflows are fine for lightweight checks and notifications. If the
schedule becomes a data pipeline with dependencies, backfills, partitions, SLAs,
or long-running compute, move the work to Airflow or Dagster and keep n8n as a
notification or integration edge if needed.

Webhook workflows are the most API-like. They need stable URLs, caller
ownership, retry behavior, and an answer for duplicate requests. If the caller
will retry on timeout, make sure the workflow's side effects can tolerate that.
Use idempotency keys or explicit duplicate checks when the destination supports
them.

## Exports And Change Review

n8n workflows can be exported from the UI, and the n8n CLI can export workflows
and credentials from inside the running container. Treat exports as sensitive
until inspected. They can reveal endpoint names, request shapes, internal
workflow logic, credential names, and sometimes credential payloads or encrypted
credential material.

For command-line exports, check the CLI help inside the exact running image
before relying on flags:

```bash
kubectl exec -n "$NS" deploy/n8n -c n8n -- n8n export:workflow --help
kubectl exec -n "$NS" deploy/n8n -c n8n -- n8n export:credentials --help
```

For important workflows, keep a sanitized export or a prose runbook in version
control. Sanitized means no tokens, no private payload samples, no credentials,
and no unnecessary private endpoint details. The goal is to preserve the
workflow's intent, trigger contract, and node structure without leaking the
systems it touches.

When reviewing a UI-authored workflow change, do not only look at whether the
canvas is tidy. Ask operational questions:

```text
What event starts this?
What happens if the same event arrives twice?
Which node is the first irreversible side effect?
What data is stored in execution history?
Which credential scopes are required?
What should an operator do if this fails halfway?
Is this still visual glue, or has it become application code?
```

## Debugging By Layer

Most n8n failures are easier to solve when you name the failing layer.

Configuration layer:

```bash
cd pulumi/data/workflow/n8n

pulumi config get n8n:namespace --stack mx
pulumi config get n8n:image --stack mx
pulumi config get n8n:postgres_stack --stack mx
```

If `n8n:postgres_stack` changes, preview n8n and think about database identity.
Pointing at a different PostgreSQL stack can look like a fresh n8n install if
the expected database contents are not there.

PostgreSQL producer layer:

```bash
cd pulumi/data/databases/postgres

pulumi stack output --stack mx rw_service_fqdn
pulumi stack output --stack mx port
```

Avoid `--show-secrets` unless you intentionally need to retrieve a secret into
your terminal. The n8n stack only needs the host, port, username, and password
outputs to be valid; most investigations do not require printing the password.

Kubernetes runtime layer:

```bash
cd pulumi/data/workflow/n8n

NS="$(pulumi config get n8n:namespace --stack mx)"
kubectl get deploy,pods,svc,endpoints,ingress,pvc -n "$NS"
kubectl describe deploy -n "$NS" n8n
kubectl describe pvc -n "$NS" n8n
kubectl logs -n "$NS" deploy/n8n -c init --tail=100
kubectl logs -n "$NS" deploy/n8n -c n8n --tail=200
```

Startup failures usually show up as database connection errors, migration
errors, filesystem permission errors, or encryption/config errors. If the init
container fails, inspect the PVC mount and ownership path. If the main container
starts but n8n cannot connect to PostgreSQL, inspect the StackReference config,
the Secret reference, and the PostgreSQL service.

Routing layer:

```bash
kubectl describe ingress -n "$NS" n8n
kubectl get service -n "$NS" n8n -o wide
kubectl get endpoints -n "$NS" n8n -o wide
```

The Service selector is `app=n8n`, matching the pod template labels. If
endpoints are empty, look at pod readiness, labels, and whether the Deployment
created pods in the namespace you expected.

Workflow layer:

```text
open the execution
find the first node with wrong input or output
inspect the external service response
check whether the failing node uses the expected credential
decide whether retrying would repeat an irreversible side effect
```

Do not assume a failed destination call means n8n is broken. It may be an
expired external token, a caller sending a different payload shape, a rate
limit, a changed API response, or a workflow expression that no longer matches
the data.

## Safe Infrastructure Changes

Use the repo workflow for changes. Do not make durable fixes by editing live
Kubernetes objects. If a change matters, put it in
`pulumi/data/workflow/n8n/__main__.py` or `Pulumi.mx.yaml`, then preview it.

For ordinary n8n stack edits:

```bash
just sync pulumi/data/workflow/n8n
just check-python
just lint
git diff --check
just preview pulumi/data/workflow/n8n stack=mx
```

Changing the image is an application upgrade. n8n upgrades can run database
migrations, change CLI behavior, alter node behavior, or change credential and
execution storage expectations. Read the release notes, preserve PostgreSQL and
the PVC, and test an existing workflow after the rollout.

Changing the namespace is a migration. It changes where the Namespace-scoped
PVC, Secret, Deployment, Service, and Ingress live. Unless you intentionally
move state, a new namespace can behave like a new install.

Changing `n8n:db_name` is a migration. The current default is `n8n`, and the
PostgreSQL producer currently creates an app database named `n8n`. If you change
the n8n database name without creating and migrating the matching database,
n8n will not see the old workflows.

Changing storage is a migration. Increasing a PVC request is different from
renaming or replacing the PVC. The claim named `n8n` is part of the stateful
identity of this deployment.

Changing replicas is not a safe scaling shortcut. The current stack is
single-replica and does not configure n8n queue mode, Redis, separate workers,
or shared external binary storage. Design the scaling model before increasing
replicas.

Changing routing should preserve the external webhook contract. If active
workflow callers use a URL, moving the host or path can break them even while
the UI still opens. For webhook-heavy changes, test from the real caller path,
not only from the editor.

After an apply, the minimum useful verification is:

```text
the pod starts
the Service has endpoints
the Tailscale route opens the UI
an existing workflow loads
a tiny manual execution succeeds
at least one real webhook path works if webhooks are in use
execution history records the expected result
```
