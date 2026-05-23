# Application Services

Application stacks are where cluster infrastructure becomes a product surface. A person does not experience a Deployment, a Service, or a Helm release; they experience a workspace that starts, a wiki page that loads, a photo library that still has its albums, an observability trace that appears where they expect it, or a short link that resolves when they need it.

That is the right starting point for this part of the repo. The first question is not "is Kubernetes green?" It is: can the service do the job it exists to do, using the state and dependencies it is supposed to use?

The app guides are written from that outside-in view. Start with the human workflow, then walk inward through access, routing, Service endpoints, pods, volumes, databases, external providers, and finally the Pulumi program. A healthy pod is useful evidence, but it is not the whole answer.

## What Makes An App Stack Different

Core stacks usually provide shared machinery. Data stacks usually preserve durable systems or run computation. App stacks sit at the boundary where those lower layers become a concrete experience.

That boundary makes them easy to misread. An app can be "up" while the thing people need is broken. Coder can serve its UI while workspace pods fail. Langfuse can load its UI while trace ingestion or storage is broken. MediaWiki can run PHP while a migration Job or MySQL router is the real blocker. Stitch can have a healthy pod while Twitch, Discord, or webhook configuration is wrong outside the cluster.

For app work, think in terms of contracts:

- The access contract: what URL, tailnet name, ingress, tunnel, or port-forward should a person or client use?
- The behavior contract: what action proves the service works, beyond a pod becoming ready?
- The state contract: which PVCs, databases, generated secrets, token stores, uploaded files, or runtime directories must survive?
- The dependency contract: which operators, databases, routes, external APIs, credentials, or callbacks does the app rely on?
- The code contract: which Pulumi project owns the durable shape of the service?

Those contracts are often more important than any one Kubernetes object. A Service rename, model alias rename, hostname change, database identity change, PVC replacement, or callback URL change can break users even when preview looks small.

## State Versus Runtime

The most useful distinction in application stacks is replaceable runtime versus durable state.

Runtime is the part you can usually recreate: Deployments, Pods, ReplicaSets, Services, Ingresses, probes, ConfigMaps that are generated from code, dashboards, and most chart-rendered objects. Runtime should still be changed carefully, but a failed rollout is usually recoverable by fixing code, rolling back the image, or restoring the previous chart behavior.

State is the part the application exists to protect or accumulate. For these app stacks, that includes things like:

- Coder's PostgreSQL metadata and any workspace storage created by templates.
- Hermes' `hermes-data` PVC, including Codex home, provider setup, browser state, and runtime files.
- Langfuse's PostgreSQL, ClickHouse, Valkey, RustFS objects, and generated application secrets.
- Immich's media library PVC and PostgreSQL metadata.
- MediaWiki's MySQL cluster, page revisions, generated config, and uploaded images PVC.
- WordPress' MySQL cluster, `wordpress-data` PVC, uploads, themes, plugins, and site settings.
- golink's SQLite database and tsnet identity on the PVC.
- Stitch's PostgreSQL state and external integration credentials.

Do not treat those as incidental implementation details. They are the continuity of the service. If a chart upgrade, image change, cleanup, or namespace move touches state, slow down and identify exactly what must be preserved before changing code.

The recovery question is simple and revealing: if the new pod came up empty, what would be missing? If the answer includes user files, page history, uploaded media, model tokens, workspace records, integration credentials, or tailnet identity, you are touching durable state.

## Debug From The User Inward

Application debugging works best as a layered walk from symptom to owner.

First, reproduce or define the user-visible failure. A vague "the app is down" hides useful differences. Is the browser getting a DNS failure, a TLS error, a 404, a 502, a login loop, a blank page, a provider error, a failed upload, a missing workspace, or stale data? Each one points at a different layer.

Then inspect the access path. Apps in this repo may be reached through Tailscale ingress, Cloudflare Tunnel, tsnet, a private service name, or a local port-forward. Make sure the name or URL is the one this stack actually publishes. If a service is private, test from a device that should have access to that private path.

Then inspect routing and selection. In Kubernetes, a route can be correct while its backend has no endpoints. That usually means the Service selector, pod labels, readiness, or namespace is wrong; editing ingress first will not help.

Useful first pass:

```bash
cd pulumi/apps/<service>

pulumi stack output --stack mx
kubectl get pods,svc,endpoints,ingress,pvc -n <namespace>
kubectl describe ingress -n <namespace> <name>
kubectl get endpoints -n <namespace> <service>
```

After that, inspect the application process. Logs should be read with the service model in mind. For a content app, database and filesystem errors matter. For Langfuse, ingestion, ClickHouse, object storage, and worker errors matter. For Stitch, a webhook that never arrives is different from one that arrives and fails validation. For Hermes, gateway, dashboard, and browser sidecar failures are separate paths.

Then inspect durable state and dependencies. Check PVC mounts, database readiness, install/update Jobs, operator-owned custom resources, token storage, and external provider configuration. Do not print secret values into notes or chat just to prove they exist; presence, names, references, and error messages are usually enough.

Only after the live failure is classified should you edit Pulumi. Durable fixes belong in the repo, but not every incident starts as a code bug. Sometimes the right next step is to refresh state, inspect an operator resource, restart a stuck pod, confirm a missing config key, or identify drift between Pulumi state and the cluster.

## How To Use The App Guides

Use each app page as an operating guide, not as a static inventory. The page should help you answer four questions quickly:

1. What is this service for?
2. What does this repo create for it?
3. Where does its state live?
4. What proves it works after a change?

Read the guide before editing the stack. The individual pages call out details that are easy to lose in the generic Kubernetes view: Coder's split between control plane and workspace plane, Hermes' persistent runtime directory, Langfuse's trace and object-store state, Immich's paired media and database state, MediaWiki's install/update Jobs, WordPress' UI-managed plugin and upload drift, golink's tsnet identity, and Stitch's external integration boundary.

When a page says to test a real workflow, take that literally. For app stacks, a preview plus pod readiness is only the infrastructure half of verification. The product half is the behavior:

- Create and reconnect to a Coder workspace.
- Run a Hermes provider test and check optional browser support if enabled.
- Open Immich and verify existing media plus a small upload.
- Read, edit, and upload or view media in MediaWiki.
- Load WordPress, log in, view content, and confirm uploads still work.
- Open an existing golink and create a temporary test link.
- Exercise one Stitch webhook or provider path that crosses the real external boundary.

If a guide and live state disagree, trust the live state for diagnosis and then update the repo or docs if the difference is durable. The page is there to orient you; `__main__.py`, `Pulumi.yaml`, stack config, Pulumi state, and the cluster tell you what is true right now.

## The Services In This Area

[Coder](/stacks/apps/coder) is the workspace platform. Debug it as two related systems: the web control plane and the workspace plane it creates. A reachable UI does not prove templates, workspace namespace permissions, agents, PVCs, or editor access work.

[Hermes](/stacks/apps/hermes) is a persistent agent runtime. Treat it like a small remote workstation in Kubernetes: preserve the PVC, use the exported setup and validation commands, and separate gateway, dashboard, Codex, provider, and browser-sidecar failures.

[Langfuse](/stacks/apps/langfuse) is the LLM observability surface. Treat traces, prompt data, ClickHouse analytics storage, and object uploads as durable state, not as disposable chart internals.

[Immich](/stacks/apps/immich) is a personal media library. The app is only healthy if the UI, media PVC, PostgreSQL metadata, background processing, and uploads agree. Database-only or PVC-only backups are incomplete.

[MediaWiki](/stacks/apps/mediawiki) is a database-backed wiki with generated configuration, install/update Jobs, MySQL operator storage, and uploaded files. Check MySQL and Jobs before treating PHP or ingress as the root cause.

[WordPress](/stacks/apps/wordpress) is a CMS with both database state and filesystem state. Decide whether themes and plugins are UI-managed or repo-managed before changing the deployment model, because that decision determines where the source of truth lives.

[golink](/stacks/apps/golink) is a private short-link service over tsnet. Its SQLite database and tailnet identity live together on the PVC, so preserving storage preserves both links and the service identity people remember.

[Stitch](/stacks/apps/stitch) is an integration service. Kubernetes runs the middle of the system, but Twitch, Discord, webhook URLs, shared secrets, Cloudflare routing, and PostgreSQL shape whether it actually works.

## Commands Worth Keeping Nearby

Use commands to answer specific questions. Avoid running a broad command just because it is familiar.

Preview the repo-owned change:

```bash
just sync pulumi/apps/<service>
just preview pulumi/apps/<service> stack=mx
```

Run the cheap repo checks after editing code:

```bash
just check-python
just lint
git diff --check
```

Inspect the common Kubernetes layers:

```bash
kubectl get pods,svc,endpoints,ingress,pvc -n <namespace>
kubectl describe pod -n <namespace> <pod>
kubectl logs -n <namespace> deploy/<deployment> --tail=200
kubectl get events -n <namespace> --sort-by=.lastTimestamp
```

For operator-backed apps, include the custom resources and Jobs the guide names. For external integrations, include the provider's own test tools or a harmless request through the real callback path. For stateful content apps, include a small read/write workflow that proves existing data survived.

## Change Discipline

Most app fixes should end in the Pulumi project that owns the service. One-off cluster changes can help prove a theory, but they are easy to lose on the next reconcile or restart. Once you know the durable fix, put it in the repo and preview the stack.

At the same time, do not edit Pulumi before you know which layer failed. A 502 with zero Service endpoints is not an ingress problem. A MediaWiki deployment waiting on MySQL is not fixed by changing PHP first. A missing golink tailnet identity may be storage or auth state, not a Service issue.

Protect state first. Preserve names and selectors unless you intend a migration. Treat hostnames, model aliases, database identities, PVC names, generated Secrets, callback URLs, and tailnet identities as contracts. When those contracts must change, make the migration explicit and verify the real user workflow after the infrastructure looks healthy.
