# Operators

Operators are Kubernetes controllers that turn higher-level API objects into
running systems. In this repo they are the layer between Pulumi and systems that
need continuous reconciliation: databases, Ray clusters, webhooks, generated
Services, failover behavior, metrics, certificates, and repair loops.

The important thing is that Pulumi and the operator are not doing the same job.
Pulumi declares Kubernetes resources. The Kubernetes API stores those resources.
The operator watches selected resource kinds and keeps lower-level objects in the
shape implied by the custom resource spec.

Most operator-backed systems in this repo have three layers:

```text
Pulumi stack -> custom resource -> controller-created resources
```

Those arrows are ownership boundaries, not just a creation order. A Pulumi
preview can tell you that the stack will update a `Cluster`, `InnoDBCluster`, or
`RayCluster`. It cannot prove that the controller will later create healthy pods.
A custom resource can be accepted by the Kubernetes API and still fail
reconciliation. An operator can be healthy while one custom resource is invalid.
A custom resource can be ready while a consuming app is using the wrong service
name, database, or credential Secret.

[CloudNativePG](/stacks/core/operators/cnpg) is the PostgreSQL operator layer. It matters because many services depend on PostgreSQL either directly or through StackReference outputs from the database stack.

[MySQL Operator](/stacks/core/operators/mysql) is the MySQL operator layer. It matters for applications such as MediaWiki and WordPress, where the app chart and database lifecycle need to stay in agreement.

[KubeRay](/stacks/core/operators/kuberay) is the Ray operator layer. It matters when Ray clusters, Ray jobs, or RayService-style workloads need Kubernetes-native reconciliation instead of hand-managed pods.

## The Three Layers

Treat each layer as a different source of truth.

Pulumi owns the declared resources in the stack: namespaces, Helm releases,
custom resources, Secrets, Services, ingresses, dashboards, monitors, and any
other Kubernetes objects that appear in the program. Pulumi tracks desired state,
resource names, provider inputs, dependencies, and stack outputs. It is the right
place to make durable changes to the objects the repo declares.

The custom resource is the operator contract. Its `spec` is the desired state
the controller is expected to reconcile. Its `status` is the controller's report
about the observed state. Examples in this area include CloudNativePG `Cluster`
resources, MySQL `InnoDBCluster` resources, and KubeRay `RayCluster` resources.

Controller-created resources are the implementation detail the operator manages:
pods, StatefulSets, Deployments, Services, PVCs, certificates, routers,
endpoints, jobs, config, and sometimes generated Secrets. These objects can be
useful to inspect, but direct edits usually do not last because the controller
will reconcile them back to the desired state from the custom resource.

The right fix depends on which layer owns the bad state. If the MySQL router
Service has no endpoints, the app Deployment is probably not the first place to
edit. If a KubeRay worker pod cannot schedule because of resource requests, the
`RayCluster` spec or stack values matter more than a manual pod patch. If a
PostgreSQL app has a wrong connection Secret while the CNPG `Cluster` is ready,
the consumer stack contract is the likely boundary.

## Pulumi Versus Reconciliation

Pulumi creates and updates objects through the Kubernetes API. Once a custom
resource exists, the operator reconciliation loop continues after Pulumi exits.
That has a few practical consequences.

A successful preview only proves what Pulumi expects to change. It does not prove
the controller will accept every field at runtime, that the webhook is healthy,
or that dependent pods will roll cleanly. A successful apply can still be
followed by a custom resource with `Ready=False`.

A failed preview or apply can come from several layers: Pulumi program errors,
provider behavior, Kubernetes schema validation, admission webhooks, missing CRDs,
or a controller that rejects a field. Do not collapse those into one category.
Classify the failure before changing code again.

Helm-installed operators add another boundary. The operator stack may install
CRDs, webhooks, RBAC, controller Deployments, metrics, and dashboards. Consumer
stacks may create CRs from those CRDs. Upgrading the operator can therefore be a
platform migration even when the Pulumi diff looks small. Preview the operator
stack, then preview important consumers when CRD fields, defaulting, conversion,
or controller behavior changed.

Typed CRD bindings are convenience, not ownership. The MySQL page uses generated
Python bindings under `pulumi/lib/mysql_operator_crds` so app stacks can create
typed `InnoDBCluster` resources. Those bindings should be regenerated from CRDs,
not hand-edited. The durable object is still the custom resource in the
consumer stack.

## Reading Custom Resource Status

For operator-backed resources, `spec` says what was requested and `status` says
what the controller has observed. Start with status before editing child pods.

The key fields are usually:

```text
metadata.generation        the current desired-spec generation
status.observedGeneration  the generation the controller has processed
status.conditions[]        named readiness or failure facts
status.phase               a coarse state, if the operator exposes one
status.*                   operator-specific details such as endpoints or roles
```

If `observedGeneration` is behind `metadata.generation`, the controller has not
processed the latest spec yet. A condition from an older generation can be stale.
If the generation is current and a condition is `False` or `Unknown`, read its
`reason` and `message` before looking elsewhere.

Conditions are more useful than a single "healthy" label because they preserve
separate facts. A database cluster can be accepting writes while a backup or
replica condition is failing. A Ray cluster can have a running head pod while
workers are pending. A controller can report webhook, storage, scheduling, image,
or permission problems directly in the condition message.

Useful status commands:

```bash
kubectl get <kind> -n <namespace> <name> -o yaml

kubectl get <kind> -n <namespace> <name> -o jsonpath='{.metadata.generation}{" observed="}{.status.observedGeneration}{"\n"}'

kubectl get <kind> -n <namespace> <name> -o jsonpath='{range .status.conditions[*]}{.type}{"="}{.status}{" reason="}{.reason}{" message="}{.message}{"\n"}{end}'
```

Use `kubectl describe` when you want status and related events together:

```bash
kubectl describe <kind> -n <namespace> <name>
```

Avoid pasting full custom resource YAML into docs or chat when it may include
private hostnames, stack-specific identifiers, or Secret references. Summarize
the condition, reason, and owning layer instead.

## Events And Logs

Events explain what Kubernetes and controllers tried to do recently. They are
especially useful for admission failures, failed scheduling, PVC binding, image
pulls, probe failures, owner-reference deletion, and repeated reconciliation
errors.

Events are namespaced and short-lived. Read them while the failure is happening:

```bash
kubectl get events -n <namespace> --sort-by=.lastTimestamp

kubectl get events -n <namespace> \
  --field-selector involvedObject.kind=<Kind>,involvedObject.name=<name> \
  --sort-by=.lastTimestamp
```

`kubectl describe` also includes recent events for the object being described.
If status tells you the controller is blocked but does not say why, check events
for the custom resource and for the child pod, PVC, or Service named in the
condition message.

Controller logs come after status and events. Logs are best for controller
errors, webhook failures, RBAC denials, reconciliation panics, or unclear
condition messages:

```bash
kubectl logs -n <operator-namespace> -l app.kubernetes.io/name=<operator-name> --tail=200
```

For workload failures, use workload logs instead. A healthy KubeRay operator will
not explain a Python import error inside a Ray task. A healthy MySQL operator
will not explain an application migration job using the wrong database name.

## Ownership And Drift

Before changing a Kubernetes object, ask who will try to put it back.

Pulumi-owned objects should be changed in Pulumi. If a stack declares a Service,
Ingress, Secret, `RayCluster`, `InnoDBCluster`, or CNPG `Cluster`, edit the
stack and preview it. A direct `kubectl patch` creates drift unless it is
immediately backported into the repo.

Operator-owned objects should usually be changed through the custom resource
spec. If the operator created a StatefulSet, pod, router Service, PVC, or
certificate, manual edits may be overwritten. They can still be useful for
emergency diagnosis, but they are not a durable fix.

Some objects are shared contracts. A Service created for ingress, metrics, or a
client API may be read by a different stack. Be extra careful with selectors,
ports, names, and labels. If an operator-generated Service has the wrong selector
or an unstable name, a repo-owned Service that selects the intended pods can be
safer than routing traffic through a controller implementation detail. Preview
that as a normal stack change rather than patching the live Service by hand.

Check ownership from metadata:

```bash
kubectl get <resource> -n <namespace> <name> -o jsonpath='{range .metadata.ownerReferences[*]}{.kind}{"/"}{.name}{" "}{end}{"\n"}'

kubectl get <resource> -n <namespace> <name> -o jsonpath='{.metadata.labels}{"\n"}{.metadata.annotations}{"\n"}'
```

Owner references tell Kubernetes garbage collection and often point back to the
custom resource. Labels and annotations tell you whether Helm, Pulumi, or an
operator is managing the object. Finalizers tell you an operator may need to
perform cleanup before deletion completes.

Do not delete CRDs casually. Removing a CRD can remove the API surface and strand
or delete custom resources depending on how the change is applied. Operator CRD
upgrades should be treated as migrations and checked against consuming stacks.

## A Practical Debugging Loop

Use this loop when an operator-backed system is failing:

1. Identify the owning stack and resource kind.
2. Check the operator pod and webhook health.
3. Read the custom resource `status`, `observedGeneration`, and conditions.
4. Read events for the custom resource and the named child resources.
5. Inspect controller-created pods, Services, endpoints, PVCs, and jobs.
6. Check the consuming stack contract: hostnames, service names, ports, database
   names, Secret names, and StackReference outputs.
7. Decide which layer owns the fix before editing.

Common discovery commands:

```bash
kubectl get crd | rg 'postgres|mysql|ray'
kubectl api-resources | rg 'postgres|mysql|ray'
kubectl get pods -A | rg 'cnpg|mysql|ray|operator'
kubectl get clusters.postgresql.cnpg.io --all-namespaces
kubectl get innodbclusters --all-namespaces
kubectl get rayclusters --all-namespaces
```

Then narrow to the specific namespace and object:

```bash
kubectl describe <kind> -n <namespace> <name>
kubectl get pods,svc,endpoints,pvc -n <namespace>
kubectl get events -n <namespace> --sort-by=.lastTimestamp
```

Do not stop at "the pod is running." For operator-backed systems, a running pod
can still have the wrong Service selector, missing endpoints, an unbound PVC, a
failed init job, a stale condition, or a consumer pointing at the wrong hostname.

## Safe Operator-Backed Changes

A safe change starts by choosing the layer:

```text
operator install or CRDs changed?      change the operator stack
custom resource desired state changed? change the stack that declares the CR
child pod or Service is wrong?         change the CR spec or add a repo-owned contract
consumer cannot connect?               check the consumer stack and exported outputs
```

For operator stacks, run the repo gates and a targeted preview:

```bash
just sync pulumi/core/operators/<operator>
just check-python
just lint
git diff --check
just preview pulumi/core/operators/<operator> stack=mx
```

For a consumer stack that creates the custom resource, preview that consumer too.
For example, a MySQL operator or binding change should be checked against at
least one app stack that creates an `InnoDBCluster`. A CNPG operator change
should be checked against the PostgreSQL data stack. A KubeRay CRD or image
change should be checked against the stack that declares the affected
`RayCluster`.

When CRDs change, regenerate bindings through the repo command instead of editing
generated files:

```bash
just generate-mysql-crds
just generate-kuberay-crds
just check-python
just lint
git diff --check
```

Review previews for replacements, selector changes, Service name changes, PVC
changes, and webhook or CRD diffs. A version bump that looks like a single Helm
release update can still change defaults used by every custom resource.

After an apply, verification should cross the boundary that changed. For an
operator upgrade, verify controller pods, webhooks, CRDs, and at least one
existing custom resource. For a custom resource change, verify status conditions
and the generated pods or Services. For a consumer fix, verify the consuming app
after the operator resource is healthy.

The operator pages focus on concrete stacks, but the rule is the same across all
of them: make the durable change at the layer that owns the desired state, then
verify the next layer down actually reconciled it.
