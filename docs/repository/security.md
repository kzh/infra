# Security And Confidentiality

This repository should be useful to read, review, and operate without becoming
a source of credentials or a map of private infrastructure. Security here is
practical: keep secrets in the systems that are meant to hold them, keep
durable behavior in Pulumi, and keep docs and pull requests focused on what an
operator needs to know without publishing private values.

Assume the repository is watched by more eyes than the live cluster, Pulumi
Cloud, ESC, Tailscale, Cloudflare, Notion, Google, GitHub, or any other external
system. If a value would be risky in an issue, PR, generated docs site, or
terminal transcript, it does not belong in git.

## What Must Not Be Committed

Do not commit credentials, even when they look temporary, generated, encrypted
somewhere else, or already rotated. That includes:

```text
API keys, access tokens, bearer tokens, session cookies
OAuth client secrets, webhook secrets, signing secrets
database passwords, generated admin passwords, app bootstrap passwords
private keys, SSH keys, TLS private keys, age/SOPS keys
kubeconfig contents, client certificates, service-account tokens
decrypted Pulumi secret values or ESC values
cloud provider credentials and local provider auth files
```

Also keep private environment details out of git when they are not intentionally
public documentation:

```text
private hostnames, private URLs, tailnet names, private DNS zones
Cloudflare account, zone, tunnel, and token identifiers
Tailscale OAuth client details, device identity, auth keys, tailnet metadata
Pulumi stack outputs that reveal credentials or private service coordinates
Notion database IDs, private page content, private rankings, or copied records
Google Drive file IDs or private document content copied from connected systems
full kubeconfig, ESC, Pulumi, helm, or kubectl output dumps
secret-bearing logs, traces, screenshots, generated reports, and build artifacts
```

Names can be sensitive even when values are not. A Secret name can reveal an
account structure. A dashboard label can reveal a private hostname. A stack
output can expose a route that was intended only for tailnet users. When the
value helps only the local operator, document how to retrieve it locally instead
of committing it.

## Where Secrets Come From

Config in this repo is not only the visible YAML beside a project. A stack can
receive values from Pulumi stack config, ESC environment imports,
`StackReference` outputs, Kubernetes Secrets, Helm-generated Secrets, provider
environment, local CLI auth, and external systems. Audit all of those surfaces
before deciding whether a value is safe to print, export, or document.

| Source | Common risk | Safer handling |
| --- | --- | --- |
| `Pulumi.<stack>.yaml` | Plain config keys drift into secrets. | Use Pulumi secret config for sensitive inputs. Review stack files before committing. |
| ESC imports | Required values are not visible in local YAML. | Treat imported environment values as part of the config contract; do not copy them into docs. |
| `StackReference` outputs | Producer outputs can silently become consumer inputs. | Preserve output names, meaning, and secret-ness. Preview consumers after producer changes. |
| Kubernetes Secrets | Base64 can be mistaken for encryption. | Generate or load values from Pulumi secrets, chart secret settings, or the owning external system. |
| Helm charts | Charts may create admin passwords or embed values in rendered manifests. | Prefer `existingSecret` or secret-backed values when charts support them. Check rendered diffs. |
| Provider environment | Local kube context, cloud auth, and CLI sessions affect previews. | Keep provider auth local. Do not paste provider diagnostics that include credentials or account IDs. |
| Logs and dashboards | Labels, annotations, URLs, and traces can reveal private routing. | Review generated JSON and log snippets before committing or quoting them. |
| External systems | IDs and private content are easy to copy into docs. | Keep external records in the external system; docs describe the boundary and retrieval path. |

If you are unsure whether a value is sensitive, treat it as sensitive until the
owning system or existing public docs make the opposite clear.

## Pulumi And ESC

Pulumi config is part of the infrastructure contract. Sensitive deploy-time
inputs should be read as secrets, generated credentials should stay secret, and
secret outputs should remain secret when consumed by other stacks.

In Pulumi programs, prefer secret-aware APIs for sensitive values:

```python
config = pulumi.Config()
api_token = config.require_secret("apiToken")
```

When a stack generates a credential that humans or another stack need, export it
as a secret output. Do not make it a plain output just to make retrieval easier:

```python
pulumi.export("admin_password", pulumi.Output.secret(admin_password))
```

When one project consumes another project's output, preserve the producer's
contract. A secret database password from the PostgreSQL stack should not become
a plain Helm value, ConfigMap entry, log line, or doc snippet in a consumer
stack. Passing an `Output` directly as a resource input is usually safer than
forcing it into a Python string.

For local operator retrieval, document the command, not the value:

```bash
pulumi stack output <output-name> --stack mx --show-secrets
```

That command is appropriate for a local operator in the target project
directory. Its output is not appropriate for commit messages, PR descriptions,
docs, chat, issue comments, screenshots, or copied terminal transcripts.

When setting secret config, avoid putting the secret value directly in a command
that will be stored in shell history or visible in process listings. Prefer the
interactive prompt or an approved secret-management flow:

```bash
pulumi config set --secret <key>
```

ESC environment imports are first-class config. Do not remove, rename, or ignore
an `environment:` import during cleanup just because the value is not visible in
`Pulumi.<stack>.yaml`. When auditing missing config, ask whether the value is
expected from ESC before changing program behavior.

Use Pulumi secrets for values Pulumi must know at deployment time. Use ESC for
shared or secret-backed environment configuration. Use the owning external
system for credentials that belong there. Do not duplicate the same secret into
multiple places unless the system design requires it.

## Kubernetes Secret Handling

Kubernetes `Secret` objects are not safe to commit as raw manifests merely
because their values are base64 encoded. Base64 is transport encoding, not
encryption.

Prefer these patterns:

```text
Pulumi secret config -> Kubernetes Secret data
generated Pulumi secret output -> Kubernetes Secret data
external system credential -> ESC/Pulumi secret config -> Kubernetes Secret data
chart-supported existingSecret -> Kubernetes Secret reference
```

Avoid these patterns:

```text
committed Secret YAML with real data or stringData
credentials in ConfigMaps
credentials in container args when env or mounted secret files work
tokens in annotations, labels, dashboards, probes, or command strings
plain stack outputs that duplicate Secret contents
debug logs that print environment variables or rendered chart values
```

When using Helm, check whether the chart supports `existingSecret`,
`secretKeyRef`, `extraEnvFrom`, or another secret-reference mechanism. If the
chart only accepts a plain value, make sure the Pulumi value remains secret and
inspect the preview for accidental disclosure. Some providers or charts may
render values into diffs, notes, annotations, or generated resources; do not
paste those sections into PRs.

Use ConfigMaps for non-sensitive configuration only. A value does not become
safe because it is "just an endpoint" if that endpoint identifies a private
service, internal account, or tailnet-only route.

## Stack Outputs And Retrieval

Outputs are APIs for humans and other stacks. Keep them small, intentional, and
classified correctly.

Good outputs:

```text
service name needed by a consumer stack
namespace needed for operational checks
tailnet or public route intentionally documented for users
secret output for a generated password that operators must retrieve locally
```

Risky outputs:

```text
full DSNs that include credentials
entire kubeconfig fragments
raw chart values
private URLs that are not meant for docs
large object dumps where one nested key might be secret
```

If a consumer needs a credential, pass the credential as a secret output and
consume it as a secret input. If a human needs a credential, document the local
retrieval command. If nobody needs a value outside the stack, do not export it.

## Docs, PRs, And Review Notes

Docs and PRs should explain behavior, commands, validation, and operational
impact. They should not be archives of raw output.

Good documentation says:

```text
the stack requires a database password from secret config
the service is exposed through Tailscale
retrieve the generated admin password with a local Pulumi command
preview showed no resource replacements
the UI loaded and the Service had endpoints
```

Bad documentation says:

```text
the actual password, token, client secret, or kubeconfig
the full private URL when the route is not intended to be public
the full Pulumi output object
the full kubectl describe output including annotations and tokens
the full preview log when only a replacement summary is needed
private Notion, Google Drive, or database content copied into the repo
```

When reporting preview or validation results, include the command and a scoped
summary. If a broad preview writes logs under `/tmp`, include the local log
directory and summarize the failure class. Do not paste secret-bearing excerpts.

For redaction, prefer useful placeholders over decorative masking:

```text
<stack>
<namespace>
<secret-name>
<tailnet-hostname>
<private-url>
<account-id>
```

Redact the whole sensitive value. Partial tokens, suffixes, IDs, and hostnames
can still be enough to identify or abuse a resource.

Generated docs deserve the same review as hand-written docs. A service page can
accidentally copy private stack outputs. Dashboard JSON can contain private
labels. Screenshots can reveal account names or URLs. Treat generated content as
untrusted until reviewed.

## External System Boundaries

This repo can describe integrations with external systems, but it should not
become their data store.

For Tailscale, keep OAuth clients, auth keys, device identity, tailnet metadata,
and private route details in Tailscale and secret config. Docs can say that a
service is tailnet-exposed and explain the operator checks without publishing
private tailnet details.

For Cloudflare, keep API tokens, tunnel credentials, account IDs, zone IDs, and
private routing details out of docs and PRs unless a value is intentionally
public. The Pulumi program can own durable tunnel behavior, but credentials
belong in secret config or the external credential store.

For GitHub and container registries, do not commit tokens, package credentials,
workflow secrets, or private registry auth. If image names are documented, make
sure they are meant to be visible.

For Notion, Google Drive, spreadsheets, docs, and other knowledge systems, keep
private records in the source system. If infrastructure docs need to mention an
external workflow, summarize the operational contract. Do not commit database
IDs, page IDs, private document excerpts, personal rankings, or copied content
that only belongs in that system.

For local workstation state, keep kubeconfigs, CLI profiles, browser sessions,
shell history, and provider credentials out of the repository. A command that is
safe to run locally is not automatically safe to publish.

## Safe Local Checks

Before committing or opening a PR, inspect what is actually changing:

```bash
git status --short
git diff --check
git diff --stat
git diff
```

For a staged commit:

```bash
git diff --cached --check
git diff --cached --stat
git diff --cached
```

Manual review matters. Search tools help, but they produce false positives and
miss context. Useful spot checks include:

```bash
rg -n "password|passwd|token|secret|api[_-]?key|client[_-]?secret|private[_-]?key" .
rg -n "BEGIN .*PRIVATE KEY|kubeconfig|certificate-authority-data|client-key-data" .
```

If the worktree is shared with other workers, scope the review to the files you
own before editing or staging. Do not revert, restage, or rewrite unrelated
changes to make your security pass easier.

For code changes, also run the normal repo gates:

```bash
just check-python
just lint
git diff --check
```

For a changed stack, run a targeted preview when the change affects live
behavior:

```bash
just preview pulumi/<area>/<service> stack=mx
```

Do not run `pulumi up`, `pulumi destroy`, or `just up` unless the task
explicitly asks for a live apply or destructive action.

## Incident Handling

If a secret appears in a diff, PR, docs build, issue, chat, log, or generated
artifact, treat it as exposed. Removing the text is necessary, but it is not the
same as restoring trust in the credential.

Use this order:

1. Stop copying the value. Do not quote it again while asking for help.
2. Identify the source and owner: Pulumi config, ESC, Kubernetes Secret,
   Tailscale, Cloudflare, GitHub, database, registry, Notion, Google, or another
   system.
3. Remove the value from the working tree, generated artifacts, docs, PR text,
   comments, and logs where you control them.
4. Rotate or revoke the credential in the owning system. If the value was a
   generated password, generate a replacement through the stack or owning
   service flow.
5. Update Pulumi, ESC, Kubernetes, or the external integration with the new
   secret through the normal secret path.
6. Check for secondary copies: shell history, CI logs, preview logs, docs build
   output, screenshots, pasted support notes, and generated dashboard JSON.
7. If the value was committed or pushed, coordinate history cleanup separately.
   Do not rely on history rewriting as a substitute for rotation.
8. Run the smallest validation that proves the new secret path works, such as a
   targeted preview, pod rollout check, login smoke test, or external API check.
9. Record what happened without repeating the secret: source, exposure surface,
   rotation action, validation performed, and any remaining follow-up.

For private identifiers that are not credentials, rotate may not apply. Still
remove the exposure, check whether the identifier grants access or enables
targeting, and update docs to describe the category rather than the value.

## The Working Rule

Keep secrets in secret stores, keep desired infrastructure in Pulumi, keep
private external records in their external systems, and keep docs focused on
operator knowledge. When a reader needs a sensitive value, give them the safe
local retrieval path, not the value itself.
