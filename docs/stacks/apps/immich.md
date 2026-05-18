# Immich

Source: `pulumi/apps/immich`

Immich is a personal photo and video library. The useful mental model is not
"a web app with uploads"; it is a database-backed media archive with a web app
attached to it. The irreplaceable service is the relationship between three
things:

- the original media files on the library volume,
- the PostgreSQL rows that say what those files are and who they belong to,
- the background work that turns raw uploads into a browsable library.

If those three agree, Immich feels simple: open the UI, see the timeline, upload
a photo, search or browse, and move on. If any one of them drifts, Kubernetes can
look healthy while the library is not actually usable. A running pod does not
prove the media is present, a full PVC does not prove the database still points
to the right files, and a good database backup does not recreate videos and
photos that were never backed up from disk.

This repo deploys Immich with the upstream Helm chart, stores the media library
on an explicit PVC, connects the app to PostgreSQL, enables persistent Valkey,
disables the machine-learning component, and publishes the server through a
private Tailscale ingress at `https://immich`.

## Stack Shape

The Pulumi project is small, but it owns stateful behavior:

```text
Project:               pulumi/apps/immich
Pulumi runtime:         Python 3.12 through uv
Helm chart:             immich
Chart repository:       https://immich-app.github.io/immich-charts
Chart version:          0.12.0
Default image tag:      v2.7.5
Ingress host:           immich
Ingress class:          tailscale
Server Service:         immich-server
Server port:            2283
Library PVC:            immich-pvc
Default library size:   200Gi
Valkey:                 enabled, persistent, 8Gi
Machine learning:       disabled
Database extension env: DB_VECTOR_EXTENSION=pgvector
```

Pulumi creates the namespace from `namespace`, creates the `immich-pvc` media
library volume, writes the `immich-db-credentials` Kubernetes Secret, installs
the Helm chart, and creates an Ingress named `immich` that routes to
`immich-server` on port `2283`.

PostgreSQL is deliberately outside the Immich Helm chart. In the normal
production shape, the stack reads a `postgres_stack` StackReference and uses its
read-write service hostname, port, username, and password. For development or a
preview-only setup, the same program can read explicit `pg_host`, `pg_port`,
`pg_admin_user`, and secret `pg_admin_password` config instead. The Immich
database name defaults to `immich` unless `db_name` overrides it.

The stack does not currently export an Immich URL or resource summary. Treat
`pulumi/apps/immich/__main__.py` as the source of truth for object names and
chart values.

## First Principles

Immich has a human surface and a storage surface.

The human surface is what you use every day: a web UI, mobile clients, albums,
search, thumbnails, video playback, and uploads. That surface should be tested
with real behavior, not only Kubernetes readiness. After a change, open the
library, confirm existing media appears, upload a small asset, and watch that it
becomes usable.

The storage surface is what makes the human surface durable. Uploaded bytes are
written into the library path backed by `immich-pvc`. Asset records, users,
albums, metadata, thumbnails state, job state, and application settings live in
PostgreSQL. Valkey supports runtime coordination and job/cache behavior; this
stack gives it persistent storage, but it is not a substitute for backing up the
library PVC and database.

The background surface connects the two. An uploaded file is not fully "done"
just because the HTTP request returned. Immich still needs to extract metadata,
generate thumbnails, index useful data, and process media-specific work such as
video handling. Some of that work appears to the user as delayed thumbnails,
missing metadata, stale search results, or slow library updates. When debugging
Immich, always ask whether the failure is in upload, database write, media file
storage, background processing, or routing.

Machine-learning features are not part of this deployment. The stack sets
`machine-learning.enabled` to `False`, so do not expect the machine-learning
service to be present unless the Pulumi code is intentionally changed and
previewed. If a feature depends on Immich's ML service, its absence is expected
for this stack.

## Access And Routing

Open Immich through the private Tailscale ingress:

```text
https://immich
```

The request path is:

```text
browser or client
  -> Tailscale ingress host immich
  -> Kubernetes Ingress immich
  -> Service immich-server
  -> port 2283 on ready Immich server pods
```

That path gives a clean way to debug. A browser `502` usually means the ingress
reached Kubernetes but did not have a healthy backend. Check the Service
endpoints before editing ingress code:

```bash
cd pulumi/apps/immich
NS="$(pulumi config get namespace)"

kubectl get ingress,svc,endpoints -n "$NS"
kubectl describe ingress -n "$NS" immich
kubectl get endpoints -n "$NS" immich-server
```

If `immich-server` has no endpoints, the problem is usually pod readiness,
labels/selectors, or the server workload itself. In that case, editing the
Ingress host or TLS stanza will not fix the user-visible problem.

Inspect the workload and logs:

```bash
kubectl get pods,deploy,statefulset,svc,pvc -n "$NS" -o wide
kubectl logs -n "$NS" deploy/immich-server --tail=200
```

For client-side upload or playback problems, reproduce from a network location
that should actually have access to the Tailscale name. A local port-forward can
be useful for isolating app behavior from ingress behavior, but it does not
prove the published route works.

```bash
kubectl port-forward -n "$NS" svc/immich-server 2283:2283
```

Then open `http://localhost:2283` only as a temporary diagnostic path.

## Uploads And Imports

For normal use, upload through the Immich web UI or official clients. The safe
operating assumption is that every upload has two durable writes: a file write
to the library PVC and a metadata/application write to PostgreSQL. Background
work follows after that.

A small upload should prove all of these:

```text
the client can reach https://immich
the server accepts the upload
the library PVC is writable
PostgreSQL records the asset
background jobs make the asset browsable
the asset still appears after a server restart
```

Large imports are a different operational event than day-to-day uploads. They
stress storage, database write throughput, background queues, and thumbnail or
video processing. Do not use a large import as the first test after changing the
chart, image, database, or storage. Prove the system on a tiny batch first.

A cautious import sequence:

```text
confirm current backups for the PVC and database
check free space on the library volume
upload or import a tiny album
wait for thumbnails and metadata to settle
restart the server during a quiet window
confirm the same media is still present
watch storage growth and logs
then move to larger batches
```

Immich also has concepts such as external libraries and import workflows outside
this Pulumi program. This stack currently wires only the main library
persistence through `immich-pvc`; it does not add extra host mounts for an
external library. If external-library support is added later, document the mount
path, ownership model, backup story, and whether Immich owns or only indexes
those files. An indexed external folder has a different risk profile from files
uploaded into Immich-owned storage.

Avoid doing major imports while applying app upgrades or database migrations.
Both paths can be correct individually and still create an uncomfortable
recovery problem if they overlap.

## Media Storage

The media library lives on `immich-pvc`. Pulumi creates it directly and passes it
into the chart through `immich.persistence.library.existingClaim`.

The default size is `200Gi`:

```python
library_size = config.get("library_storage_size") or "200Gi"
```

That means the storage size can be changed through stack config, but a PVC
change is still a stateful storage operation. Before changing it, inspect the
preview for replacement or deletion. Expanding a volume is very different from
replacing it.

Useful storage checks:

```bash
cd pulumi/apps/immich
NS="$(pulumi config get namespace)"

kubectl get pvc -n "$NS" immich-pvc
kubectl describe pvc -n "$NS" immich-pvc
kubectl get pods -n "$NS" -o wide
```

To inspect mounted capacity from the server side:

```bash
kubectl exec -n "$NS" deploy/immich-server -- df -h
```

Do not delete `immich-pvc` as cleanup. If the app comes back empty after a
change, stop and inspect both the PVC and database before making another change.
Repeated restarts are less risky than a rushed storage edit, but the most useful
next step is usually to identify whether the app is pointing at the expected
claim and database.

## PostgreSQL Metadata

Immich's database is not just a cache. It is the catalog that makes the media
files into a library. It stores application state such as users, assets, albums,
metadata, paths, thumbnails state, and other records Immich needs to interpret
the bytes on disk.

This stack supplies database connection settings to the chart as environment
variables:

```text
DB_HOSTNAME
DB_PORT
DB_USERNAME
DB_PASSWORD from Secret immich-db-credentials
DB_DATABASE_NAME, default immich
DB_VECTOR_EXTENSION=pgvector
```

The password is written to the Kubernetes Secret `immich-db-credentials` in the
Immich namespace. You can verify the Secret exists without printing its value:

```bash
kubectl get secret -n "$NS" immich-db-credentials
```

If Immich logs database connection errors, check the non-secret shape first:

```bash
pulumi config get namespace
pulumi config get postgres_stack
pulumi config get db_name
```

If `postgres_stack` is set, inspect the producer stack outputs by name without
showing secrets. In this repo, the shared PostgreSQL stack is expected to export
the read-write service FQDN, port, username, and password consumed by Immich.

The stack sets `DB_VECTOR_EXTENSION` to `pgvector`. If search, indexing, or
migration behavior fails after a PostgreSQL change, verify that the expected
extension support exists in the target database. The database page notes that
the shared PostgreSQL `mx` config creates an `immich` application database and
enables vector-related extensions. Keep that producer-consumer relationship in
mind before changing PostgreSQL outputs, database names, or extension config.

Database migrations usually happen as part of application startup or upgrade
behavior. That is why an image upgrade is not only a container rollout. It may
also advance schema state. Once schema state advances, rollback may require both
a code rollback and a database restore, depending on the Immich release.

## Valkey And Background Work

The chart deploys Valkey with persistence enabled:

```text
valkey.enabled=true
valkey.persistence.data.enabled=true
valkey.persistence.data.size=8Gi
valkey.persistence.data.accessMode=ReadWriteOnce
```

Think of Valkey as runtime coordination for Immich, not the library of record.
It supports queue/cache style behavior that helps the app process work, but the
two durable assets you must be able to restore are still the media PVC and
PostgreSQL database.

Background work is where a lot of "Immich is up but not done" behavior lives.
Uploads may need metadata extraction, thumbnail generation, video processing,
library scanning, or indexing before the UI feels complete. If new uploads
arrive but thumbnails never appear, or if the UI shows assets but processing
seems stuck, inspect logs and workload health rather than only ingress.

```bash
kubectl get pods,deploy,statefulset -n "$NS"
kubectl logs -n "$NS" deploy/immich-server --tail=300
```

If the chart version changes and introduces different workload names, use a
broad namespace listing first instead of assuming the old deployment layout:

```bash
kubectl get all -n "$NS"
```

Do not treat a growing queue or delayed processing as proof that data is lost.
First establish whether the original files exist, whether database rows exist,
and whether the workers are making progress.

## Backups And Restore

An Immich backup is not complete unless it can restore both sides of the
library:

```text
media files on immich-pvc
PostgreSQL database metadata for the same point in time
```

A PVC-only backup leaves you with files but not the application catalog that
knows users, albums, asset IDs, paths, and metadata. A database-only backup
leaves you with rows pointing at files that may not exist. Either half can be
useful for forensics, but neither half is a full restore plan by itself.

Before large imports, chart upgrades, image upgrades, storage changes, or
database work, be able to answer:

```text
which backup contains immich-pvc?
which backup contains the Immich PostgreSQL database?
were they taken close enough together for a coherent restore?
has a restore been tested anywhere?
what would rollback require if migrations already ran?
```

Valkey persistence is helpful for smoother restarts, but do not let it distract
from the primary restore contract. If you can restore the media library and the
database cleanly, Immich has the durable material it needs. If you cannot
restore those, a healthy Valkey volume will not save the library.

The best restore rehearsal is a temporary namespace or cluster where the app can
start against restored copies without touching the production library. If that
is too heavy for a quick change, at least confirm where the current backups
live, what names they restore to, and whether any migration would make rollback
more complicated.

## Safe Upgrades

There are two upgrade surfaces in this Pulumi program:

```text
Helm chart version: k8s.helm.v4.Chart(... version="0.12.0")
Immich image tag:  config.get("image_tag") or "v2.7.5"
```

Changing the image tag upgrades Immich itself and may run application
migrations. Changing the chart version can change Kubernetes object names,
labels, selectors, values structure, subcharts, probes, persistence behavior,
and hooks. Treat both as migrations.

Before changing either one:

```text
read the relevant Immich release notes
read chart value changes for the versions involved
confirm current database and PVC backups
identify whether rollback is code-only or data restore plus code
preview the exact stack before applying
```

Use the repo commands for validation:

```bash
just sync pulumi/apps/immich
just check-python
just lint
git diff --check -- docs/stacks/apps/immich.md pulumi/apps/immich
just preview pulumi/apps/immich stack=mx
```

Do not run `pulumi up`, `pulumi destroy`, or `just up` unless the person asking
for the change explicitly asks for an apply or destructive operation.

Pay special attention to `immich-server`. This stack has a transform named
`preserve_immich_server_selector` that strips
`app.kubernetes.io/controller` from the rendered `immich-server` Service and
Deployment selector/pod labels. That is intentional. Without it, the chart can
disturb the Service/Deployment relationship and leave the ingress pointing at a
Service with no useful endpoints.

The stack also adds `pulumi.com/skipAwait` through a chart transform. That is an
operational guardrail for noisy Helm await behavior. Do not remove either
transform during cleanup unless you understand the live failure it protects
against and have previewed the replacement behavior.

After an approved apply, use product behavior as the final check:

```text
open https://immich
confirm existing media is visible
upload one small test asset
wait for thumbnail and metadata processing
check server logs for migration or processing errors
confirm immich-server endpoints exist
```

If anything looks wrong, avoid making storage or database edits as the first
reaction. Classify the failure layer first.

## Operating Checks

Start every live debugging session by getting the namespace from Pulumi config:

```bash
cd pulumi/apps/immich
NS="$(pulumi config get namespace)"
```

Then take a broad but non-secret view:

```bash
kubectl get pods,deploy,statefulset,svc,endpoints,ingress,pvc -n "$NS" -o wide
kubectl describe ingress -n "$NS" immich
kubectl describe pvc -n "$NS" immich-pvc
kubectl logs -n "$NS" deploy/immich-server --tail=200
```

Use symptoms to choose the next layer:

```text
browser cannot resolve immich
  check Tailscale access and the published private hostname

browser gets 502
  check ingress, Service, endpoints, pod readiness, and the selector transform

login page loads but uploads fail
  check server logs, PVC mount, free space, and database writes

existing media disappears
  stop changing things; verify the app still points at immich-pvc and the
  expected PostgreSQL database

new media uploads but thumbnails or metadata do not settle
  inspect server logs and background processing behavior

database errors appear after an upgrade
  check migration logs, DB connection config, extension support, and rollback
  implications

ML-backed features are unavailable
  expected for this stack unless machine-learning has been deliberately enabled
```

A restart can be a useful validation after a planned change, but avoid doing it
during active uploads:

```bash
kubectl rollout restart -n "$NS" deploy/immich-server
kubectl rollout status -n "$NS" deploy/immich-server
```

After the rollout, reload the UI and confirm existing media still appears. That
proves more than pod readiness because it crosses the route, app process,
database, and media volume.

## Change Boundaries

Keep routine docs or code edits scoped to `pulumi/apps/immich` and this page.
The shared PostgreSQL stack is a dependency, not an implementation detail to
change casually from the Immich side. If a change requires PostgreSQL outputs,
database creation, extensions, or credentials to move, preview the producer and
consumer relationship explicitly.

Do not paste secret values, decrypted Pulumi outputs, kubeconfig data, private
URLs beyond the intended service name, or full secret-bearing logs into docs or
PR text. Most Immich operations can be described with resource names and
non-secret wiring:

```text
namespace name
PVC name
Service and Ingress names
database name
Secret name, not Secret value
chart and image versions
```

When in doubt, preserve the library first. The stack exists so the photo library
survives ordinary pod churn, chart churn, and app upgrades. Any change that
risks `immich-pvc` or the PostgreSQL metadata deserves a slower review than a
stateless Deployment edit.
