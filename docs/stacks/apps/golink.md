# golink

Source: `pulumi/apps/golink`

golink is the private short-link service for this cluster. Its job is simple:
turn a small, memorable internal name into the longer URL that actually does
the work.

The important word is "private". This stack does not publish a public HTTP
hostname through Cloudflare, an Ingress, or a Kubernetes Service. It runs the
Tailscale golink server inside Kubernetes and lets that process join the
tailnet directly with tsnet. A person reaches golink from a device that is
already on the tailnet and allowed by tailnet policy. From Kubernetes' point of
view it is a single pod with a PVC. From the tailnet's point of view it is a
Tailscale device.

That split is the key to operating it. A healthy pod does not prove the tailnet
name works. A visible tailnet device does not prove the SQLite database is
mounted and writable. Debug both halves.

## What Pulumi Builds

The `mx` stack config currently sets the namespace, image, storage size, and
tailnet tag, and imports Tailscale provider configuration through the
`infra/tailscale` ESC environment. Do not remove that environment import when
auditing configuration; it is part of how the provider can create the tailnet
auth key.

The Pulumi program creates:

```text
Namespace:             golink
Deployment:            golink
Labels/selectors:      app=golink
Image:                 ghcr.io/tailscale/golink:main
Replicas:              1
Strategy:              Recreate
PVC:                   golink-storage
PVC access mode:       ReadWriteOnce
PVC default size:      1Gi
Container user/group:  65532
Pod fsGroup:           65532
Auth Secret:           golink-auth
Auth env var:          TS_AUTHKEY
SQLite path:           /home/nonroot/golink.db
tsnet config dir:      /home/nonroot/tsnet-golink
PVC mount path:        /home/nonroot
Tailnet tags:          tag:golink
Auth key expiry:       90 days by default
Auth key reusable:     true by default
Auth key ephemeral:    false by default
Auth key preauthorized: true by default
```

It deliberately does not create a Kubernetes `Service`, `Ingress`, or
Cloudflare route. The golink process handles its own network presence through
tsnet. If you are debugging reachability, looking for a missing Service is the
wrong first question for this stack.

The container starts with:

```text
--verbose
--sqlitedb /home/nonroot/golink.db
--config-dir /home/nonroot/tsnet-golink
```

The whole `/home/nonroot` directory is backed by the PVC. That means the link
database and the tailnet node identity live on the same volume.

## The Private Short-Link Model

A short link is not just a convenience URL. In practice it becomes shared
language. When people type, paste, document, or automate a short name, that
name becomes a tiny contract.

Use stable names for stable things. A link target can move from one long URL to
another while the short name stays the same; that is the point of having the
service. The reverse is more disruptive: deleting or renaming an established
short link can break docs, saved messages, scripts, and habits even if every
Kubernetes object looks healthy.

Keep temporary names obviously temporary. Avoid putting secrets, one-time
tokens, private invitation URLs, or credentials into link targets unless you
are comfortable with them being stored in golink's SQLite database and visible
to anyone who can administer or read that database. Tailnet access is an access
boundary, not a secret manager.

If another service guide depends on a short link, mention the link in that
service's guide too. golink stores the mapping, but the owning service should
explain why the link exists and what workflow it supports.

## Request Routing

The request path is:

```text
tailnet client
  -> Tailscale DNS / tailnet device routing
  -> golink tsnet node inside the pod
  -> golink HTTP handler
  -> SQLite lookup in /home/nonroot/golink.db
  -> redirect to the stored destination
```

There is no Kubernetes load balancer in that path. Kubernetes schedules the pod
and mounts the PVC, but the network identity people use is owned by the tsnet
state on disk plus the current tailnet device record.

That has a useful debugging consequence: classify failures by layer before
editing code.

If the name does not resolve or is not reachable from a laptop, start with
tailnet membership, MagicDNS, ACL/tag policy, and whether the golink device is
online. If the device is online but the UI or redirect fails, inspect the pod,
logs, database path, and volume permissions. If Kubernetes shows the pod is
running but the device never appears in the tailnet, inspect tsnet auth, the
Secret reference, the auth key, and the config directory on the PVC.

## Using The Service

Start from the stack outputs. They give you the names and paths that the Pulumi
program intends to own:

```bash
cd pulumi/apps/golink

pulumi stack output --stack mx namespace
pulumi stack output --stack mx deployment
pulumi stack output --stack mx pvc
pulumi stack output --stack mx sqlitedb_path
pulumi stack output --stack mx tailscale_config_dir
pulumi stack output --stack mx tailnet_key_expires_at
pulumi stack output --stack mx tailnet_key_tags
```

Do not print or copy the Tailscale auth key itself. The stack exports key
metadata, and Kubernetes stores the actual auth material in the `golink-auth`
Secret.

From a device on the tailnet, find the golink device and verify basic tailnet
reachability:

```bash
tailscale status | rg -i golink
tailscale ping <golink-tailnet-name>
```

Then open the golink tailnet name in a browser from that same tailnet context.
Use the UI to create and edit links. A good post-change smoke test is to open
an existing important link, create a temporary test link, resolve it, and then
remove the temporary link.

## Link Storage And Identity State

Two different kinds of state share the same PVC:

```text
/home/nonroot/golink.db         SQLite database with short-link mappings
/home/nonroot/tsnet-golink      tsnet node configuration and identity state
```

The SQLite database is the service data. If it is empty, missing, replaced, or
not writable, the service may come up with no useful links or fail when saving
changes.

The tsnet config directory is the network identity. It lets the process keep
using the same tailnet identity across normal pod restarts. If that directory
is lost, the pod may rejoin the tailnet as a new device, may need a valid auth
key, and may no longer be reachable at the name people expect.

The Deployment uses one replica and `Recreate` for a reason. SQLite and a
single tsnet identity do not want two active pods writing the same PVC or trying
to present the same service identity. Do not scale this Deployment horizontally
without redesigning storage and identity semantics first.

The pod runs as UID/GID `65532`, drops Linux capabilities, disallows privilege
escalation, and sets `fsGroup` to `65532`. If the database is present but writes
fail, check PVC ownership and effective permissions before changing application
flags.

## Auth Keys And Tailnet Access

Pulumi creates a `tailscale.TailnetKey` named for golink and stores the key in
the Kubernetes Secret as `TS_AUTHKEY`. The key is reusable and preauthorized by
default, tagged with `tag:golink`, and configured with `recreate_if_invalid` so
Pulumi can replace it when needed.

The key gets the process into the tailnet. It is not the whole long-term
identity story. Once golink has joined, the tsnet state on the PVC is what makes
normal pod restarts stable. A key expiring does not necessarily remove an
already-authenticated tsnet node, but it can matter if the pod has to create a
fresh identity after PVC loss, restore mistakes, or deliberate reset.

The tag also matters outside this repo. Tailnet ACLs and tag ownership decide
whether a preauthorized tagged device can join and who can reach it. If logs
show auth or permission failures, include tailnet policy in the investigation;
there may be nothing wrong with Kubernetes.

Safe key rotation means preserving the PVC, previewing the Pulumi change, and
then verifying that the same expected golink device is reachable afterward. If
you intentionally reset identity, plan for client-facing fallout and clean up
the old tailnet device only after the replacement is verified.

## Backups

A useful backup for golink is a backup of both the links and the service
identity:

```text
include /home/nonroot/golink.db
include any adjacent SQLite files such as golink.db-wal or golink.db-shm
include /home/nonroot/tsnet-golink/
preserve ownership/permissions for UID/GID 65532
```

Backing up only the database preserves links but may not preserve the tailnet
device identity. Backing up only the tsnet directory preserves identity but not
the links. For normal disaster recovery, treat the whole PVC as the unit of
backup.

Prefer the cluster's normal PVC snapshot or backup mechanism when available.
That avoids depending on tools inside the application image and gives you a
volume-level restore path. If you need an ad hoc copy, do it during a quiet
window and copy the database together with any SQLite sidecar files. For the
most consistent file copy, stop writes first or use a storage-level snapshot.

Useful inspection before a backup:

```bash
cd pulumi/apps/golink
NS="$(pulumi stack output --stack mx namespace)"
POD="$(kubectl get pod -n "$NS" -l app=golink -o jsonpath='{.items[0].metadata.name}')"

kubectl get pvc -n "$NS"
kubectl exec -n "$NS" "$POD" -- ls -la /home/nonroot
```

If the application image does not contain the shell, `ls`, `tar`, or `sqlite3`
tools you want, use a storage snapshot or a short-lived helper pod that mounts
the PVC. Do not install tools into the running application container as the
backup plan.

Restore is successful only when both checks pass: existing links resolve, and
the service is reachable through the expected tailnet identity. A restore that
brings back the links under a new tailnet device may still require user-facing
communication or DNS/name cleanup.

## What To Inspect

Kubernetes tells you whether the pod can run and whether the volume is mounted:

```bash
cd pulumi/apps/golink
NS="$(pulumi stack output --stack mx namespace)"

kubectl get pods,pvc,secrets -n "$NS"
kubectl get events -n "$NS" --sort-by=.lastTimestamp
kubectl logs -n "$NS" deploy/golink --tail=200
kubectl describe pod -n "$NS" -l app=golink
kubectl describe pvc -n "$NS" golink-storage
```

The tailnet tells you whether the service identity is present and reachable:

```bash
tailscale status | rg -i golink
tailscale ping <golink-tailnet-name>
```

Read both views together. For example, a pod stuck in `Pending` with an
unbound PVC is a storage scheduling problem. A running pod with Tailscale auth
errors is probably key, tag, ACL, Secret, or tsnet-state related. A reachable
tailnet device with missing links points back to the SQLite database and PVC.

## Common Failure Modes

The tailnet name does not resolve or does not show up in `tailscale status`.
Confirm the client is on the right tailnet, then inspect golink logs for tsnet
startup/auth messages. Check that the `golink-auth` Secret exists, that the pod
is reading `TS_AUTHKEY`, and that the tag is valid in tailnet policy.

The pod is running but the tailnet device is absent. Kubernetes may be fine
while tsnet cannot authenticate or cannot write its config directory. Check the
Secret reference, auth key validity, tag policy, and permissions on
`/home/nonroot/tsnet-golink`.

The tailnet device is visible but the web UI fails. Verify you are opening the
right device name from a tailnet client. Then check container logs and whether
the process can read and write `/home/nonroot/golink.db`.

Links disappeared after a restart or rollout. Suspect a replaced or newly
empty PVC, a changed SQLite path, a failed restore, or a pod running without the
expected volume mounted. Do not create replacement links until you know whether
the old database still exists somewhere.

Link edits appear to work but do not survive. Check database writability,
SQLite sidecar files, PVC mount state, UID/GID ownership, and any errors in the
verbose logs.

The pod is stuck in `Pending` or `ContainerCreating`. Inspect the PVC,
storage class, node attachment, and events. The PVC is `ReadWriteOnce`, so
attachment conflicts or node-local storage behavior can block scheduling.

The pod joins as a new tailnet identity after storage work. The tsnet config
directory was probably lost, excluded from restore, or made unreadable. Decide
whether to restore the old identity or accept the new one deliberately, then
clean up stale tailnet state after verification.

Behavior changes even though Pulumi did not. The image is configured as
`ghcr.io/tailscale/golink:main`, and the pull policy is `IfNotPresent`. That
combination can make runtime version drift depend on node image cache and pod
scheduling. If reproducibility matters for a change, pin the image to a stable
tag or digest in Pulumi and preview that explicit change.

## Safe Changes

Start with the live model before editing:

```bash
cd pulumi/apps/golink

pulumi stack output --stack mx namespace
pulumi stack output --stack mx pvc
pulumi stack output --stack mx tailscale_config_dir
tailscale status | rg -i golink
```

Then make the repo-backed change and run the normal checks:

```bash
just sync pulumi/apps/golink
just check-python
just lint
git diff --check
just preview pulumi/apps/golink stack=mx
```

Do not run `pulumi up`, `pulumi destroy`, or `just up` unless the user has
explicitly asked for an apply or destructive action.

Treat these changes as user-facing:

```text
renaming the namespace, Deployment, PVC, or Secret
changing the SQLite path
changing the tsnet config directory
changing tailnet tags or auth-key behavior
changing from private tsnet access to Service/Ingress access
changing storage class
replacing the PVC
scaling above one replica
moving away from Recreate strategy
changing the image tag or digest
```

Storage-size increases may be routine if the storage class supports expansion.
Storage-class changes are usually migrations because a PVC's class is not a
casual in-place edit. A PVC replacement is a data and identity event, not just
an infrastructure cleanup.

Keep the tailnet access model intentional. Adding a Kubernetes Service or
Ingress would create a second routing path with different security properties.
That can be valid, but it should be designed as an exposure change, not added
as a debugging shortcut.

After an approved apply, verify all three layers:

```text
Kubernetes: pod is running, PVC is bound, logs are quiet
Tailnet: expected golink device is online and pingable
Application: existing link resolves, temporary test link can be created and removed
```

If any layer fails, preserve the PVC while you investigate. For golink, the
fastest way to make a small incident much larger is to reset storage or
identity before you know which one is broken.
