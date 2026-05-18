# Stitch

Source: `pulumi/apps/stitch`

Stitch is an integration service for Twitch stream tracking and notification.
From the infrastructure repo alone, the service is clearly not a generic web
app: the Pulumi program wires together Twitch credentials, a Twitch webhook
URL, a webhook signing secret, Discord credentials, a Discord channel, and a
PostgreSQL database. The configured Helm chart and local Stitch application
source add the next layer of context: Stitch runs a gRPC server for client
operations, receives Twitch EventSub callbacks on a webhook listener, stores
tracked channels and stream history in PostgreSQL, and posts or edits Discord
messages as stream state changes.

That means the useful operating question is not just "is the pod running?" A
running pod only proves the Rust process started. Stitch is healthy when it can:

- connect to PostgreSQL and run its migrations;
- authenticate to Twitch and synchronize EventSub subscriptions;
- receive Twitch webhook callbacks through the Cloudflare Tunnel ingress;
- validate those callbacks with the configured webhook secret;
- update stream/channel state in PostgreSQL;
- reach Discord with the configured bot token and channel;
- answer gRPC client calls on its private service port.

Any one of those edges can fail while Kubernetes still reports an available
Deployment. Read Stitch as a small event pipeline with Kubernetes in the middle,
not as a self-contained service.

## Ownership Boundary

This repo owns the Pulumi wrapper at `pulumi/apps/stitch`. That wrapper creates
the infrastructure contract:

- a PostgreSQL role named `stitch`;
- a PostgreSQL database named `stitch`;
- a generated database password;
- a database URL assembled from the generated role/password and PostgreSQL
  config;
- a Kubernetes namespace, defaulting to `stitch`;
- a Helm v4 chart render named `stitch`;
- chart values for server, database, Twitch, webhook, Discord, ingress, and
  Tailscale service exposure.

The actual Helm chart is not checked into this infra repo. The stack reads a
`path` config value and renders the chart at that path. In the current `mx`
stack config, `path` points at a local Stitch chart checkout outside this repo.
Use the config command below to inspect the path on your machine instead of
assuming a relative chart directory exists here:

```bash
cd pulumi/apps/stitch
pulumi config get path --stack mx
```

That split matters. If you change only the external chart, the durable source of
that chart change is the Stitch repo, not this infra repo. If you change only
this Pulumi wrapper, you are changing the values and Kubernetes objects that the
chart receives, not the server code itself. Keep those ownership lines clear
when debugging or reviewing a preview.

The `mx` stack also imports an ESC environment. Do not audit Stitch by reading
only `Pulumi.mx.yaml`; that file is ignored and may contain only local overrides
plus the imported environment name. Required config can come from ESC, local
stack config, or provider config.

```bash
cd pulumi/apps/stitch
pulumi config --stack mx
pulumi config env ls --stack mx
```

Normal `pulumi config` output masks secret-backed values. Keep it that way.
Avoid `--show-secrets` unless you are doing a local credential operation in a
trusted terminal, and do not paste the result into docs, chat, issues, or commit
messages.

## What Pulumi Builds

The Pulumi program is short, so it is worth keeping the shape in your head.

First, it creates database identity:

```text
RandomPassword: stitch-password
PostgreSQL role: stitch
PostgreSQL database: stitch
```

Then it builds a database URL like:

```text
postgres://<role>:<generated-password>@<POSTGRES_HOST>.<k8s_namespace>:<postgresql:port>/stitch?sslmode=disable
```

Be careful with the naming here. `namespace` is the Kubernetes namespace for
Stitch itself. `k8s_namespace` is used in the database host name that Pulumi
passes to the application. Those are separate config keys, and mixing them up
will produce a database URL that looks plausible while pointing at the wrong
place.

After that, Pulumi creates the Stitch namespace and renders the Helm chart with
these value groups:

```text
config.server.port
config.database.url
config.twitch.clientId
config.twitch.clientSecret
config.twitch.webhookUrl
config.webhook.secret
config.webhook.port
config.webhook.url
config.discord.token
config.discord.channel
ingress.enabled
ingress.className
ingress.host
service.annotations
```

The chart renders a single-container Deployment, a ClusterIP Service, a
ConfigMap, a Secret, and a Cloudflare Tunnel Ingress. Live object names currently
match the chart release:

```text
Namespace:    stitch
Deployment:   stitch
Service:      stitch
ConfigMap:    stitch-config
Secret:       stitch-secret
Ingress:      stitch
```

The Service exposes three ports:

```text
50051  gRPC server
50052  Twitch webhook listener
50053  tokio-console
```

The Cloudflare Tunnel Ingress routes public webhook traffic to service port
`50052`. The Service is also annotated for Tailscale service exposure with the
hostname `stitch`, which is the private access path for the service rather than
the public Twitch callback path.

There are no stack outputs today:

```bash
cd pulumi/apps/stitch
pulumi stack output --stack mx
```

Expect that command to say there are no output values unless the Pulumi program
is later expanded.

## Config And External Dependencies

Stitch has three kinds of config: Pulumi/provider config, application config,
and external provider state.

The Pulumi/PostgreSQL provider side needs enough PostgreSQL configuration to
connect as an administrator and create the `stitch` role and database. The app
side also needs enough information to assemble the runtime database URL. In
practice, inspect these keys from `pulumi/apps/stitch`:

```text
postgresql:host
postgresql:username
postgresql:password
postgresql:port
postgresql:sslmode
POSTGRES_HOST
k8s_namespace
namespace
path
PORT
WEBHOOK_PORT
WEBHOOK_URL
WEBHOOK_SECRET
TWITCH_CLIENT_ID
TWITCH_CLIENT_SECRET
DISCORD_TOKEN
DISCORD_CHANNEL
```

Some of those are secrets and some are not. Treat all provider credentials,
tokens, webhook secrets, generated database passwords, and complete connection
URLs as sensitive. The Discord channel and Twitch client ID may not be secret in
the same way a token is, but they are still environment-specific configuration
and should not be copied casually into public docs.

`WEBHOOK_URL` deserves special attention. The name sounds like it might be a
full URL, and the Helm README uses URL-like examples, but the Stitch server code
currently formats Twitch callbacks as:

```text
https://<WEBHOOK_URL>/webhook/twitch
```

The same value is used as the Kubernetes Ingress host. In the current operating
shape, set it as the callback hostname, not as a full URL with scheme and path,
unless the application code and chart change together.

The external dependencies are:

- shared PostgreSQL, reachable from the cluster and from the Pulumi PostgreSQL
  provider during preview/apply;
- Twitch OAuth, Helix, and EventSub APIs;
- Twitch provider-side EventSub subscription state for tracked channels;
- a Cloudflare Tunnel controller and DNS/route state for the webhook hostname;
- Discord HTTP API access for the configured bot token and destination channel;
- the Tailscale operator/tailnet for private service access;
- the external Stitch Helm chart and server image.

The chart default image is `ghcr.io/kzh/stitch-server:latest` with
`imagePullPolicy: Always`. That is convenient during development, but it is not
reproducible. A pod restart can pull different application code without any
Pulumi diff if the `latest` tag moved. For operationally serious changes, prefer
a pinned tag or digest in the chart values and preview the resulting change.

One current security caveat is worth calling out plainly: the Helm chart renders
`DATABASE_URL` into `stitch-config`, and that URL includes the generated
database password. Do not print ConfigMap values in shared logs or tickets. If
you only need to check which keys exist, list keys without values:

```bash
NS="$(pulumi config get namespace --stack mx 2>/dev/null || echo stitch)"

kubectl get cm stitch-config -n "$NS" \
  -o go-template='{{range $k, $_ := .data}}{{println $k}}{{end}}'

kubectl get secret stitch-secret -n "$NS" \
  -o go-template='{{range $k, $_ := .data}}{{println $k}}{{end}}'
```

Moving the database URL into a Secret would be a chart/application packaging
change, not a docs-only fix. Until that changes, treat the ConfigMap as
sensitive.

## Request Paths

There are two primary request paths and one operator/debug path.

The private gRPC path is for Stitch clients and operator access:

```text
client
-> Tailscale hostname `stitch` or local port-forward
-> Kubernetes Service `stitch`, port 50051
-> Stitch gRPC server
-> PostgreSQL, Twitch API, and webhook subscription management
```

The gRPC API is defined in the Stitch proto, not in this infra repo. From the
local Stitch source, the server supports operations to track a channel, untrack a
channel, list tracked channels, list stored stream history, and stream channel
profile images. Tracking a channel is not just a database write: the server
fetches Twitch channel metadata, stores it, registers webhook handling, and
subscribes the channel to Twitch events.

For local testing through Kubernetes, port-forward the gRPC service:

```bash
NS="$(pulumi config get namespace --stack mx 2>/dev/null || echo stitch)"
kubectl port-forward -n "$NS" svc/stitch 50051:50051
```

The Stitch client defaults to a local `127.0.0.1:50051` server in the local
Stitch source, so a port-forward is the least surprising way to test client
behavior without depending on tailnet DNS. Use the actual client commands from
the Stitch repo; this infra repo does not define them.

The public webhook path is for Twitch EventSub:

```text
Twitch EventSub
-> https://<WEBHOOK_URL>/webhook/twitch
-> Cloudflare Tunnel Ingress `stitch`
-> Kubernetes Service `stitch`, port 50052
-> Stitch webhook listener
-> signature/challenge/notification handling
-> PostgreSQL and Discord
```

The ingress rule uses `path: /` with `Prefix`, but the application route that
matters is `/webhook/twitch`. A request can reach the pod and still fail if it
uses the wrong path, lacks Twitch headers, has the wrong signature, or carries a
payload the app does not recognize.

A raw unsigned request is only a routing probe. It should not be interpreted as
a successful webhook test. For example, a 4xx response from the application can
still prove that Cloudflare reached the pod, while a 502 or timeout points you
back toward ingress, tunnel, Service, endpoints, or pod availability:

```bash
curl -i "https://<webhook-host>/webhook/twitch"
```

To prove the integration works, use Twitch's EventSub test tools or perform a
real harmless provider-side action that sends a valid signed callback.

The debug path is tokio-console on port `50053`:

```text
operator
-> port-forward or private service access
-> Service `stitch`, port 50053
-> tokio-console subscriber inside the Rust server
```

The chart exposes the port, and the server starts the console subscriber, but
this repo does not define a ready-made command for using it. Treat it as an
advanced Rust runtime debugging surface, not as the main health check.

## Logs

The primary logs are the Stitch container logs:

```bash
NS="$(pulumi config get namespace --stack mx 2>/dev/null || echo stitch)"

kubectl logs -n "$NS" deploy/stitch --tail=200
kubectl logs -n "$NS" deploy/stitch --previous --tail=200
```

Use `--previous` when a pod has restarted. The current chart has no explicit
readiness or liveness probes, so Kubernetes availability is a fairly coarse
signal. Restart count and previous logs matter.

On startup, useful log lines include:

```text
Stitch gRPC server listening: 0.0.0.0:<PORT>
Stitch webhook server listening: 0.0.0.0:<WEBHOOK_PORT>
```

During normal operation, logs identify Twitch webhook activity with messages
like stream online, stream offline, and channel update events. Those lines often
include Twitch channel names. That is useful during local debugging, but it is
not a reason to paste raw logs into public places.

The chart controls `RUST_LOG` with the `logLevel` value. Its default is:

```text
info,sqlx=info
```

If you need more detail, prefer a targeted temporary log-level change through
the chart/Pulumi path and preview it. Do not leave noisy debug logging on by
accident; integration services can log a lot of provider activity.

Database connection failures deserve extra care. The server code adds context
around the full `DATABASE_URL` when establishing the pool. Depending on how an
error is surfaced, a raw database error log may include credential-bearing
connection text. Summarize the error category instead of copying the entire
line into docs or chat.

## First Debugging Pass

Start by deciding which path is broken. "Stitch is down" is too broad to act on
well. Ask whether the failing thing is gRPC client access, Twitch webhook
delivery, Discord notification delivery, database state, or a Pulumi preview.

For a basic live check:

```bash
cd pulumi/apps/stitch
NS="$(pulumi config get namespace --stack mx 2>/dev/null || echo stitch)"

kubectl get deploy,pods,svc,endpoints,ingress,cm,secret -n "$NS"
kubectl describe deploy stitch -n "$NS"
kubectl logs -n "$NS" deploy/stitch --tail=200
```

The Service and Endpoints check is important. The ingress and Tailscale
exposure can both be correct while the backend Service has no endpoints. If
`endpoints/stitch` is empty, stay in Kubernetes: check pod labels, readiness,
container state, and the Service selector before changing Cloudflare or
Tailscale.

Check the rendered Service ports without printing secret data:

```bash
kubectl get svc stitch -n "$NS" \
  -o go-template='{{range .spec.ports}}{{.name}} {{.port}} -> {{.targetPort}}{{"\n"}}{{end}}'
```

Check the public webhook backend:

```bash
kubectl get ingress stitch -n "$NS" \
  -o jsonpath='{.spec.ingressClassName}{" "}{.spec.rules[0].http.paths[0].backend.service.name}{":"}{.spec.rules[0].http.paths[0].backend.service.port.number}{"\n"}'
```

Expected shape:

```text
cloudflare-tunnel stitch:50052
```

If the public URL returns a backend error, inspect the app Service and endpoints
first. Then inspect the Cloudflare Tunnel controller:

```bash
kubectl logs -n cloudflare-tunnel \
  -l app.kubernetes.io/name=cloudflare-tunnel-ingress-controller \
  --tail=200
```

For private access through Tailscale, verify both Kubernetes and the tailnet
side:

```bash
kubectl get svc stitch -n "$NS" -o yaml
tailscale status
tailscale ping stitch
```

The Tailscale service annotation exposes the service under the private hostname.
If `tailscale ping stitch` fails, do not assume the pod is broken. It can be a
tailnet DNS, ACL, proxy, or client-path issue.

## Config And Preview Failures

If `pulumi preview` or `just preview pulumi/apps/stitch stack=mx` fails before
the Python program runs, look at ESC and stack config before debugging the
Pulumi code. A historical failure mode for this stack was an ESC environment
resolution error of the form:

```text
opening environment: [0] Diags: no matching item
```

That points at the imported environment, not at the Stitch Python program. Check
the import and access:

```bash
cd pulumi/apps/stitch
pulumi config env ls --stack mx
pulumi env get stitch/prod
```

`pulumi env get` keeps secrets masked. Avoid `pulumi env open` unless you really
need resolved secret values locally.

If preview gets as far as rendering Helm and then complains that
`DATABASE_URL` is missing, check the chart path and value names. The external
chart has a helper that fails when `config.database.url` is absent. This Pulumi
program does pass that value, so a missing `DATABASE_URL` usually means you are
rendering a different chart than expected, the chart schema changed, or the
value path changed.

If preview wants to replace the database role, database, namespace, Service, or
Ingress, slow down. Those names are contracts. A rename can break runtime state,
provider subscriptions, clients, or hostnames even when the diff looks small.

## Database Debugging

The durable state for Stitch lives in PostgreSQL. There is no PVC in this stack.
The application stores tracked channels, stream records, stream update events,
profile image data, and backfill markers in the `stitch` database, based on the
current server migrations.

The server runs SQLx migrations at startup. If migrations fail, the pod may
restart or exit before either the gRPC or webhook listener is useful. Look for
errors around:

```text
failed to establish database pool
running database migrations
failed to list channels from database
```

When database connection fails, check the pieces separately:

- Does the PostgreSQL stack exist and expose the expected service?
- Does Pulumi provider config still let the PostgreSQL provider create/read the
  `stitch` role and database?
- Does the app database URL point at the same PostgreSQL namespace and port?
- Did the generated password rotate without the running Secret/ConfigMap and
  database role agreeing?
- Did a migration fail because the image changed while the database state did
  not?

Do not print the complete `DATABASE_URL` as a debugging shortcut. The current
chart puts it in the ConfigMap, but it still contains a password.

## Twitch Debugging

Twitch has two separate roles here: API access and webhook delivery.

API access is used when Stitch initializes the Twitch client, looks up channels,
gets profile images, lists streams, and creates or deletes EventSub
subscriptions. Failures here tend to show up while tracking/untracking channels
or during startup synchronization.

Webhook delivery is the reverse path: Twitch calls Stitch at:

```text
https://<WEBHOOK_URL>/webhook/twitch
```

The server validates Twitch webhook headers and handles both verification
challenges and notifications. For tracked channels, it subscribes to:

```text
stream.online
channel.update
stream.offline
```

The useful split is:

- No request appears in Stitch logs: check Twitch subscription state, callback
  hostname, DNS, Cloudflare Tunnel, Ingress, Service, and endpoints.
- Request reaches the pod but is rejected: check webhook secret, Twitch
  signature headers, body handling, and whether the request is a challenge or
  notification.
- Request is accepted but state is wrong: check database writes, stream/channel
  logic, and Discord follow-on behavior.
- Subscriptions are not created: check Twitch client ID/secret, app
  authorization, EventSub limits, and whether the callback host is reachable by
  Twitch over HTTPS.

Changing `WEBHOOK_URL` is a provider-facing migration. It changes the callback
host used for EventSub subscriptions and the Cloudflare Ingress host. After such
a change, verify provider-side subscriptions and trigger a real EventSub test.

Rotating `WEBHOOK_SECRET` is also a provider-facing change. Existing Twitch
subscriptions were created with a secret. Expect to resynchronize or recreate
subscriptions, then prove that valid signed callbacks are accepted.

## Discord Debugging

Discord is downstream of the webhook path. A Twitch event can be received and
stored correctly while Discord delivery fails.

The app builds a Discord HTTP client from `DISCORD_TOKEN` and sends, edits, or
deletes messages in `DISCORD_CHANNEL`. Check:

- the token is present as a Pulumi secret and rendered into `stitch-secret`;
- the bot is still valid and has access to the target server/channel;
- the channel ID is the intended destination;
- the bot has permission to send, edit, and delete messages as required;
- Discord API errors appear in Stitch logs after accepted Twitch events.

Do not rotate the Discord token as a casual first fix. If the token was revoked
or leaked, rotate it intentionally through secret config or ESC, preview the
stack, restart/redeploy through the normal path, and test a harmless message
flow.

## Common Failure Patterns

Preview fails with an ESC environment-opening error. Inspect the `stitch/prod`
environment import and Pulumi account access before editing Python.

Preview cannot find the chart. Check `pulumi config get path --stack mx` and
whether the local Stitch chart checkout exists on this machine.

Preview or render says `DATABASE_URL` is required. Confirm you are rendering the
expected chart and that the chart still accepts `config.database.url`.

Pod starts and exits. Check previous logs for database connection, migration,
Twitch initialization, or webhook binding errors.

Pod is running but gRPC clients cannot connect. Check Tailscale/private access,
Service port `50051`, endpoints, and client target address. Port-forward to
isolate Kubernetes from tailnet issues.

Webhook hostname returns 502 or times out. Check Service endpoints, pod state,
Ingress backend port `50052`, and Cloudflare Tunnel controller logs.

Webhook route returns an application error. That may prove routing works. Check
path, method, Twitch headers, signature, challenge handling, and webhook secret.

Twitch subscriptions are missing or stale. Check Twitch credentials, callback
host, app/EventSub state, and startup sync logs.

Discord messages do not appear. Check Discord token, channel ID, bot
permissions, and logs after accepted Twitch events.

Stream history is missing or stale. Check database writes, migrations, backfill
logs, and whether the relevant Twitch events were delivered.

## Safe Changes

Use the repo wrappers for validation:

```bash
just sync pulumi/apps/stitch
just check-python
just lint
git diff --check
just preview pulumi/apps/stitch stack=mx
```

Do not run `just up`, `pulumi up`, `pulumi destroy`, or destructive database
operations unless that is the explicit task.

Keep these contracts stable unless you are deliberately migrating them:

- PostgreSQL role name `stitch`;
- PostgreSQL database name `stitch`;
- Kubernetes namespace `stitch`;
- Helm release/object name `stitch`;
- Service port `50051` for gRPC clients;
- Service port `50052` for Twitch webhooks;
- Service port `50053` for tokio-console;
- public webhook hostname in `WEBHOOK_URL`;
- private Tailscale hostname `stitch`;
- Discord channel destination;
- Twitch app/client identity.

One chart detail is easy to miss: the ingress template currently routes to
service port `50052` as a literal number. If you change `WEBHOOK_PORT` in
Pulumi, also verify the chart ingress template and rendered Ingress. Otherwise
the pod may listen on one port while the public route still points at another.

Treat chart path changes as source-of-truth changes. If `path` moves from a
local checkout to a vendored chart, OCI chart, or different directory, preview
the full render and check Deployment, Service, Ingress, ConfigMap, and Secret
names. A chart that renders the same app under different labels can break
Service endpoints or Tailscale/Cloudflare routing.

Treat image changes as application deploys, not cosmetic edits. With `latest`
and `Always`, the live image content can drift without Pulumi seeing a version
change. Pinning an image is safer for incident recovery because it gives you a
real rollback target.

Treat secret rotation as an integration event:

- Twitch client secret rotation can affect API auth and subscription sync.
- Webhook secret rotation can invalidate existing callback signatures until
  subscriptions are recreated or resynchronized.
- Discord token rotation can break message delivery until the bot and channel
  permissions are verified.
- Database password rotation must keep the PostgreSQL role and rendered runtime
  config in agreement.

After an apply, do not stop at a green Deployment. Pick the behavior that the
change could have broken and test that real boundary:

- for gRPC changes, port-forward or use the private hostname and run a client
  list/track operation that is safe for the moment;
- for webhook changes, use Twitch's EventSub test path or a real harmless event;
- for Discord changes, verify a harmless message path into the configured
  channel;
- for database changes, confirm existing tracked channels and recent stream
  history are still present;
- for ingress changes, prove the public callback host reaches the webhook port
  and not the gRPC port.

Stitch is small, but it has sharp edges because most of its correctness lives in
contracts with systems outside Kubernetes. Preserve those contracts, preview
repo-owned changes, and verify one real integration path before calling it done.
