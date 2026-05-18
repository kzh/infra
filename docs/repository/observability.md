# Observability

Observability in this repo is deliberately split between platform ownership and
service ownership. The monitoring stack provides Prometheus, Grafana, the
Prometheus Operator CRDs, storage, retention, ingress, and the discovery
machinery. The service stacks own the signals that explain their own behavior:
metrics exposure, `ServiceMonitor` or `PodMonitor` objects, and Grafana dashboard
JSON.

That split matters. A dashboard for Spark should change beside Spark because it
depends on Spark labels, Spark metric names, Spark operator behavior, and Spark
failure modes. A dashboard for Tailscale proxies should change beside the
Tailscale stack because it depends on proxy labels and Tailscale-specific metric
semantics. `pulumi/ops/monitoring` should not become a pile of dashboards for
everything else in the cluster.

## What Monitoring Owns

`pulumi/ops/monitoring` installs the common observability substrate:

```text
Prometheus Operator CRDs
kube-prometheus-stack
Prometheus storage and retention
Grafana and its Tailscale ingress
Grafana dashboard and datasource sidecars
```

The stack currently installs `prometheus-operator-crds` separately, then deploys
`kube-prometheus-stack` with chart-managed CRD installation disabled. That is a
normal operator pattern: CRDs are installed first, then the chart that depends on
them starts reconciling custom resources. The generated Pulumi package under
`pulumi/lib/monitoring_crds` is for creating typed `ServiceMonitor` and
`PodMonitor` resources from service stacks; do not hand-edit it.

Prometheus is configured as the durable scrape store. Grafana is configured as
the dashboard UI and is exposed through the cluster's private ingress path.
Grafana dashboard discovery is sidecar-based: service stacks create ConfigMaps
with dashboard JSON and label them:

```text
grafana_dashboard=1
```

The monitoring stack owns how Grafana discovers those ConfigMaps. The service
stack owns the actual JSON and the ConfigMap that carries it.

## What Service Stacks Own

A service stack owns observability when it owns the workload. In practice that
means:

```text
the exporter or metrics endpoint
the Kubernetes Service or pod labels that expose that endpoint
the ServiceMonitor or PodMonitor that Prometheus discovers
the dashboard JSON that explains the metrics
the Pulumi dependency edges needed to create these in the right order
```

Some charts can generate their own monitors and dashboards when values are set.
That is still service-stack ownership because the values live in the service
stack. Other services create `ServiceMonitor` or `PodMonitor` resources directly
with the generated monitoring bindings:

```python
from pulumi_monitoring_crds.monitoring.v1 import ServiceMonitor

ServiceMonitor(
    "example-servicemonitor",
    metadata={
        "name": "example",
        "namespace": namespace_name,
        "labels": {
            "release": monitoring_release_label,
        },
    },
    spec={
        "selector": {
            "matchLabels": {
                "app": "example",
                "component": "metrics",
            },
        },
        "namespaceSelector": {
            "matchNames": [namespace_name],
        },
        "endpoints": [
            {
                "port": "metrics",
                "path": "/metrics",
                "interval": "30s",
                "scheme": "http",
            }
        ],
    },
)
```

The exact labels and port names are not incidental. They are the contract between
Kubernetes, the Prometheus Operator, Prometheus, and Grafana.

## The Discovery Contract

Prometheus does not scrape a workload just because a pod has a `/metrics` path.
Discovery has several layers, and each layer has different labels.

For a `ServiceMonitor`:

```text
Prometheus selects the ServiceMonitor by the ServiceMonitor's labels.
The ServiceMonitor selects Kubernetes Services by Service labels.
The Service selects pods by pod labels.
The ServiceMonitor endpoint names a Service port, not a container port.
The Service endpoint path and scheme must match the metrics endpoint.
```

For a `PodMonitor`:

```text
Prometheus selects the PodMonitor by the PodMonitor's labels.
The PodMonitor selects pods directly by pod labels.
The podMetricsEndpoints entry points at a container port name, target port, or number.
The path and scheme must match the metrics endpoint.
```

In this repo, service-created monitors normally carry:

```text
release=kube-prometheus-stack
```

Many stacks make that value configurable as `monitoringReleaseLabel`, but the
default is intentionally consistent. Preserve that label unless the monitoring
stack's selector changes.

The most common mistakes are small and easy to miss:

```text
using pod labels in a ServiceMonitor selector when it must match Service labels
using a container port name where the ServiceMonitor needs the Service port name
forgetting namespaceSelector when the monitor and target are in different namespaces
adding a dashboard ConfigMap without grafana_dashboard=1
renaming a dashboard file without updating the ConfigMap data key
writing a dashboard query against labels that the scrape path does not preserve
```

## Current Stack Patterns

The current repo has several useful patterns to copy.

| Stack | Pattern |
| --- | --- |
| `pulumi/ops/monitoring` | Owns Prometheus Operator CRDs, `kube-prometheus-stack`, Prometheus persistence and retention, Grafana, and dashboard sidecar discovery. |
| `pulumi/core/networking/cf-tunnel` | Creates a `ServiceMonitor` for the Cloudflare tunnel controller metrics Service and ships tunnel overview/transport dashboards as ConfigMaps. |
| `pulumi/core/networking/tailscale` | Creates an explicit `ServiceMonitor` for the Tailscale operator metrics Service, enables proxy metrics through `ProxyClass`, and ships operator/proxy dashboards. |
| `pulumi/core/operators/kuberay` | Enables KubeRay chart metrics, creates a `PodMonitor` for Ray pods, and ships Ray upstream dashboards beside the KubeRay stack. |
| `pulumi/core/operators/cnpg` | Enables chart-managed CNPG `PodMonitor` and chart-managed Grafana dashboard ConfigMaps through Helm values. |
| `pulumi/data/databases/postgres` | Creates an explicit `PodMonitor` for the CNPG PostgreSQL cluster pods, with chart-generated rules trimmed to this deployment's needs. |
| `pulumi/data/analytics/clickhouse` | Enables operator metrics, chart-managed `ServiceMonitor`, and chart-managed Grafana dashboards through the ClickHouse operator chart values. |
| `pulumi/data/analytics/spark` | Enables Spark operator Prometheus metrics and chart-created `PodMonitor`, then ships a Spark overview dashboard with the Spark stack. |
| `pulumi/data/analytics/slurm` | Enables Slinky controller metrics and chart-created `ServiceMonitor`, then ships a Slurm overview dashboard with the Slurm stack. |
| `pulumi/data/workflow/airflow` | Enables Airflow StatsD, creates a `ServiceMonitor` for the StatsD scrape endpoint, and ships an Airflow overview dashboard. |
| `pulumi/apps/mediawiki` | Ships a MediaWiki dashboard beside the app stack. Its dashboard composes Kubernetes, MySQL operator, storage, restart, and Tailscale traffic signals rather than relying on a MediaWiki exporter. |

Do not copy a pattern without checking it. Copy the ownership rule, then inspect the target
chart and workload. If a chart can generate a correct monitor, using chart values
is often simpler. If the generated monitor has the wrong selector, namespace, or
labels for this cluster, create the monitor explicitly in the owning Pulumi
program.

## Dashboard ConfigMaps

The local dashboard pattern is intentionally plain:

```python
from pathlib import Path

dashboards_dir = Path(__file__).resolve().parent / "dashboards"
dashboard_files = [
    "example-overview.json",
]

for dashboard_file in dashboard_files:
    dashboard_name = dashboard_file.replace(".json", "")
    dashboard_data = (dashboards_dir / dashboard_file).read_text(encoding="utf-8")
    k8s.core.v1.ConfigMap(
        f"example-dashboard-{dashboard_name}",
        metadata=k8s.meta.v1.ObjectMetaArgs(
            name=f"example-dashboard-{dashboard_name}",
            namespace=namespace_name,
            labels={
                "grafana_dashboard": "1",
                "app": "example",
            },
        ),
        data={
            dashboard_file: dashboard_data,
        },
    )
```

Keep the dashboard JSON under the owning project's `dashboards/` directory.
Store the JSON in a ConfigMap whose data key is the file name. Label the
ConfigMap with `grafana_dashboard=1`. Additional labels such as `app` are useful
for humans and `kubectl` filtering, but the Grafana sidecar discovery label is
the required one.

Some stacks place dashboard ConfigMaps in the service namespace; some place them
in the monitoring namespace. Follow the existing stack unless there is a reason
to change it. The important part is that the ConfigMap is managed by the stack
that owns the dashboard and carries the discovery label Grafana expects.

Use `delete_before_replace=True` deliberately when dashboard ConfigMap renames or
chart behavior make same-name replacement noisy. Do not add it as a reflex; use
it when preview shows an actual replacement conflict or the existing local
pattern already uses it for that dashboard family.

## Debug A Missing Dashboard

Start with discovery before editing dashboard JSON. If Grafana never loaded the
ConfigMap, PromQL changes will not help.

```bash
kubectl get configmaps --all-namespaces -l grafana_dashboard=1
kubectl describe configmap -n <namespace> <dashboard-configmap>
kubectl get pods -n monitoring
```

Then inspect Grafana and its sidecars:

```bash
kubectl logs -n monitoring deploy/kube-prometheus-stack-grafana --all-containers --tail=200
kubectl get events -n monitoring --sort-by=.lastTimestamp
```

Check the dashboard file locally before assuming Grafana is wrong:

```bash
jq empty pulumi/<area>/<service>/dashboards/<dashboard>.json
```

If the ConfigMap exists and the JSON is valid, look for sidecar load errors,
folder/provider errors, or a namespace mismatch. If the ConfigMap does not
exist, the owning stack did not create it; inspect that stack's `__main__.py`,
run the cheap checks, and preview the owning stack rather than changing
`pulumi/ops/monitoring`.

## Debug Missing Metrics

A useful scrape investigation moves from Kubernetes discovery toward Prometheus
state. First list the monitor objects:

```bash
kubectl get servicemonitors,podmonitors --all-namespaces
kubectl get servicemonitors,podmonitors --all-namespaces -l release=kube-prometheus-stack
kubectl describe servicemonitor -n <namespace> <name>
kubectl describe podmonitor -n <namespace> <name>
```

For a `ServiceMonitor`, compare the ServiceMonitor selector to the Service
labels, then compare the Service selector to the pod labels:

```bash
kubectl get svc -n <namespace> --show-labels
kubectl describe svc -n <namespace> <metrics-service>
kubectl get pods -n <namespace> --show-labels
kubectl get endpoints -n <namespace> <metrics-service>
kubectl get endpointslices -n <namespace> -l kubernetes.io/service-name=<metrics-service>
```

For a `PodMonitor`, skip the Service layer and compare the PodMonitor selector
directly to pod labels:

```bash
kubectl get pods -n <namespace> --show-labels
kubectl describe pod -n <namespace> <pod>
```

If there is no Prometheus target, the monitor was not selected or did not select
anything. Check the monitor labels, namespace selector, target labels, and port
name. If there is a target but it is down, check the endpoint port, path, scheme,
network policy, and exporter process. If the target is up but the panel is
empty, check the metric names and dashboard label filters.

When you need to test the endpoint directly, port-forward the metrics Service or
pod and read the metrics text:

```bash
kubectl port-forward -n <namespace> svc/<metrics-service> 9099:<service-port>
curl -fsS http://127.0.0.1:9099/metrics | head
```

Prometheus itself is the final source for scrape truth. Port-forward the
Prometheus Service when needed and use the targets and service-discovery pages:

```bash
kubectl port-forward -n monitoring svc/kube-prometheus-stack-prometheus 9090:9090
```

Useful first PromQL checks:

```promql
up{namespace="<namespace>"}
count by (namespace, job) (up)
scrape_samples_scraped{namespace="<namespace>"}
scrape_duration_seconds{namespace="<namespace>"}
```

Use broad queries first. If `up{namespace="<namespace>"}` has no series, the
problem is before the dashboard. If `up` is `0`, the scrape target exists but the
endpoint is failing. If `up` is `1` and the dashboard is empty, the dashboard is
probably filtering on the wrong label or querying a metric the exporter no
longer emits.

## Write Signals That Explain Behavior

The goal is not to graph every metric. A useful signal answers an operational
question:

```text
Is the service reachable?
Is the control loop healthy?
Is work moving through the system?
Are errors increasing?
Is latency changing?
Is capacity running out?
Which component should I inspect next?
```

For HTTP-ish services, start with availability, request rate, error rate,
latency, saturation, restarts, and resource pressure. For controllers, graph
reconcile rate, reconcile errors, reconcile duration, workqueue depth, retries,
and Kubernetes API errors. For workflow systems, graph scheduler heartbeats,
loaded work, import or parse failures, queue depth, successful and failed runs,
and executor capacity. For compute systems, graph pending/running jobs,
executor or worker count, failed executors, queue pressure, and cluster resource
usage. For networking, graph targets up, throughput, packet drops, path or DERP
distribution when relevant, health warnings, and RTT.

Choose labels that keep cardinality bounded. Good labels usually describe a
small vocabulary:

```text
namespace
app
component
controller
queue
result
status
code
cluster
node
```

Avoid unbounded labels in dashboards and exporters:

```text
user id
request id
pod uid
full URL
raw query string
object key
SQL text
exception message
```

For counters, graph `rate()` or `increase()` over a window. For gauges, graph
the current value or a recent max/min when that helps. For histograms, use the
exporter's bucketed metric correctly and label the panel as a percentile only
when the query really computes one. Put units on panels. Make legends name the
component and outcome, not the whole PromQL expression.

## Write Dashboards For Decisions

A dashboard should read from top to bottom like an investigation.

Start with a small health row: target up, replicas or workers, current error
rate, and the one workload-specific signal that says whether useful work is
happening. Follow with workload behavior: requests, jobs, reconciles, DAGs,
executors, queues, sessions, or tasks. Then show resource pressure: CPU, memory,
PVC usage, restarts, queue depth, object store pressure, or network drops. Put
runtime internals last unless the service is a runtime where those internals are
the product.

Prefer panels that lead to action:

```text
"Reconcile Errors by Controller" tells you which controller to inspect.
"Workqueue Depth by Queue" tells you whether the controller is falling behind.
"Executor Failures Last Hour" tells you whether Spark is losing capacity.
"Import Errors" tells you whether Airflow DAG parsing is blocking use.
"MTU Drops / sec" tells you whether tunnel transport needs network attention.
```

Avoid panels whose only message is "a number changed" unless the next step is
obvious. If no one knows what they would do when the panel looks wrong, either
remove it or rewrite it around a better question.

## Validate Observability Changes

For a dashboard-only edit, validate the JSON and preview the owning stack:

```bash
jq empty pulumi/<area>/<service>/dashboards/<dashboard>.json
just preview pulumi/<area>/<service> stack=mx
```

For a new or changed monitor, run the normal repo gates first:

```bash
just check-python
just lint
git diff --check
```

Then preview the owning stack:

```bash
just preview pulumi/<area>/<service> stack=mx
```

Read replacements carefully. Monitor and dashboard ConfigMap replacements are
usually fine. Replacement of a workload Service, persistent storage, database
cluster, or ingress identity is a different kind of change and should not be
treated as routine observability cleanup.

Do not run `pulumi up`, `pulumi destroy`, or `just up` unless the task explicitly
asks for a live apply or destructive action.
