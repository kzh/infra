# Pulumi Model

Pulumi is the control plane for this repository. Each directory under `pulumi/<area>/<service>/` is a small Python program that compiles into a Pulumi resource graph for one independently managed stack. Python is the language used to describe the graph; the graph is the object Pulumi previews, stores in state, and reconciles against Kubernetes and external APIs.

That distinction is the foundation for every safe change in this repo:

```text
Python execution -> Pulumi resource graph -> provider operations -> live infrastructure
```

The Python file can branch, call helper functions, read local dashboard JSON, import generated CRD classes, and assemble dictionaries. None of that is infrastructure until a Pulumi resource constructor runs. Once a constructor runs, Pulumi records a resource with an identity, inputs, outputs, provider, options, dependencies, and eventually a live object behind it.

## Projects And Stacks

The repo is a monorepo, but not a single Pulumi program. `just projects` lists many independent projects: core operators and networking, data services, applications, and monitoring. Each project has its own `Pulumi.yaml`, `pyproject.toml`, `uv.lock`, and `__main__.py`. The `Pulumi.yaml` project name, the stack name, the resource type, and the resource's logical name all participate in Pulumi identity.

Most local work targets the `mx` stack. Some projects also read config from Pulumi ESC through `environment:` imports, so the complete configuration surface is not necessarily visible in the local stack YAML. Treat project config, ESC imports, Pulumi Cloud stack config, kube context, and stack outputs as part of one model.

The root wrappers are deliberately thin:

```bash
just sync pulumi/data/workflow/airflow
just preview pulumi/data/workflow/airflow stack=mx
just check-python
just lint
```

Do not use a broad command when the change is local to one stack. Do not apply unless the task explicitly calls for an apply.

## Resources

A resource is any object Pulumi tracks in state. In this repo the common resource families are:

- Kubernetes resources such as `Namespace`, `Secret`, `PersistentVolumeClaim`, `Service`, `Ingress`, `Deployment`, `ConfigMap`, and monitoring CRDs.
- Helm resources such as `k8s.helm.v3.Release`, `k8s.helm.v3.Chart`, and `k8s.helm.v4.Chart`.
- Generated CRD resources such as `SparkConnect`, `InnoDBCluster`, `ProxyClass`, `PodMonitor`, and `ServiceMonitor`.
- External provider resources such as PostgreSQL roles, databases, and extensions.
- Generated values such as `random.RandomPassword`, `random.RandomBytes`, TLS keys, and certificates.

The first positional argument to a resource constructor is the Pulumi logical name:

```python
namespace = k8s.core.v1.Namespace(
    "airflow-namespace",
    metadata=k8s.meta.v1.ObjectMetaArgs(name=namespace_name),
)
```

`"airflow-namespace"` is Pulumi identity. `metadata.name` is Kubernetes identity. They are related, but they are not the same thing. Pulumi can track a resource named `"airflow-namespace"` whose live Kubernetes object is named `"airflow"`. Changing either name can matter, and changing both at once is rarely a harmless cleanup.

Pulumi's dependency graph is built from resource inputs and outputs, provider references, parent relationships, and explicit `depends_on`. Python order helps readability, but Python order alone is not the dependency model. This is why resources often pass `namespace.metadata.name`, `secret.metadata.name`, or database role names directly into later resources instead of copying strings by hand.

```python
db_secret = k8s.core.v1.Secret(
    "app-db-credentials",
    metadata=k8s.meta.v1.ObjectMetaArgs(
        name="app-db-credentials",
        namespace=namespace.metadata.name,
    ),
    string_data={
        "username": db_role.name,
        "password": db_password.result,
    },
    opts=pulumi.ResourceOptions(depends_on=[namespace, database]),
)
```

Here Pulumi can see both value dependencies and an explicit ordering dependency. The explicit `depends_on` is useful when a provider cannot infer the relationship from values alone, or when a chart/operator needs one object to exist before another object is submitted. It should not replace passing outputs as inputs.

## Names

Names are state boundaries.

Pulumi logical names become URNs. A URN includes the stack, project, parent path, resource type, and logical name. Rename a Pulumi resource and Pulumi may plan a delete plus create even if the live Kubernetes `metadata.name` is unchanged.

Kubernetes names are live API identities. Rename `metadata.name` for a `Service`, `Secret`, `PVC`, `Ingress`, custom resource, or Helm-rendered object and Kubernetes usually sees a different object. That can break DNS names, mounted secrets, selectors, operator ownership, or retained storage.

Helm release names and chart naming controls are another naming layer. Values such as `name`, `fullnameOverride`, `resource_prefix`, and chart-specific naming options determine the Kubernetes objects Helm renders. Changing them can orphan the old objects, collide with same-name objects, or make Pulumi believe a chart moved from one ownership model to another.

Selector names are operational contracts. A `Service` is only useful if its selector matches ready Pods. An `Ingress` is only useful if it targets a `Service` with endpoints. This repo has had real cases where healthy Pods still sat behind a failing URL because the backend `Service` selector pointed at old labels. When a stack creates a repo-owned service in front of operator-owned Pods, keep the selector tied to labels the operator actually places on the current Pods.

```python
connect_server_selector = {
    "spark-role": "connect-server",
    "spark-version": SPARK_VERSION,
    "sparkoperator.k8s.io/connect-name": connect_name,
    "sparkoperator.k8s.io/launched-by-spark-operator": "true",
}

k8s.core.v1.Service(
    "spark-connect-ui-service",
    metadata=k8s.meta.v1.ObjectMetaArgs(name=f"{connect_name}-ui"),
    spec=k8s.core.v1.ServiceSpecArgs(
        type="ClusterIP",
        selector=connect_server_selector,
    ),
)
```

That selector is part of the service contract. If `SPARK_VERSION` changes, the selector and the Pods must move together, and a preview alone is not enough to prove endpoints will exist. Check the rendered labels or live endpoints when selectors are part of the change.

## Outputs

Stack outputs are APIs between Pulumi projects and between Pulumi and humans. They are not just display fields.

PostgreSQL exports connection coordinates, service names, CA material, usernames, and passwords. Airflow, Dagster, Coder, Temporal, Convex, and other consumers read those outputs instead of duplicating the database stack's internals. Applications export URLs, hostnames, chart versions, PVC names, service names, generated admin users, and sometimes generated secrets.

Changing an output name, type, secrecy, or meaning is a breaking API change. Prefer additive changes:

```python
pulumi.export("rw_service_fqdn", rw_service_fqdn)
pulumi.export("port", secret_decoded_secret_field(pg_secret, "port"))
pulumi.export("password", secret_decoded_secret_field(pg_secret, "password"))
```

If a consumer needs a new shape, add a new output first, migrate consumers, preview the dependent stacks, then remove the old output only when there are no remaining references.

Secrets remain secret only if the value is secret in the Pulumi graph. Use `config.require_secret`, provider-generated secret outputs, or `pulumi.Output.secret(...)` when a value should not appear in plaintext state or CLI output.

```python
access_key = random.RandomPassword("rustfs-access-key", length=20, special=False)

pulumi.export("access_key", pulumi.Output.secret(access_key.result))
```

Do not paste secret output values into docs, logs, commit messages, PR text, or chat. It is fine to document that an output exists and how to retrieve it locally.

## StackReferences

`StackReference` is how one project consumes another project's output contract.

```python
postgres_stack_ref = config.get("postgresStack", "kzh/postgresql/mx")
postgres_stack = pulumi.StackReference(postgres_stack_ref)

postgres_service_host = postgres_stack.require_output("rw_service_fqdn")
postgres_password = postgres_stack.require_output("password")
```

The reference reads the last known outputs of another stack. It does not update the producer stack, and it does not prove the producer stack is currently healthy. If the producer output is missing, renamed, plaintext when a secret is expected, or semantically changed, the consumer can fail at preview time or deploy with the wrong assumptions.

Use StackReferences for stable cross-stack facts:

- service DNS names and ports
- database host/user/password outputs
- generated CA material
- namespaces and service names
- URLs or hostnames intentionally exported by another stack

Avoid using StackReferences to reach through an abstraction. If a consumer needs to know a Kubernetes object name, the producer should export the intended name. The consumer should not reconstruct it from the producer's implementation details unless the name is a documented convention.

When changing a producer output, search consumers directly:

```bash
rg 'require_output\("rw_service_fqdn"\)|StackReference' pulumi -g '__main__.py'
```

For a real migration, preview the producer and each consumer. `just preview-all` can help after targeted checks, but the first pass should be the stacks whose contract changed.

## Outputs And `apply`

Pulumi `Output[T]` is a future value with dependency and secrecy metadata. You can pass an `Output[str]` to most resource inputs that want a string. Pulumi will keep the dependency.

Good:

```python
airflow_chart = k8s.helm.v3.Release(
    "airflow",
    namespace=namespace.metadata.name,
    values={
        "data": {
            "metadataConnection": {
                "host": postgres_stack.require_output("rw_service_fqdn"),
                "user": airflow_role.name,
                "pass": database_password.result,
            },
        },
    },
)
```

Use `Output.concat`, `Output.format`, `Output.all(...).apply(...)`, or `.apply(...)` when you need to transform values:

```python
pg_connect_addr = pulumi.Output.format(
    "{}:{}",
    pgref.require_output("rw_service_fqdn"),
    pgref.require_output("port"),
)

local_settings_php = pulumi.Output.all(
    wiki_name,
    mediawiki_url,
    mysql_service_host,
    mediawiki_db_password.result,
).apply(render_local_settings)
```

The transformation should produce a value. It should not create resources. Creating resources inside `apply` hides graph shape from preview whenever the value is unknown, makes ordering harder to reason about, and can produce resources Pulumi cannot plan clearly before deployment.

Risky:

```python
some_output.apply(lambda value: k8s.core.v1.Secret("late-secret", ...))
```

If the number or kind of resources depends on configuration, make that configuration a normal Python value from `pulumi.Config()` when possible. If the value is only known at deployment time, step back and design a stable resource shape that accepts the output as an input.

`apply` is appropriate for formatting, encoding, filtering, and small deterministic conversions:

```python
def base64_pem(value: pulumi.Output[str]) -> pulumi.Output[str]:
    return value.apply(lambda pem: base64.b64encode(pem.encode()).decode())
```

If any input to an `Output.all(...).apply(...)` is secret, Pulumi tracks the result as secret. Keep that property intact by passing the output through the graph instead of unwrapping it into logs or local files.

## Providers

Providers are graph nodes too. The default Kubernetes provider comes from the selected Pulumi stack environment and kubeconfig. Some stacks also create explicit providers for APIs outside Kubernetes.

PostgreSQL consumers are the main pattern:

```python
admin_provider = pg.Provider(
    "pg-admin",
    host=postgres_stack.require_output("ts_hostname"),
    port=5432,
    username=postgres_stack.require_output("username"),
    password=postgres_stack.require_output("password"),
    database="postgres",
    sslmode="disable",
)

airflow_database = pg.Database(
    "airflow-database",
    name=database_name,
    owner=airflow_role.name,
    opts=pulumi.ResourceOptions(provider=admin_provider),
)
```

The `pg.Database` resource is managed by the PostgreSQL provider, not the Kubernetes provider. Its dependency on the database stack flows through provider inputs and explicit `depends_on` where needed. Renaming the provider, changing its database, or changing the host output can affect every resource bound to it, so treat provider edits as graph edits, not connection-string cleanup.

Provider configuration can depend on stack outputs. That is fine. The provider still has to be able to connect during preview or apply for operations that require live reads. A failure here may be a network, Tailscale, credential, ESC, or producer-stack problem rather than a syntax problem in the consumer.

## Helm

Helm is common here, but there is no single Helm model.

`k8s.helm.v3.Release` asks Helm to install a release. It is useful when release semantics, hooks, `wait_for_jobs`, `cleanup_on_fail`, or chart-managed lifecycle behavior matter.

`k8s.helm.v4.Chart` renders chart resources into Pulumi-managed Kubernetes resources. It makes rendered objects more visible to Pulumi and works well for many operators and apps, but it changes ownership boundaries compared with a Helm release.

Older `k8s.helm.v3.Chart` usage still exists in monitoring. Treat any move among these APIs as a migration. It can change resource URNs, ownership, wait behavior, release naming, and same-name conflict behavior.

Helm upgrades in this repo should be read as infrastructure migrations:

```text
read chart release notes and values changes
check rendered object names and selectors
check CRD ownership and install order
check hooks, Jobs, and wait behavior
preview for replace/delete/same-name conflicts
verify services, endpoints, and app behavior after an approved apply
```

Some chart resources have deliberate transforms or options. Monitoring marks specific generated Jobs and ConfigMaps with `delete_before_replace` because same-name generated resources can block replacement. PostgreSQL adds a `pulumi.com/waitFor` annotation because generic readiness is not enough for CNPG cluster health. Vault uses `skipAwait` in places where the provider's wait behavior is noisy for that chart. These are not decorative. Remove them only after reproducing the original problem or proving the provider/chart no longer needs them.

CRDs have two separate concerns:

- Installing CRDs belongs to the operator or chart stack that owns those CRDs.
- Using CRDs from Python should use the generated packages under `pulumi/lib/*_crds`.

Do not hand-edit generated CRD packages. Regenerate them with the repo targets, then run the Python and lint checks.

## Kubernetes Selectors

Kubernetes selectors connect independently managed objects. Pulumi will happily create a `Service` whose selector matches no Pods, because that is a valid Kubernetes object. The failure appears at runtime as no endpoints, 502s, or unreachable dashboards.

For Services and ServiceMonitors, inspect both sides of the relationship:

- The selector in the Pulumi program.
- The labels placed on Pods or Services by the chart, operator, or Deployment.
- The namespace used by the selector or monitor.
- The backend name used by an Ingress.

Deployment selectors and StatefulSet selectors are especially sensitive because many selector fields are immutable. Changing them can force replacement or leave old ReplicaSets and Pods behind. For app-owned Deployments, keep a small stable selector set and put extra descriptive labels outside the selector when possible.

For operator-owned Pods, prefer selectors based on documented operator labels or labels you set in the custom resource template. If an operator owns a Service and its selector proves unreliable for this repo's ingress path, a repo-owned `ClusterIP` Service with an explicit selector can be safer than depending on the generated Service.

## Replacement Risk

Pulumi previews are the main place to catch replacement risk. The dangerous lines are not only deletes; `replace` means the old resource will be destroyed and a new one will be created, with all the Kubernetes and data-plane consequences that implies.

Low-risk replacements are usually stateless, recreated quickly, and not referenced by stable external names. Higher-risk replacements include:

- `PersistentVolumeClaim` and any storage-backed workload
- database clusters, roles, databases, and generated credentials
- Kubernetes `Secret` objects consumed by Pods or operators
- `Service` objects with stable DNS names, Tailscale exposure, or Ingress backends
- `Ingress` objects that own external hostnames or certificates
- Helm releases and chart-rendered resources with release ownership
- CRDs and custom resources managed by operators
- Namespaces, service accounts, and RBAC used by many resources

`delete_before_replace=True` is not a general safety setting. It is useful when Kubernetes will reject create-before-delete because an object with the same live name already exists, such as some chart-rendered Jobs or immutable generated objects. On stateful objects, delete-before-replace can be the dangerous option.

`pulumi.com/skipAwait` changes waiting, not desired state. It can avoid provider hangs or noisy readiness checks, but it does not make a resource healthy and it does not preserve data.

`ignore_changes` hides drift from Pulumi for selected fields. Use it narrowly and only when another controller legitimately owns that field. Broad ignores make previews less trustworthy.

## Safe Refactors

A safe refactor preserves identity unless the migration intentionally changes identity.

Start by deciding what kind of change you are making:

- Pure Python cleanup: preserve every logical name, `metadata.name`, provider name, chart release name, selector, and output.
- Output contract change: add first, migrate consumers, then remove old names later.
- Resource logical rename: add an alias for the old Pulumi name.
- Parent/component move: add aliases that point to the old parent path.
- Kubernetes object rename: stage the live migration; a Pulumi alias cannot rename the Kubernetes API object in place.
- Helm API or release rename: treat as an ownership migration and expect CRD, hook, and same-name conflict checks.
- Selector change: verify the selected Pods or Services, not just the Pulumi preview.

For Pulumi logical renames, aliases preserve state identity:

```python
k8s.core.v1.Service(
    "spark-connect-ui-service",
    metadata=k8s.meta.v1.ObjectMetaArgs(name="spark-connect-ui"),
    opts=pulumi.ResourceOptions(
        aliases=[pulumi.Alias(name="old-spark-ui-service")],
    ),
)
```

For a Kubernetes object rename, use a staged migration instead. Create the new object, switch consumers such as Ingresses or StackReference consumers, verify traffic, then remove the old object in a later change. A Pulumi alias can tell Pulumi that a logical resource moved; it cannot make Kubernetes mutate `metadata.name`.

If introducing `ComponentResource` classes later, give every child `ResourceOptions(parent=self)` and use aliases when moving existing resources under the component. Otherwise Pulumi sees a parent-path change as a different URN.

Before editing, inspect the current worktree and the current stack model. For normal code changes, the minimum local checks are:

```bash
just check-python
just lint
git diff --check
```

For a changed stack, also run a targeted preview:

```bash
just preview pulumi/<area>/<service> stack=mx
```

When a preview fails, classify the blocker before changing more code: missing config, bad ESC import, live-state drift, provider connectivity, chart behavior, or a real program bug. That classification matters in this repository because live infrastructure, Pulumi state, ESC, Tailscale, and Kubernetes operators can drift independently of the Python code.

The safest Pulumi change is not the smallest diff; it is the diff whose graph effect is understood.
