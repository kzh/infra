# Configuration

Configuration is not a side file for Pulumi. It is one of the inputs that
creates the resource graph.

For any stack in this repository, the deployed shape comes from six inputs
read together:

```text
Pulumi program          __main__.py builds the desired resource graph
project metadata        Pulumi.yaml names the project and Python runtime
stack configuration     Pulumi.<stack>.yaml supplies stack-local values
ESC imports             environment-backed config and secrets
referenced stacks       StackReference outputs from producer stacks
provider environment    kube context, local CLI auth, cloud auth, tailnet access
```

If one of those inputs changes, the stack may change even when the Python code
does not. If one of those inputs is missing, preview may fail before any
Kubernetes resource is planned. Treat config changes with the same care as code
changes: inspect the owner, understand the contract, preview the result, and
keep secrets out of the repository and out of reports.

## Project And Stack Config

Every deployable unit has a `Pulumi.yaml`. In this repo it usually defines the
project name, the Python runtime, and the `uv` toolchain:

```yaml
name: coder
runtime:
  name: python
  options:
    toolchain: uv
```

That `name` matters because `pulumi.Config()` reads keys in the current project
namespace. In the `coder` project, this code:

```python
config = pulumi.Config()
postgres_stack = config.require("postgres_stack")
```

expects a namespaced config key named `coder:postgres_stack`, either in the
stack file or from an imported ESC environment.

Stack-local config lives in `Pulumi.<stack>.yaml` beside the project. A normal
stack file looks like:

```yaml
config:
  coder:namespace: coder
  coder:postgres_stack: kzh/postgresql/mx
  coder:service_type: ClusterIP
  coder:access_url: https://<private-hostname>
```

The left side is the Pulumi config key. The right side is the value that the
program receives. Some values are plain strings because they are service names,
chart versions, database names, storage sizes, or feature toggles. Plain does
not mean public. A private hostname or provider account identifier can still be
sensitive even when it is not encrypted.

Some programs read an explicit namespace:

```python
config = pulumi.Config("n8n")
```

That reads `n8n:*` keys no matter which directory the code lives in. This is
useful when the project name and config namespace need to be explicit, but it
also means missing-config diagnosis has to start at the call site, not at a
guess from the directory name.

Project config in `Pulumi.yaml` applies at the project level. Stack config in
`Pulumi.<stack>.yaml` applies to one stack. In this repo, most operational
inputs belong in the stack file or an ESC import because the repo is
stack-scoped and broad workflows are `mx`-scoped.

## Config Categories

Configuration in this repo usually falls into a few categories.

Identity config names things that other systems remember:

```text
namespace
release name
Kubernetes metadata.name
database name
database user
bucket name
service name
hostname
ingress host
tailnet host
```

Changing identity config can be a migration. A new namespace may create a
parallel copy of an app. A new database name may orphan application state. A
new Service name may break consumers even if pods are ready. A new Pulumi
resource name can cause replacement unless an alias preserves identity.

Version config chooses artifacts and APIs:

```text
Helm chart versions
container image tags or digests
operator versions
CRD versions
application versions
```

Treat version changes as upgrades, not as string edits. Check chart values,
CRD schemas, immutable Kubernetes fields, hooks, release names, and consumer
behavior.

Capacity config changes how much room a service has:

```text
PVC size
storage class
replicas
CPU and memory
worker counts
retention settings
```

Capacity changes can still be stateful. Increasing a PVC is usually safer than
renaming it. Changing a storage class can imply replacement. Reducing replicas
can affect availability or job throughput.

Integration config connects this stack to something else:

```text
StackReference identifiers
webhook URLs
provider client IDs
external service channels
public or private base URLs
database host choices
object storage endpoint choices
```

Integration config often fails at runtime rather than preview time. A pod can
be ready while an external token, callback URL, channel ID, or database host is
wrong.

Secret config contains credentials or material derived from credentials:

```text
passwords
tokens
OAuth client secrets
private keys
database connection strings with credentials
generated admin passwords
access keys
CA material when disclosure would weaken a private trust path
```

Secret config should enter through Pulumi secret config, ESC secrets, generated
secret outputs, or Kubernetes Secrets. It should not be written into docs,
commit messages, shell transcripts, issue comments, or ordinary config values.

## ESC Imports

Some stack files import Pulumi ESC environments:

```yaml
environment:
  - <project>/<environment>

config:
  app:namespace: app
```

An ESC import is part of the stack configuration surface. It can provide
ordinary config, secret-backed config, and environment variables used by tools
that run during preview or apply. Do not audit a stack by reading only the
`config:` block. A required key can be absent from `Pulumi.mx.yaml` because it
comes from `environment:`.

Useful inspection commands:

```bash
cd pulumi/apps/stitch

pulumi config --stack mx
pulumi config env ls --stack mx
pulumi env get <project>/<environment>
```

`pulumi env get` shows the environment definition with secrets masked. Prefer
that for inspection. `pulumi env open` resolves and reveals values, so use it
only in a trusted terminal when the actual secret value is required for an
operation. Do not paste the result anywhere.

If a stack fails before the Python program runs with an environment-opening
error, inspect the import first:

```text
is the environment name spelled correctly?
does the current Pulumi user have access?
was the environment moved, renamed, or deleted?
does the environment define values under values.pulumiConfig?
does the stack import the environment expected by this repo, not another machine?
```

Avoid duplicating the same key in both ESC and stack config unless the override
is deliberate and verified. When both surfaces might define a key, check the
resolved config with `pulumi config --stack <stack>` from the target project
before assuming which value the program will see.

ESC is a good home for shared secret-backed configuration and external
provider credentials. It is not a reason to hide important contracts. If a
stack depends on an ESC-provided key, the code and docs should still make the
required category clear, even when the value itself stays private.

## Secrets

Pulumi has a secret bit. Use it early. A value that enters Pulumi as a secret
is encrypted in state, masked in normal CLI output, and keeps its secrecy
through most `Output` transformations.

Set deploy-time secrets as secrets:

```bash
cd pulumi/core/networking/tailscale

pulumi config set --stack mx --secret tailscale:TS_CLIENT_SECRET
```

Use secret readers in code:

```python
config = pulumi.Config()
client_id = config.require("TS_CLIENT_ID")
client_secret = config.require_secret("TS_CLIENT_SECRET")
```

Use secret outputs for generated or imported credentials:

```python
password = random.RandomPassword("app-password", length=32, special=False)
pulumi.export("password", pulumi.Output.secret(password.result))
```

When a stack reads an existing Kubernetes Secret and exports a decoded field,
wrap the decoded value back into a Pulumi secret output. The PostgreSQL stack
does this because it exports secret-derived connection material for consumers.

When an app needs a secret at runtime, deliver it through a Kubernetes Secret
or the chart's supported secret mechanism. Do not place secret values directly
into ConfigMaps, rendered docs, dashboard JSON, or ordinary environment values.

Good docs show retrieval commands, not values:

```bash
pulumi stack output --stack mx --show-secrets adminPassword
```

Use `--show-secrets` only when the operator actually needs the value locally.
Do not include command output in this docs site or in a final report.

Encrypted `secure:` blobs in `Pulumi.<stack>.yaml` are still configuration
artifacts. Do not hand-write or reformat them. Use `pulumi config set --secret`
or move the secret to ESC through the supported CLI path.

## StackReference Contracts

`StackReference` is how one Pulumi project consumes outputs from another
Pulumi stack:

```python
postgres_stack = pulumi.StackReference(config.require("postgres_stack"))
pg_host = postgres_stack.require_output("rw_service_fqdn")
pg_password = postgres_stack.require_output("password")
```

This is an API boundary. The producer owns output names and meanings. The
consumer relies on those names and meanings. If the producer changes an output
name, changes a service hostname, changes whether a value is secret, rotates a
credential, or changes a database/bucket convention, every consumer may still
compile while its runtime behavior breaks.

The important producer contracts in this repo are:

| Producer project | Common consumers | Contract |
| --- | --- | --- |
| `pulumi/data/databases/postgres` | Coder, LiteLLM, Immich, ConvexDB, MLflow, Trino, Spark, Airflow, Dagster, n8n, Temporal | Kubernetes service coordinates, Tailscale host, namespace, database credentials, CA material, and optional app database/extension setup. |
| `pulumi/data/storage/rustfs` | MLflow, Trino, Spark | S3 endpoint coordinates, namespace, access key, secret key, bucket conventions, and shared Iceberg warehouse access. |
| `pulumi/data/analytics/clickhouse` | Trino | ClickHouse host, port, admin username, and admin password. |

Consumer stack config often chooses the producer stack identifier:

```yaml
config:
  trino:postgresStack: kzh/postgresql/mx
  trino:rustfsStack: kzh/rustfs/mx
  trino:clickhouseStack: kzh/clickhouse/mx
  spark:postgresStack: kzh/postgresql/mx
  spark:rustfsStack: kzh/rustfs/mx
  spark:trinoStack: kzh/trino/mx
```

That identifier is config too. If it points at the wrong org/project/stack, the
consumer may read valid outputs from the wrong place. If the producer stack was
never deployed, the consumer cannot read the contract. If the current Pulumi
user cannot access the producer, the consumer may fail before planning its own
resources.

When changing a producer output:

1. Keep the old output when a compatibility period is useful.
2. Preserve secret-ness for secret values.
3. Preview the producer.
4. Preview every known consumer that reads the changed output.
5. Run a live consumer check after apply if the user asked for an apply.

Passing a secret output into a Kubernetes Secret preserves the right shape:

```python
db_secret = k8s.core.v1.Secret(
    "app-db-credentials",
    string_data={"DB_PASSWORD": pg_password},
)
```

Do not unwrap outputs into local variables or create resources inside
`.apply()`. Pass outputs directly into resource inputs when possible. Use
`.apply()` for value transformation, not for creating the resource graph.

## Stack Files And Git

Stack files are source-controlled inputs, but they are sensitive by default.
Review them as infrastructure code.

Before editing a stack file, read:

```text
Pulumi.yaml
Pulumi.<stack>.yaml
__main__.py
any imported ESC environment names
any producer stack outputs consumed by StackReference
```

New stack files are ignored by default in this repo. Be deliberate before
adding or tracking one. Broad repository workflows are intentionally `mx`-only
unless a task says otherwise, so do not introduce non-`mx` operational scope as
a side effect of a config cleanup.

Prefer the Pulumi CLI for secret edits:

```bash
cd pulumi/apps/example

pulumi config set --stack mx example:storageSize 20Gi
pulumi config set --stack mx --secret example:apiToken
pulumi config rm --stack mx example:obsoleteKey
```

Manual edits are fine for ordinary YAML values when the change is clear and
reviewable. They are not fine for decrypted secrets, generated encrypted
payloads, or values copied from a private terminal.

Config files can also contain values that are not cryptographic secrets but
still deserve care:

```text
private URLs
tailnet hostnames
provider account IDs
OAuth client IDs
internal namespace conventions
database and bucket names that reveal application structure
```

Use placeholders in docs when the exact value is not needed to teach the
contract.

## Missing Config Diagnosis

Missing config is a signal, not noise. Fix the configuration surface that owns
the value. Do not turn a required value into a default just to make preview
continue unless the default is genuinely safe for every stack that can run the
program.

Start by confirming scope:

```bash
cd pulumi/<area>/<project>

pulumi stack ls
pulumi config --stack mx
pulumi config env ls --stack mx
```

Then read the code that failed. Map the call site to the expected key:

```python
config = pulumi.Config()
config.require("namespace")          # <project-name>:namespace

config = pulumi.Config("n8n")
config.require("namespace")          # n8n:namespace

config.require_secret("apiToken")    # same namespace, should be set as secret
```

If the error says a required config variable is missing, ask:

```text
which project directory am I in?
which stack did I select?
what is the project name in Pulumi.yaml?
does the code read the default namespace or an explicit namespace?
is the key local stack config, ESC-provided config, or provider config?
should the value be secret?
was the key renamed from camelCase to snake_case, or vice versa?
does this stack intentionally rely on a default instead?
```

If the failure mentions an ESC environment, inspect the `environment:` block and
the environment definition. A bad ESC import can fail before `__main__.py`
builds the graph.

If the failure is a missing `StackReference` output, debug the producer:

```bash
cd pulumi/data/databases/postgres
pulumi stack output --stack mx
```

Do not use `--show-secrets` for ordinary shape checks. You only need output
names and categories to diagnose most contract drift.

If the failure is a provider connection issue, it may not be stack config at
all. Check the provider environment:

```text
current kube context
KUBECONFIG
local Tailscale reachability
cloud provider auth
Pulumi login and stack access
provider plugin version
```

For example, a PostgreSQL provider that runs during preview may need to reach a
private database host from your machine. A Kubernetes provider may fail because
the kube context is wrong even though every stack key is present.

After identifying the missing layer, make the smallest durable fix:

```text
add the key to Pulumi.<stack>.yaml when it is stack-local and safe to store
add a Pulumi secret config value when it is deploy-time secret material
add or repair an ESC import when the value is shared or centrally managed
repair the producer output when a StackReference contract is broken
repair local provider auth when the program cannot reach the target system
```

## Defaults Versus Required Values

Use defaults for values that are safe, boring, and genuinely conventional for
this repo. Namespaces like `airflow`, chart labels like
`kube-prometheus-stack`, or a local-path storage class can be reasonable
defaults when the stack is designed that way.

Use `require()` or `require_secret()` when guessing could create the wrong
infrastructure or connect the wrong external system. Good required values
include:

```text
external credentials
provider tokens
webhook secrets
database stack identifiers when no default producer is safe
hostnames that external services call back to
namespace names for stacks that should never silently deploy elsewhere
```

Defaults are not a kindness when they hide a mistake. A missing webhook secret,
wrong database stack, or accidental namespace should fail early.

When adding a new config key, decide all of this in one pass:

```text
name and namespace
plain or secret
local stack file or ESC
required or defaulted
effect on resource names and replacements
whether docs should mention the key
which previews prove the change
```

## Safe Change Workflow

For a config-only investigation, stay read-only until you know the owning
surface:

```bash
git status --short
just projects

cd pulumi/<area>/<project>
pulumi config --stack mx
pulumi config env ls --stack mx
```

For a code or stack-config change, run the cheap gates and a targeted preview:

```bash
just check-python
just lint
git diff --check
just preview pulumi/<area>/<project> stack=mx
```

If the changed stack is a producer, preview its consumers too. A PostgreSQL
preview can be green while Airflow or Coder still receives the wrong database
contract. A RustFS preview can be green while Trino or Spark cannot read the
shared Iceberg warehouse.

Look carefully at preview replacements. Config changes that touch these fields
deserve extra scrutiny:

```text
PVC names
database cluster names
Service names used by other stacks
Secret names and keys
Helm release names
operator custom resource names
ingress hostnames
bucket names
database names and users
```

Use aliases or staged migrations when preserving state matters. Do not rely on
`depends_on` to repair a config contract. Dependency edges order resources;
they do not make a wrong hostname, secret name, or stack output correct.

Do not run `pulumi up`, `pulumi destroy`, or `just up` unless the task
explicitly asks for a live apply or destructive action. A configuration guide,
preview, or diagnosis should stop at evidence unless live change is requested.

## Reporting Config Work

A useful config report says what was actually checked:

```text
project and stack
config file or ESC import inspected
whether secrets were only checked for presence
producer StackReference outputs checked
preview command run
consumer previews or live checks still needed
```

Avoid secret-bearing excerpts. Prefer summaries:

```text
required secret config is present
ESC import is missing
producer stack no longer exports the expected output
consumer still points at the old stack identifier
preview shows a Service replacement from a name change
```

Configuration is where small strings become real infrastructure. Give those
strings the same respect as the Python that reads them.
