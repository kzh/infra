# Hermes

Source: `pulumi/apps/hermes`

Hermes is the repo-managed home for a persistent agent runtime in the cluster.
It is useful to think of it less like a stateless web app and more like a small
remote workstation with a stable home directory. The pod can be restarted, the
image can be rebuilt, and the node can move the workload, but the runtime is
meant to keep its working state through the `hermes-data` PVC.

That persistence is the main design constraint. Hermes has a gateway process,
a dashboard process when enabled, a browser automation sidecar when enabled, a
Codex home, provider configuration, login state, browser profile data, and a
SQLite runtime database. Those pieces only make sense if they are treated as
one long-lived environment instead of disposable container scratch space.

## What The Stack Creates

The Pulumi program creates a namespace, a persistent volume claim, and a
single-replica Kubernetes Deployment named `hermes`. The Deployment always runs
the `gateway` container. It also runs a `dashboard` container when dashboard
support is enabled and a `camofox` container when browser automation support is
enabled.

The important defaults in this repo are:

```text
Pulumi project:       pulumi/apps/hermes
Stack:                mx
Namespace:            hermes
Deployment:           hermes
PVC:                  hermes-data
Hermes home:          /opt/data
Codex home:           /opt/data/.codex
Gateway port:         8642
Dashboard port:       9119
Camofox port:         9377
Runtime UID/GID:      10000/10000
Replica count:        1
Deployment strategy:  Recreate
```

The one-replica `Recreate` strategy is intentional. The PVC is
`ReadWriteOnce`, and Hermes is stateful enough that two live pods sharing or
racing over the same runtime state would be unsafe. A Deployment update should
be understood as a short runtime restart, not as a rolling stateless web
release.

The gateway and dashboard containers mount the PVC at `/opt/data`. The Camofox
browser sidecar mounts the `camofox` subpath of the same PVC at `/data`, so
browser profiles, cookies, traces, and related browser state survive restarts
alongside Hermes and Codex state.

The pod runs without an auto-mounted Kubernetes service account token. The pod
security context sets `fsGroup` to the Hermes group and uses
`OnRootMismatch`, which keeps normal PVC ownership repair from becoming more
expensive than it needs to be on every restart.

## What Persistent Runtime Means

The persistent runtime is the combination of three things:

1. The repo-managed Kubernetes objects that decide how the pod is launched.
2. The pinned runtime images that decide what software is in the pod.
3. The PVC contents that hold the lived state of the agent environment.

The first two are rebuildable from git and Pulumi. The third is not. The PVC may
contain Hermes configuration, model/provider settings, the Hermes runtime
database, Codex login state, Codex config, browser profiles, cookies, traces,
and other files created by normal agent work. Losing the PVC is closer to
replacing the workstation than to clearing a cache.

The most important paths are:

```text
/opt/data            HOME and HERMES_HOME for gateway/dashboard
/opt/data/.codex     CODEX_HOME for Codex CLI state and config
/opt/data/state.db   Hermes runtime database
/data                Camofox state inside the browser sidecar
```

The stack sets `HOME=/opt/data`, `HERMES_HOME=/opt/data`, and
`CODEX_HOME=/opt/data/.codex` in the Hermes containers. The exported Codex
commands run through `gosu hermes` so the Codex CLI writes into the runtime
user's Codex home instead of root's home.

When debugging, keep a clear line between image state and PVC state. Rebuilding
or replacing the image changes the software. Deleting or replacing the PVC
changes the identity and memory of the runtime.

## How Users Interact With Hermes

Most day-to-day use should go through the stack outputs. They encode the
namespace, Deployment name, target container, runtime user, and expected home
directory layout. Prefer them over hand-written `kubectl exec` commands unless
you are intentionally debugging a lower-level issue.

From the project directory:

```bash
cd pulumi/apps/hermes

pulumi stack output --stack mx model_setup_command
pulumi stack output --stack mx codex_login_command
pulumi stack output --stack mx codex_login_status_command
pulumi stack output --stack mx codex_version_command
pulumi stack output --stack mx provider_test_command
```

Run an exported command by command-substituting the output:

```bash
$(pulumi stack output --stack mx codex_login_status_command)
$(pulumi stack output --stack mx codex_version_command)
$(pulumi stack output --stack mx provider_test_command)
```

Use `codex_login_command` for the Codex device-auth flow. It is interactive and
stores the resulting state under `/opt/data/.codex`. Do not copy tokens into
docs, commits, PR text, or chat. The login status command is the safer way to
confirm whether the runtime is currently authenticated.

Use `model_setup_command` when Hermes provider/model configuration needs to be
created or changed. Use `provider_test_command` after setup or an upgrade to
make sure the runtime can actually complete a small provider-backed request.

The dashboard is the human inspection surface. When enabled, the stack exports:

```bash
pulumi stack output --stack mx dashboard_port_forward_command
```

Run the exported port-forward command when you want a local dashboard session.
If dashboard ingress is enabled, the stack also creates a private Tailscale
Ingress and exports the ingress object and host. In this repo that ingress is
for the private tailnet path, not a general public endpoint.

## Runtime Processes

The `gateway` container is the main service process:

```text
/opt/hermes/.venv/bin/hermes gateway run
```

It uses the shared Hermes home and, when Camofox is enabled, receives:

```text
CAMOFOX_URL=http://127.0.0.1:9377
```

That URL is loopback inside the pod. It is not a cluster Service. It works
because the gateway and browser sidecar share the same pod network namespace.

The `dashboard` container starts Hermes dashboard mode with the configured host
and port. If dashboard ingress is enabled, the dashboard binds to `0.0.0.0` and
the stack passes `--insecure` because the private ingress path is expected to
provide the access boundary. If ingress is disabled, the safer default bind is
`127.0.0.1` and access is through `kubectl port-forward`.

The `camofox` container provides browser automation. It has readiness and
liveness probes on `/health`, a memory-backed `/dev/shm`, and persistent
profile/cookie/trace directories under the PVC. An init container runs before
the main containers to set Hermes browser persistence:

```text
hermes config set browser.camofox.managed_persistence true
```

If a browser task fails, do not assume the Hermes gateway is broken. Check the
Camofox health endpoint and sidecar logs first.

## Routing And Exposure

Hermes currently exposes the dashboard through Kubernetes routing when
dashboard ingress is enabled. The stack creates:

```text
Service:  hermes-dashboard
Ingress:  hermes-dashboard
Class:    tailscale
TLS:      host-backed by the Tailscale ingress path
```

The gateway container declares its port, but this stack does not create a
general-purpose gateway Service. That keeps the agent runtime from becoming an
accidental cluster API surface. If a future change needs gateway routing, make
that a deliberate Pulumi change with an explicit access model.

For dashboard reachability issues, debug the layers separately:

```bash
cd pulumi/apps/hermes
NS="$(pulumi stack output --stack mx namespace)"

kubectl get pod,svc,ingress,pvc -n "$NS"
kubectl describe ingress -n "$NS" hermes-dashboard
kubectl get endpoints -n "$NS" hermes-dashboard
```

If the dashboard is reachable through port-forward but not ingress, the
dashboard process is probably fine and the issue is in Service, Ingress, or
tailnet routing. If neither path works, inspect the dashboard container and pod
events before changing ingress.

## Logs And First Checks

Start by checking the actual workload shape:

```bash
cd pulumi/apps/hermes
NS="$(pulumi stack output --stack mx namespace)"

kubectl get pods -n "$NS" -l app=hermes
kubectl get deploy,pvc -n "$NS"
kubectl describe pod -n "$NS" -l app=hermes
```

Read logs by container. Some containers are optional, so check the pod spec
before assuming every container exists:

```bash
kubectl logs -n "$NS" deploy/hermes -c gateway --tail=200
kubectl logs -n "$NS" deploy/hermes -c dashboard --tail=200
kubectl logs -n "$NS" deploy/hermes -c camofox --tail=200
```

When a container has restarted, look at the previous logs as well:

```bash
kubectl logs -n "$NS" deploy/hermes -c gateway --previous --tail=200
kubectl logs -n "$NS" deploy/hermes -c dashboard --previous --tail=200
kubectl logs -n "$NS" deploy/hermes -c camofox --previous --tail=200
```

If Kubernetes reports a historical pod as `Evicted` or `Failed`, container logs
may be unavailable. In that case, `kubectl describe pod` and node events are
more useful than repeating `kubectl logs`. Separate old failed pods from the
current replacement pod before deciding that Hermes is still broken.

## Inspecting State Safely

The fastest non-destructive state checks are filesystem checks and read-only
status commands:

```bash
kubectl -n "$NS" exec deploy/hermes -c gateway -- ls -lah /opt/data
kubectl -n "$NS" exec deploy/hermes -c gateway -- ls -lah /opt/data/.codex
$(pulumi stack output --stack mx codex_login_status_command)
```

Treat `/opt/data/state.db` as live application state. It is often the best
place to confirm what Hermes believes happened in a conversation or session,
but it should not be edited in place during ordinary debugging. If you need to
inspect it deeply, copy it out or open it read-only, and avoid pasting private
conversation rows or account details into docs or issue text.

Good state questions are specific:

```text
Does the file exist?
Did its size change recently?
Is the runtime writing as the Hermes user?
Does Codex state live under /opt/data/.codex rather than another home?
Did a browser profile persist under the camofox subdirectory?
```

Those questions point to durable causes. A provider error can come from model
configuration or Codex login state. A dashboard error can come from dashboard
binding or ingress. A browser error can come from Camofox health, profile state,
or browser persistence. A permission error can make all of those symptoms look
related even when the application logic is fine.

## Common Operational Patterns

When provider setup is suspect, verify the runtime in this order:

```bash
$(pulumi stack output --stack mx codex_login_status_command)
$(pulumi stack output --stack mx codex_version_command)
$(pulumi stack output --stack mx provider_test_command)
```

If login is missing or expired, run the exported login command and complete the
device flow:

```bash
$(pulumi stack output --stack mx codex_login_command)
```

When browser automation is suspect, check the sidecar before changing Hermes:

```bash
$(pulumi stack output --stack mx camofox_healthcheck_command)
kubectl logs -n "$NS" deploy/hermes -c camofox --tail=200
```

When the dashboard is suspect, compare the local port-forward path with the
ingress path:

```bash
$(pulumi stack output --stack mx dashboard_port_forward_command)
kubectl get svc,ingress,endpoints -n "$NS"
```

When the pod is restarting, inspect events, previous logs, resources, and PVC
mounts together:

```bash
kubectl describe pod -n "$NS" -l app=hermes
kubectl logs -n "$NS" deploy/hermes -c gateway --previous --tail=200
kubectl get pvc -n "$NS"
```

Avoid collapsing every symptom into "Hermes is down." The stack is small, but
it is made of distinct pieces. Identify which process, route, or state path is
failing before editing code.

## Image And Upgrade Discipline

The runtime image is pinned by digest in stack config. The Pulumi program also
records the Hermes source commit and Codex CLI version as pod annotations. That
gives operators a path from a running pod back to the source and build inputs
that produced it.

The local `Justfile` describes the image build inputs:

```text
Hermes source commit:  a91a57fa5a13d516c38b07a141a9ce8a3daabeb0
Codex CLI version:     0.130.0
Build platform:        linux/amd64
Image repo:            ghcr.io/kzh/hermes-agent
Builder default:       docker-buildx with builder mx0
```

A normal runtime upgrade should keep those pieces aligned:

1. Choose the upstream Hermes source commit and Codex CLI version.
2. Update the project `Justfile` build inputs.
3. Build and push the runtime image.
4. Inspect the pushed image and update the Pulumi stack image digest.
5. Update the annotation constants in `__main__.py`.
6. Run the cheap repo checks.
7. Run a targeted preview for the Hermes stack.
8. After an authorized apply, verify the live runtime commands and logs.

The build recipes are project-local:

```bash
cd pulumi/apps/hermes
just push-image
just inspect-image
```

From the repo root, the safe validation path is:

```bash
just sync pulumi/apps/hermes
just check-python
just lint
git diff --check
just preview pulumi/apps/hermes stack=mx
```

Do not treat a tag as the deployment contract. The digest in Pulumi config is
what Kubernetes should run. Do not treat a successful image build as proof that
the runtime is ready; verify Codex version, login status, provider behavior,
Camofox health when enabled, and dashboard routing when changed.

## Safe Changes

The safest changes are the ones that preserve the boundary between repo state,
image state, and PVC state.

Use Pulumi for Kubernetes shape changes: namespace, PVC size, Deployment args,
containers, ports, probes, Service, and Ingress. Use the image build for runtime
software changes. Use Hermes or Codex supported commands for runtime
configuration. Use the PVC only as the state store, not as a place to hide
untracked patches.

Avoid editing software inside the running container as the fix. It can be a
short diagnostic step when everyone understands it will be lost, but it should
not become the durable solution. Startup scripts that rewrite files under
`/opt/hermes` or transport code inside the image make the live behavior hard to
reason about and will surprise the next operator. If Hermes or Codex needs a
behavior change, prefer an upstream fix, a rebuilt image, or a supported config
surface.

Be careful with these changes:

```text
PVC deletion or replacement
  Resets login state, Hermes runtime state, Codex state, and browser state.

Changing UID/GID or fsGroup
  Can turn a healthy runtime into a permissions problem.

Switching Deployment strategy or replica count
  Can make a stateful single-writer runtime unsafe.

Opening dashboard or gateway routes
  Changes the access boundary and should be reviewed as an exposure change.

Changing dashboard bind host
  Determines whether the dashboard is reachable only through port-forward or
  through the pod network for ingress.

Changing image tags without changing digests
  Makes reviews and rollbacks ambiguous.
```

If a preview fails after a code change, classify the failure before editing
more code. It may be missing config, live-state drift, an ingress/controller
issue, provider behavior, or a real program bug. The fix depends on which class
it is.

## Reset And Recovery

Preserve the PVC while debugging ordinary misconfiguration. Restarting the pod,
rebuilding the image, changing model setup, or repairing ingress should not
require throwing away runtime state.

A PVC reset is a last-resort operational decision. It may be appropriate after
a compromise, a deliberately abandoned runtime, or state corruption that cannot
be repaired. It should be planned as a reset of Hermes identity and continuity:
Codex login, provider setup, browser profiles, cookies, traces, and Hermes
conversation/session state may all need to be recreated.

Before any destructive reset, capture non-secret facts that will help rebuild:

```text
Current image digest
Hermes source commit annotation
Codex CLI version annotation
Enabled features
PVC name and size
Dashboard ingress setting
Whether Codex login is currently valid
Whether provider test currently succeeds
Whether Camofox health currently succeeds
```

Do not copy secret config, token contents, browser cookies, private
conversation text, or full database rows into the repo.

## Quick Reference

Useful read-only checks:

```bash
cd pulumi/apps/hermes
pulumi stack output --stack mx namespace
pulumi stack output --stack mx deployment
pulumi stack output --stack mx pvc
pulumi stack output --stack mx image
pulumi stack output --stack mx hermes_home
pulumi stack output --stack mx codex_home
```

Useful runtime checks:

```bash
$(pulumi stack output --stack mx codex_login_status_command)
$(pulumi stack output --stack mx codex_version_command)
$(pulumi stack output --stack mx provider_test_command)
$(pulumi stack output --stack mx camofox_healthcheck_command)
```

Useful Kubernetes checks:

```bash
NS="$(pulumi stack output --stack mx namespace)"

kubectl get pod,deploy,pvc,svc,ingress -n "$NS"
kubectl describe pod -n "$NS" -l app=hermes
kubectl logs -n "$NS" deploy/hermes -c gateway --tail=200
kubectl logs -n "$NS" deploy/hermes -c dashboard --tail=200
kubectl logs -n "$NS" deploy/hermes -c camofox --tail=200
```

Useful repo checks before proposing a Hermes stack change:

```bash
just sync pulumi/apps/hermes
just check-python
just lint
git diff --check
just preview pulumi/apps/hermes stack=mx
```

Use the preview result to understand what Kubernetes would change. Apply only
as an intentional live-infrastructure operation after the change and its state
impact are understood.
