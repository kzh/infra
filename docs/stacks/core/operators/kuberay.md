# KubeRay

Source: `pulumi/core/operators/kuberay`

KubeRay is the Kubernetes operator layer for Ray. Ray is the distributed Python
runtime: a Python driver submits tasks, actors, jobs, data pipelines, or Serve
applications, and Ray places that work on a cluster of processes. Kubernetes is
the substrate that starts pods, attaches networking, applies resource requests,
and restarts containers. KubeRay is the bridge between those two worlds.

The most useful first principle is that Ray and Kubernetes have different ideas
of "the cluster." Kubernetes sees pods, Services, ingresses, labels, endpoints,
and custom resources. Ray sees a head node, worker nodes, CPUs, memory, object
store capacity, actors, tasks, placement groups, jobs, and Serve deployments.
KubeRay keeps those views aligned by watching `RayCluster` resources and
reconciling the Kubernetes objects needed to run a Ray cluster.

This repo's KubeRay stack is not only an operator install. It also creates a
small `ray-dev` cluster that can be used immediately for smoke tests,
experiments, dashboard inspection, and lightweight distributed Python work. That
makes the stack a platform controller and a small runtime surface at the same
time.

## What This Stack Owns

The Pulumi project is named `kuberay`. It uses Python 3.12, `uv`, the Kubernetes
provider, the generated KubeRay CRD package under `pulumi/lib/kuberay_crds`, and
the generated monitoring CRD package for `PodMonitor`.

The program declares these main resources:

```text
kuberay-operator namespace
KubeRay operator Helm chart
ray-dev namespace
ray-dev RayCluster
ray-dev dashboard Service
ray-dev Ray Client Service exposed through Tailscale
ray-dev dashboard Ingress through the Tailscale ingress class
ray-dev PodMonitor
Grafana dashboard ConfigMaps in the monitoring namespace
```

The defaults in `__main__.py` are intentionally small:

```text
Operator namespace:       kuberay-operator
KubeRay chart version:    1.6.1
Ray namespace:            ray-dev
RayCluster name:          ray-dev
Ray version:              2.55.1
Ray image:                ghcr.io/kzh/ray:2.55.1-py3.13.13-uv-amd64@sha256:beeac3a95f854b75dbc2c2dc579b98cd1fbf67054ec6a58135a572fa176564fe
Worker group:             dev-workers
Worker replicas:          1 fixed replica by default
Head resources:           500m CPU / 768Mi request, 1000m CPU / 2Gi limit
Worker resources:         250m CPU / 512Mi request, 500m CPU / 768Mi limit
Ray task memory:          1073741824 bytes on head, 402653184 bytes on workers
Object store memory:      100000000 bytes on head and workers
Dashboard port:           8265
Ray Client port:          10001
GCS port:                 6379
```

Stack config can override the namespace, chart version, Ray namespace, cluster
name, Ray image, Ray version, worker replica count, dashboard host, client API
hostname, and the Prometheus/Grafana values that the Ray dashboard uses for
monitoring links. The default Ray image is built from this stack's
`images/ray-uv/Dockerfile` with the `mx0` BuildKit builder and installs Ray into
Python 3.13.13 with `uv`. Do not copy private hostnames or full stack output
values into docs. Read them from Pulumi when operating the stack.

The KubeRay Helm chart is installed from the upstream KubeRay Helm repository.
The chart values enable the operator's Prometheus `ServiceMonitor` and select
the repo's kube-prometheus-stack release label. Separately, this stack creates a
`PodMonitor` for Ray node pods in `ray-dev`, so the Ray workload metrics and the
operator metrics are two related but distinct scrape paths.

The generated CRD bindings in this checkout currently cover `RayCluster` only.
That is important when thinking about jobs and Serve: this stack declares a
`RayCluster`; it does not currently declare repo-owned `RayJob` or `RayService`
custom resources.

## Ray Mental Model

A Ray application starts with a driver. The driver is the Python process that
calls `ray.init()`, defines remote functions or actors, submits work, and waits
for results. In local development the driver might be your laptop. In a Ray Job,
the driver runs in the cluster. In Ray Serve, the Serve application runs on the
cluster and exposes request-handling replicas managed by Ray.

The Ray head node is the control plane for a Ray cluster. It runs cluster
coordination services, accepts client connections, hosts the dashboard, exposes
the Jobs API, and participates in scheduling. The worker nodes run tasks and
actors. A tiny workload may run entirely on the head, but a useful distributed
workload should be understood as:

```text
driver submits work -> head coordinates -> workers execute -> object refs move results
```

Ray tasks are remote functions. They are good for stateless parallel work:
mapping over inputs, fan-out/fan-in computation, distributed data transforms,
and short units of work that can be retried.

Ray actors are stateful remote Python objects. They are good when work needs
state that lives across method calls: model replicas, connection pools, caches,
coordinators, or long-lived workers.

Ray's object store holds values produced by tasks and actors so other tasks can
refer to them by object reference. That is different from durable storage. The
object store is runtime memory attached to the Ray cluster. If a workload needs
durable artifacts, checkpoints, datasets, model weights, or logs, write them to
real storage deliberately instead of treating the Ray pod filesystem or object
store as durable state.

Ray schedules against resources it knows about: CPUs, memory, GPUs if present,
custom resources, and placement constraints. Kubernetes schedules the pods that
provide those resources. When something cannot run, check both layers. A Ray task
can be pending because Ray lacks available CPUs even though Kubernetes pods are
healthy. A Ray worker can be missing because Kubernetes cannot schedule the pod
even though the Ray cluster spec asks for it.

## Operator Versus Ray Cluster

The KubeRay operator and a Ray cluster are separate things.

The operator runs in the operator namespace and watches Ray custom resources. It
owns reconciliation. If it is unhealthy, new or changed `RayCluster` resources
may not turn into healthy pods and Services.

A `RayCluster` is the desired state for one Ray runtime. In this repo, Pulumi
declares the `ray-dev` `RayCluster` in the `ray-dev` namespace. The spec has a
head group and one worker group. Each group includes a pod template with the Ray
image, resources, ports, and Ray start parameters. KubeRay reads that spec and
creates the lower-level Kubernetes objects needed to run Ray.

Pulumi, KubeRay, and Ray each own a different layer:

```text
Pulumi owns the declared Kubernetes resources.
KubeRay owns the controller-created Ray runtime objects.
Ray owns task, actor, job, Serve, and scheduler state inside the running cluster.
```

That boundary matters during debugging. If a Pulumi-declared Service has the
wrong selector, fix the Pulumi program. If KubeRay-created worker pods are
pending, inspect the `RayCluster` status, pod events, image pulls, and resource
requests before editing child pods. If a Python task fails with `ImportError`,
the operator is probably doing its job and the workload packaging needs work.

The `ray-dev` cluster uses fixed worker replicas by default: `replicas`,
`minReplicas`, and `maxReplicas` all come from the same configured value. That
means changing `rayDevWorkerReplicas` scales the fixed size of the worker group;
it does not turn on dynamic autoscaling by itself.

## Access Paths

There are two main human or client-facing paths into this stack.

The Ray dashboard is exposed through a Pulumi-owned `ClusterIP` Service named
`ray-dev-dashboard` and a Pulumi-owned Ingress named `ray-dev-dashboard` using
the `tailscale` ingress class. The dashboard talks to the Ray head on port
`8265`. Use the Pulumi output for the dashboard host instead of hard-coding a
private hostname in notes or docs.

The Ray Client API is exposed through a Pulumi-owned `ClusterIP` Service named
`ray-dev-api`. The Service selects the Ray head pod and forwards port `10001`.
It is annotated for Tailscale exposure and uses the configured Tailscale
hostname. This is the path a compatible local Python client can use with a
`ray://...:10001` address.

Start by reading the outputs:

```bash
cd pulumi/core/operators/kuberay

pulumi stack output --stack mx namespace
pulumi stack output --stack mx chart_version
pulumi stack output --stack mx ray_dev_namespace
pulumi stack output --stack mx ray_dev_cluster_name
pulumi stack output --stack mx ray_dev_api_service
pulumi stack output --stack mx ray_dev_api_hostname
pulumi stack output --stack mx ray_dev_dashboard_ingress_host
```

Then inspect the Kubernetes objects:

```bash
NS="$(pulumi stack output --stack mx ray_dev_namespace)"
CLUSTER="$(pulumi stack output --stack mx ray_dev_cluster_name)"

kubectl get rayclusters.ray.io -n "$NS"
kubectl describe raycluster -n "$NS" "$CLUSTER"
kubectl get pods,svc,ingress -n "$NS"
```

For the dashboard path, verify the Service has endpoints and the Ingress points
at the expected Service:

```bash
kubectl get svc,endpoints,ingress -n "$NS" ray-dev-dashboard
kubectl get endpointslices -n "$NS" \
  -l kubernetes.io/service-name=ray-dev-dashboard
```

For the Ray Client path, verify the Tailscale-exposed Service selects the head
pod and has an endpoint on port `10001`:

```bash
kubectl get svc,endpoints -n "$NS" ray-dev-api
kubectl get endpointslices -n "$NS" \
  -l kubernetes.io/service-name=ray-dev-api
kubectl get pod -n "$NS" \
  -l "ray.io/cluster=$CLUSTER,ray.io/node-type=head" \
  --show-labels
```

## A Small Ray Smoke Test

For a local client, the Python environment needs a Ray version compatible with
the cluster. The current default cluster image uses Python 3.13.13 and Ray
2.55.1, so a matching local Ray install is the least surprising starting point.

This is the shape of a Ray Client smoke test:

```python
import ray

ray.init("ray://<ray-dev-api-host>:10001")

@ray.remote
def square(value: int) -> int:
    return value * value

refs = [square.remote(value) for value in range(8)]
print(ray.get(refs))
```

Run that only from a network location that can reach the Tailscale-exposed Ray
Client hostname. If it connects but the task fails, move from network debugging
to runtime debugging: check the dashboard, head logs, worker logs, and whether
the function imports are present in the cluster image.

From inside the cluster, use Kubernetes DNS instead of the Tailscale hostname.
A temporary shell is often enough:

```bash
kubectl run -n "$NS" ray-smoke --rm -it --restart=Never \
  --image=ghcr.io/kzh/ray:2.55.1-py3.13.13-uv-amd64 -- bash
```

Inside that shell:

```bash
python - <<'PY'
import ray

ray.init("ray://ray-dev-api:10001")

@ray.remote
def ping():
    return "ok"

print(ray.get(ping.remote()))
PY
```

Use this as a minimal connectivity and scheduling check, not as an application
deployment pattern.

## Jobs And Serve

Ray Client, Ray Jobs, and Ray Serve solve different problems.

Ray Client connects a driver process to a running cluster. It is convenient for
interactive development because your local Python process can submit tasks to
the cluster. The tradeoff is that the driver still lives outside the cluster, so
network interruptions, local package drift, and version mismatches can affect the
session.

Ray Jobs package an entrypoint and run the driver on the cluster. This is a
better shape for batch-style work: submit a script, let the cluster run it, read
status and logs from the dashboard or Jobs API, and avoid keeping your laptop as
the long-running driver. In this stack, the Jobs API is part of the Ray
dashboard process on the head node. Use the dashboard address when using
`ray job submit`.

From inside the cluster, a simple job submission looks like this:

```bash
ray job submit \
  --address http://ray-dev-dashboard.ray-dev.svc.cluster.local:8265 \
  -- python -c 'import ray; ray.init(); print(ray.cluster_resources())'
```

From outside the cluster, use the Tailscale dashboard host and the correct URL
scheme for that ingress. Do not paste private hostnames into docs.

Ray Serve is Ray's serving layer for HTTP/gRPC-style model or application
serving. Serve deployments run inside the Ray cluster and are scheduled onto Ray
resources. The stack ships Grafana dashboards for Serve and Serve deployments,
but those dashboards will not show meaningful application traffic until a Serve
application is actually running and metrics are being scraped.

There is also a Kubernetes-native KubeRay pattern for long-lived Serve
applications: a `RayService` custom resource. That is different from running
Serve manually inside the existing `ray-dev` cluster. This repo currently has
generated bindings and source CRD material for `RayCluster`, not repo-local
typed bindings for `RayJob` or `RayService`. If a future workload needs
Kubernetes-reconciled Ray jobs or a durable `RayService`, add that API surface
deliberately: update the CRD source, regenerate bindings, write the Pulumi
resource in the owning stack, and preview the result.

The short version:

```text
Interactive Python:  Ray Client
Batch application:   Ray Jobs
Serving application: Ray Serve
Kubernetes-owned serving lifecycle: RayService, if added intentionally
```

## Dashboard And Metrics

The Ray dashboard is the first human inspection surface. It shows nodes,
resources, jobs, tasks, actors, logs, Serve state, and cluster health. In this
repo it is also the path to the Ray Jobs API.

The stack sets Ray dashboard environment variables on the head container:

```text
RAY_PROMETHEUS_HOST
RAY_PROMETHEUS_NAME
RAY_GRAFANA_HOST
RAY_GRAFANA_IFRAME_HOST
RAY_GRAFANA_ORG_ID
```

Those values tell Ray how to link from the dashboard to Prometheus and Grafana.
They do not create metrics by themselves. The scrape path comes from the
operator chart's metrics settings and from the `ray-dev-pods` `PodMonitor`.

The `PodMonitor` selects pods with:

```text
ray.io/cluster=<ray-dev cluster name>
ray.io/is-ray-node=yes
```

and scrapes the `metrics` port at `/metrics` every 30 seconds. If Grafana is
empty but the Ray cluster is healthy, check the labels, `PodMonitor`, Prometheus
target discovery, and whether the Ray pods expose the expected metrics port
before changing Ray application code.

The stack loads these dashboard JSON files as ConfigMaps labeled
`grafana_dashboard=1` in the monitoring namespace:

```text
default_grafana_dashboard.json
serve_grafana_dashboard.json
serve_deployment_grafana_dashboard.json
serve_llm_grafana_dashboard.json
data_grafana_dashboard.json
data_llm_grafana_dashboard.json
train_grafana_dashboard.json
```

The presence of a dashboard ConfigMap only proves Grafana can discover a
dashboard definition. It does not prove that Prometheus is scraping Ray metrics
or that a given Ray feature is in use.

## Packaging And Runtime Expectations

Ray makes Python distribution easy to try and easy to get subtly wrong. A local
driver can import packages that the cluster image does not have. A task can run
on a worker that lacks a dependency available on the head. A Serve deployment
can start on one node and later be rescheduled onto a pod with a different
filesystem state if dependencies were installed by hand.

For durable work, assume dependencies must be declared before the Ray pod
starts. The clean options are:

```text
Build a Ray image with the application and dependencies.
Use a Ray runtime_env for small, explicit, reproducible dependency overlays.
Mount or fetch data and model artifacts from real storage.
Keep the Ray Python package version aligned with rayVersion and the image tag.
```

The default image is useful for basic Ray behavior and simple tests. It should
not become a hidden application image. If an experiment needs `torch`, `pandas`,
model weights, private Python packages, system libraries, or large assets, move
that setup into an image, a runtime environment, or a real artifact store before
treating the workload as repeatable.

Runtime environments are useful, but they are not magic. They add startup time,
depend on package indexes or artifact availability, and can fail differently on
different nodes if they rely on undeclared system libraries. For short
experiments they are fine. For jobs that should keep working after a pod restart,
prefer an image or a narrowly defined runtime environment with pinned versions.

Do not install packages manually into a running Ray pod and then rely on that
state. Pods are replaceable. KubeRay can recreate them. The next worker may not
have the manual change.

Also be clear about data locality. The current `ray-dev` cluster does not declare
durable volumes for workload output. Anything important should be written to a
real storage system rather than left on a pod filesystem.

## Debugging From First Principles

Start by deciding which layer is failing:

```text
Pulumi layer:      wrong declared resource, stack config, preview, output, Service, Ingress, PodMonitor
KubeRay layer:     operator health, RayCluster status, reconciliation, generated pods and Services
Kubernetes layer:  scheduling, image pulls, endpoints, probes, events, resource pressure
Ray layer:         tasks, actors, jobs, Serve deployments, object store, package imports
Network layer:     Tailscale service exposure, dashboard ingress, client hostname, port reachability
Metrics layer:     PodMonitor, labels, Prometheus targets, Grafana dashboard variables
```

Then collect evidence in that order. A useful first pass is:

```bash
cd pulumi/core/operators/kuberay
NS="$(pulumi stack output --stack mx ray_dev_namespace)"
CLUSTER="$(pulumi stack output --stack mx ray_dev_cluster_name)"

kubectl get pods -n kuberay-operator
kubectl get rayclusters.ray.io -n "$NS"
kubectl describe raycluster -n "$NS" "$CLUSTER"
kubectl get pods,svc,endpoints,ingress -n "$NS"
kubectl get events -n "$NS" --sort-by=.lastTimestamp
```

Read the custom resource status before changing child objects:

```bash
kubectl get raycluster -n "$NS" "$CLUSTER" \
  -o jsonpath='{.metadata.generation}{" observed="}{.status.observedGeneration}{"\n"}'

kubectl get raycluster -n "$NS" "$CLUSTER" \
  -o jsonpath='{range .status.conditions[*]}{.type}{"="}{.status}{" reason="}{.reason}{" message="}{.message}{"\n"}{end}'
```

If `observedGeneration` is behind `metadata.generation`, the operator has not
processed the latest desired state yet. If a condition is `False` or `Unknown`,
use its reason and message to choose the next check.

For operator reconciliation problems:

```bash
kubectl logs -n kuberay-operator \
  -l app.kubernetes.io/name=kuberay-operator \
  --tail=200
```

For Ray runtime problems, inspect the head and worker logs:

```bash
kubectl logs -n "$NS" \
  -l "ray.io/cluster=$CLUSTER,ray.io/node-type=head" \
  --tail=200

kubectl logs -n "$NS" \
  -l "ray.io/cluster=$CLUSTER,ray.io/node-type=worker" \
  --tail=200
```

For scheduler or resource questions, run Ray's own status command in the head
pod:

```bash
HEAD="$(kubectl get pod -n "$NS" \
  -l "ray.io/cluster=$CLUSTER,ray.io/node-type=head" \
  -o jsonpath='{.items[0].metadata.name}')"

kubectl exec -n "$NS" "$HEAD" -- ray status
```

Common patterns:

Dashboard unavailable usually means the head pod is not ready, the
`ray-dev-dashboard` Service has no endpoint, the Tailscale ingress is not
routing to the Service, or the dashboard process is failing inside the head pod.

Ray Client cannot connect usually means the `ray-dev-api` Service has no
endpoint, the Tailscale-exposed hostname is not reachable from the client, port
`10001` is blocked, or the local Ray client version is incompatible with the
cluster.

Tasks failing with missing imports usually means the code worked on the driver
but the package is absent from the Ray image or runtime environment. Fix the
runtime package boundary, not the operator.

Workers not starting usually points to image pull failures, resource requests
that cannot be scheduled, invalid pod template fields, or a `RayCluster` status
condition explaining why KubeRay could not reconcile the worker group.

Metrics missing from Grafana usually points to the `PodMonitor`, pod labels,
Prometheus target discovery, or dashboard variables. A healthy Ray dashboard and
an empty Grafana panel can coexist.

Serve dashboard panels with no data usually mean no Serve app is running, no
Serve traffic is present, or the Serve metrics are not being scraped. The
dashboard files are installed whether or not a Serve app exists.

## Safe Changes

Treat changes to this stack as live infrastructure changes, even when they look
small. A Ray version bump, image change, worker count change, resource change,
Service selector change, Ingress host change, chart upgrade, or CRD update can
affect running workloads and access paths.

Use Pulumi for durable changes to Pulumi-owned objects:

```text
Change the operator chart in this stack.
Change the ray-dev RayCluster spec in this stack.
Change dashboard/client Services and ingress in this stack.
Change monitoring resources and dashboard ConfigMaps in this stack.
```

Do not hand-edit generated CRD bindings under `pulumi/lib/kuberay_crds`. If the
KubeRay chart CRDs change, regenerate through the repo workflow:

```bash
just generate-kuberay-crds
just check-python
just lint
git diff --check
```

If the change is to this Pulumi project, run the project checks and a targeted
preview:

```bash
just sync pulumi/core/operators/kuberay
just check-python
just lint
git diff --check
just preview pulumi/core/operators/kuberay stack=mx
```

Do not run `pulumi up`, `pulumi destroy`, or `just up` unless the current task
explicitly asks for an apply or teardown.

When reviewing a preview, look past the summary count. Check whether it changes
resource names, selectors, ports, CRD versions, pod templates, worker group
sizes, resource requests, ingress hosts, Tailscale annotations, or dashboard
ConfigMap names. Those are the parts most likely to turn a harmless-looking
change into a broken access path or a replacement.

A few specific changes deserve extra care:

Changing `rayDevImage` should be paired with checking `rayDevVersion` and the
Python package version inside the image. Ray version mismatches are a common
source of client and job confusion.

Changing resource requests or object store memory should be checked against
Kubernetes scheduling and Ray workload needs. More object store memory may require
more pod memory. More worker replicas may require more cluster capacity.

Changing Service selectors should be treated as an access-path change. The
dashboard and API Services deliberately select the head pod by Ray labels. If
those labels change under a chart or operator upgrade, the Services can exist but
have no endpoints.

Changing the dashboard host or API hostname should be checked against the
Tailscale path. Keep private host details in stack config and Pulumi outputs, not
in this documentation.

Adding `RayJob` or `RayService` support should be treated as a new API surface,
not as a casual extension of the existing dev cluster. Decide which stack owns
the workload, make sure the CRDs and typed bindings exist, declare the resource
in Pulumi, and preview it. For production-like Serve, also decide how images,
runtime environments, request routing, metrics, and rollback should work.

After an apply, verification should cross the same boundary the change touched:

```text
Operator/chart change: verify operator pods, CRDs, webhooks if present, and the ray-dev RayCluster.
RayCluster spec change: verify status conditions, head and worker pods, Ray status, and dashboard.
Access-path change: verify Service endpoints, Tailscale ingress or exposure, and a real client connection.
Runtime/image change: run a small task or job that imports the expected packages.
Metrics change: verify Prometheus targets and at least one Grafana panel with fresh data.
```

The practical rule is simple: make the durable change at the layer that owns the
desired state, then verify the next layer down actually reconciled it.
