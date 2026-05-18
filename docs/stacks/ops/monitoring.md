# Monitoring

Source: `pulumi/ops/monitoring`

Monitoring is the shared observability platform for this repository. It answers
one cluster-wide question: when a service changes behavior, where does the
signal go, and how do we make it useful without turning the monitoring stack
into a pile of unrelated service knowledge?

The short version is:

```text
application or operator
  exposes Prometheus-format metrics
    through a Service port or Pod port
      selected by a ServiceMonitor or PodMonitor
        reconciled by Prometheus Operator
          scraped and stored by Prometheus
            queried by Grafana dashboards
```

The monitoring stack owns the Prometheus and Grafana platform. The service stack
owns the metrics endpoint, the monitor that selects that endpoint, and the
dashboards that explain that service. That boundary is the important part of the
model.

If a Spark panel is wrong, fix it in `pulumi/data/analytics/spark`. If a
Tailscale target is not being scraped, start in `pulumi/core/networking/tailscale`.
If Grafana itself is unavailable, Prometheus storage is full, or the Prometheus
Operator CRDs need to change, start here in `pulumi/ops/monitoring`.

## What This Stack Owns

`pulumi/ops/monitoring/__main__.py` installs Prometheus Operator CRDs first and
then installs `kube-prometheus-stack` into the `monitoring` namespace. The stack
uses the Prometheus community Helm repository for both charts.

Current platform shape:

```text
Namespace:                    monitoring
CRD chart:                    prometheus-operator-crds 29.0.0
kube-prometheus-stack chart:  85.1.0
Prometheus storage class:     local-path
Prometheus storage request:   100Gi
Prometheus retention:         90d
Prometheus admin API:         enabled
Grafana host:                 grafana
Grafana ingress class:        tailscale
Grafana persistence:          enabled
Grafana anonymous role:       Viewer
Dashboard ConfigMap label:    grafana_dashboard=1
```

The stack deliberately installs the Prometheus Operator CRDs through the
separate `prometheus-operator-crds` chart and sets `crds.enabled: false` on
`kube-prometheus-stack`. That gives CRD ownership a clear home and avoids the
main chart trying to manage CRDs implicitly.

The code currently customizes Prometheus storage, retention, and admin API
availability. It also customizes Grafana access, persistence, ingress, and
sidecar reload behavior. The Grafana admin password comes from secret Pulumi
config; do not print it, copy it into docs, or include it in commit messages.

There are three chart transformations worth knowing about before touching this
stack:

- The Grafana PVC gets `pulumi.com/skipAwait=true`.
- A generated Grafana Role ignores `rules` drift.
- A small set of chart-generated Jobs and a ConfigMap use
  `delete_before_replace=True` to handle same-name replacement cleanly.

Those transformations are not generic preferences. They are targeted fixes for
resources that are noisy or awkward under this chart. If a future chart upgrade
adds a new replacement problem, inspect the rendered object and preview first;
do not widen these transformations by habit.

There are helper functions for metrics-server and the Kubernetes dashboard in
the file, but they are not called by the main program. Treat them as inactive
code, not as part of the deployed monitoring platform. In particular, do not
assume `kubectl top` is provided by this stack unless the live cluster shows a
metrics-server deployment.

## What This Stack Does Not Own

Monitoring does not own every metric in the cluster. It provides the machinery
that discovers and stores metrics, but the service stack usually owns the
service-specific wiring.

That means service stacks should own:

- The exporter or application setting that exposes metrics.
- The Kubernetes Service or Pod port Prometheus will scrape.
- The `ServiceMonitor` or `PodMonitor`, unless the chart emits it.
- Service-specific Grafana dashboards.
- Service-specific alerting rules, when those alerts have an owner and a clear
  response path.

The monitoring stack should not become a central folder of dashboards for every
service. Keeping dashboards beside the service makes reviews better: the metric
producer, monitor, and panels change together, and the person reviewing a stack
can see the observability contract at the same time as the deployment contract.

## Prometheus From First Principles

Prometheus is pull-based. It does not receive arbitrary metric pushes from
services in this repo. Instead, Prometheus periodically makes HTTP requests to
targets that expose metrics in Prometheus text format, usually on `/metrics`.

A raw target is just an address, a path, and some labels. The Prometheus
Operator is what turns Kubernetes objects into those targets. It watches
`ServiceMonitor` and `PodMonitor` resources, checks whether the Prometheus
instance selects those monitor resources, and writes Prometheus scrape
configuration from them.

The distinction matters:

- A `ServiceMonitor` selects Services by label and then scrapes the selected
  Service endpoints.
- A `PodMonitor` selects Pods by label and scrapes Pod ports directly.
- The monitor resource itself must also be selected by Prometheus.

In this repo, manually created monitors and chart-created monitors are labeled
with the monitoring release label. The default is:

```text
release=kube-prometheus-stack
```

Most stacks expose that as config named `monitoringReleaseLabel`, defaulting to
`kube-prometheus-stack`. If the monitoring release label ever changes, the
service stacks need to move with it or Prometheus will stop selecting their
monitors.

Port names are just as important as labels. When a `ServiceMonitor` endpoint
says:

```python
"endpoints": [
    {
        "port": "metrics",
        "path": "/metrics",
        "interval": "30s",
    }
]
```

`port: "metrics"` refers to the Kubernetes Service port name, not a descriptive
comment. If the Service calls that port `http-metrics`, the monitor must say
`http-metrics`. A monitor with the right labels and the wrong port name will
exist, but it will not produce the target you expected.

For `PodMonitor`, `port` usually refers to a named container port. Some resources
can use `targetPort` instead; the Postgres stack uses `targetPort: 9187` for its
CloudNativePG pods. Prefer named ports when the chart or manifest gives you a
stable name because they survive numeric port changes more clearly.

## Grafana From First Principles

Grafana is the query and presentation layer. It does not scrape the cluster.
Dashboards are useful only when their PromQL matches data that Prometheus is
actually scraping.

This repo provisions Grafana dashboards through Kubernetes ConfigMaps. The
common convention is:

```text
metadata.labels.grafana_dashboard = "1"
data["some-dashboard.json"] = <Grafana dashboard JSON>
```

The dashboard JSON should live in the owning service's `dashboards/` directory.
The service's Pulumi program reads that JSON file and creates a ConfigMap with
the `grafana_dashboard=1` label. Grafana's sidecar discovers those labeled
ConfigMaps and makes them available to Grafana.

Some dashboard ConfigMaps live in the service namespace. Some chart-generated
dashboards are configured to land in `monitoring`. Follow the owning stack's
existing pattern unless there is a concrete reason to move it. The discovery
label is the key invariant.

Grafana dashboard files should be treated as code:

- Keep dashboard JSON beside the service it explains.
- Use stable dashboard titles and UIDs.
- Avoid private hostnames, personal account identifiers, or secret values in
  dashboard JSON.
- Keep datasource references portable. Do not depend on a personal Grafana UI
  datasource UID.
- Prefer PromQL that can be understood from service labels and metric names.
- Review dashboard changes with the service change that creates or changes the
  metric.

Do not rely on hand-edited dashboards in the Grafana UI for durable changes.
They may be useful while exploring, but the repo-backed dashboard JSON is the
source of truth.

## Current Service-Owned Patterns

There are two healthy ways to add monitoring in this repo.

The first is to let the service chart create the monitor or dashboard when the
chart already supports it. Examples:

- Spark enables the Spark operator Prometheus metrics and asks the chart to
  create a PodMonitor with the monitoring release label.
- Slurm enables controller metrics and the chart's ServiceMonitor.
- LiteLLM enables the chart ServiceMonitor and configures the application to
  expose Prometheus metrics.
- CloudNativePG operator enables its PodMonitor and chart-created Grafana
  dashboard.
- ClickHouse operator enables metrics, ServiceMonitor creation, and
  chart-created dashboards.

The second is to create typed monitor resources directly from the generated
Prometheus Operator bindings under `pulumi/lib/monitoring_crds`. Examples:

- Cloudflare Tunnel creates a `ServiceMonitor` for the tunnel controller metrics
  Service and owns two dashboards.
- Tailscale creates a Service for operator metrics, a `ServiceMonitor` for that
  Service, enables proxy metrics through `ProxyClass`, and owns operator/proxy
  dashboards.
- Airflow creates a `ServiceMonitor` for the StatsD exporter and owns an Airflow
  overview dashboard.
- Postgres creates a `PodMonitor` for CloudNativePG cluster pods.
- KubeRay creates a `PodMonitor` for Ray dev pods and owns the Ray dashboard
  bundle.

Both approaches are fine. Prefer the chart option when it is explicit, stable,
and labels monitors correctly. Prefer typed Pulumi monitor resources when the
chart does not expose enough control or when the repo needs a precise selector.

Current repo-owned dashboard files:

| Stack | Dashboard files |
| --- | --- |
| `pulumi/apps/mediawiki` | `mediawiki-overview.json` |
| `pulumi/core/networking/cf-tunnel` | `cloudflare-tunnel-overview.json`, `cloudflare-tunnel-transport.json` |
| `pulumi/core/networking/tailscale` | `tailscale-operator-overview.json`, `tailscale-proxy-metrics.json` |
| `pulumi/core/operators/kuberay` | `default_grafana_dashboard.json`, `serve_grafana_dashboard.json`, `serve_deployment_grafana_dashboard.json`, `serve_llm_grafana_dashboard.json`, `data_grafana_dashboard.json`, `data_llm_grafana_dashboard.json`, `train_grafana_dashboard.json` |
| `pulumi/data/analytics/slurm` | `slurm-overview.json` |
| `pulumi/data/analytics/spark` | `spark-overview.json` |
| `pulumi/data/workflow/airflow` | `airflow-overview.json` |

ClickHouse and CloudNativePG also configure their charts to create dashboards,
so not every dashboard source appears as a hand-maintained JSON file in this
repo.

## Adding A New Metrics Scrape

Start with the metric producer, not with Grafana. A dashboard should be the last
piece of the chain.

First, find out whether the application or operator can expose Prometheus
metrics. Look for chart values named `metrics`, `prometheus`, `serviceMonitor`,
`podMonitor`, or `monitoring`. If the chart has a well-supported
ServiceMonitor/PodMonitor option, use it and add the release label.

For chart-managed monitors, the shape usually looks like one of these:

```python
"metrics": {
    "enabled": True,
    "serviceMonitor": {
        "enabled": True,
        "labels": {
            "release": monitoring_release_label,
        },
    },
}
```

or:

```python
"prometheus": {
    "metrics": {
        "enable": True,
    },
    "podMonitor": {
        "create": True,
        "labels": {
            "release": monitoring_release_label,
        },
    },
}
```

If the chart cannot create the monitor, create one directly in the service
stack. A typical `ServiceMonitor` looks like this:

```python
from pulumi_monitoring_crds.monitoring.v1 import ServiceMonitor

monitoring_release_label = config.get(
    "monitoringReleaseLabel",
    "kube-prometheus-stack",
)

service_monitor = ServiceMonitor(
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
    opts=pulumi.ResourceOptions(depends_on=[metrics_service]),
)
```

Before adding it, verify the Service labels and port name you are selecting:

```bash
kubectl get svc -n <namespace> --show-labels
kubectl describe svc -n <namespace> <service-name>
kubectl get endpoints -n <namespace> <service-name>
kubectl get endpointslices -n <namespace> -l kubernetes.io/service-name=<service-name>
```

If a Service has no endpoints, Prometheus cannot scrape it. Fix the Service
selector or the workload labels before changing the monitor.

Use a `PodMonitor` when there is no stable Service to select or when the
operator exposes metrics directly on pods. A typical shape is:

```python
from pulumi_monitoring_crds.monitoring.v1 import PodMonitor

pod_monitor = PodMonitor(
    "example-podmonitor",
    metadata={
        "name": "example-pods",
        "namespace": namespace_name,
        "labels": {
            "release": monitoring_release_label,
        },
    },
    spec={
        "namespaceSelector": {
            "matchNames": [namespace_name],
        },
        "selector": {
            "matchLabels": {
                "app": "example",
            },
        },
        "podMetricsEndpoints": [
            {
                "port": "metrics",
                "path": "/metrics",
                "interval": "30s",
            }
        ],
    },
    opts=pulumi.ResourceOptions(depends_on=[workload]),
)
```

Keep resource creation outside `apply()` callbacks. Pass Pulumi outputs directly
as inputs when needed and use `depends_on` only for real ordering constraints,
such as "this monitor should not be created before the chart that creates the
Service."

Also think about cardinality before adding a scrape. A metric label with a
request ID, user ID, SQL query text, pod-unique generated value, file path, or
other unbounded string can multiply the number of time series until Prometheus
storage becomes the problem. Prefer labels that describe stable dimensions:
namespace, service, status, method, controller, queue, role, shard, and similar
bounded categories.

## Adding A Dashboard

Add dashboards to the owning service stack, not to `pulumi/ops/monitoring`,
unless the dashboard is about the monitoring platform itself.

The usual pattern is:

1. Put the exported dashboard JSON in the service-local `dashboards/` directory.
2. Add the filename to that service's dashboard file list.
3. Read it with `Path(__file__).resolve().parent / "dashboards"`.
4. Create a ConfigMap labeled `grafana_dashboard: "1"`.
5. Make the ConfigMap depend on the resource that makes the dashboard useful,
   such as the chart, monitor, or ingress.

The code shape looks like this:

```python
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
        opts=pulumi.ResourceOptions(depends_on=[service_monitor]),
    )
```

Use `delete_before_replace=True` if you are changing an immutable ConfigMap name
pattern or following an existing stack pattern that already uses it. Do not add
it reflexively; use it when preview shows a same-name replacement problem or
when the owning stack already established that convention for dashboard
ConfigMaps.

When exporting a dashboard from Grafana, review the JSON before committing it:

- Keep `title` and `uid` stable.
- Remove private URLs and personal identifiers.
- Avoid committing local time ranges that make the dashboard open to a stale
  moment.
- Prefer dashboard variables only when they make the dashboard more reusable.
- Check every PromQL expression against the metric labels the service actually
  emits.

Dashboard JSON can be large and noisy. Try to keep the meaningful change
reviewable: do not re-export every panel if you only changed one query.

## Debugging A Missing Scrape

Separate the failure into one of four states:

```text
monitor missing
monitor exists but Prometheus did not select it
target exists but is down
target is up but the expected metric is absent
```

Those states have different fixes.

Start with Kubernetes discovery:

```bash
kubectl get servicemonitors,podmonitors --all-namespaces
kubectl get servicemonitors,podmonitors --all-namespaces -l release=kube-prometheus-stack
kubectl describe servicemonitor -n <namespace> <monitor-name>
kubectl describe podmonitor -n <namespace> <monitor-name>
```

If the monitor is missing, preview and inspect the owning service stack. Maybe
the chart value did not render it, the typed resource was not added, or the
resource failed during apply.

If the monitor exists but is not selected, check its labels. In this repo the
first suspect is usually the release label:

```bash
kubectl get servicemonitor -n <namespace> <monitor-name> -o yaml
kubectl get podmonitor -n <namespace> <monitor-name> -o yaml
```

Look for:

```yaml
metadata:
  labels:
    release: kube-prometheus-stack
```

Then inspect the selected Service or Pods:

```bash
kubectl get svc -n <namespace> --show-labels
kubectl describe svc -n <namespace> <service-name>
kubectl get pods -n <namespace> --show-labels
kubectl get endpoints -n <namespace> <service-name>
kubectl get endpointslices -n <namespace> -l kubernetes.io/service-name=<service-name>
```

For a `ServiceMonitor`, match this chain:

```text
ServiceMonitor spec.selector.matchLabels
  matches Service metadata.labels
    Service spec.selector
      matches Pod metadata.labels
        Service port name
          matches ServiceMonitor endpoint port
```

For a `PodMonitor`, match this chain:

```text
PodMonitor spec.selector.matchLabels
  matches Pod metadata.labels
    podMetricsEndpoints port or targetPort
      matches a real container metrics port
```

If the target is present but down, test the endpoint from inside the cluster or
with a short-lived port-forward:

```bash
kubectl -n <namespace> port-forward svc/<service-name> 19090:<service-port>
curl -fsS http://127.0.0.1:19090/metrics | head
```

Use the Service port number in the port-forward command, not the ServiceMonitor
port name. If the Service has no endpoints, port-forwarding the Service will
not help; fix the Service selector or workload readiness first.

Prometheus itself is also a useful source of truth:

```bash
kubectl -n monitoring port-forward svc/kube-prometheus-stack-prometheus 9090:9090
curl -fsS 'http://127.0.0.1:9090/api/v1/targets?state=active' | jq '.data.activeTargets[] | {job: .labels.job, health: .health, lastError: .lastError}'
```

If you use the Grafana or Prometheus UI, keep the same state distinction in
mind. A target that does not exist points to selection. A target that is down
points to connectivity, endpoint path, TLS scheme, auth, or exporter health. A
target that is up but lacks a metric points to the application/exporter or to a
dashboard query that expects a different metric name.

Operator logs are useful when a monitor is malformed:

```bash
kubectl -n monitoring logs deploy/kube-prometheus-stack-operator
```

The Prometheus Operator can reject invalid monitor specs, especially when
scrape timeout and interval are inconsistent or when the endpoint fields do not
match the CRD schema.

## Debugging Grafana

Grafana has its own chain:

```text
dashboard JSON in repo
  becomes a ConfigMap
    labeled grafana_dashboard=1
      noticed by the Grafana sidecar
        loaded by Grafana
          querying Prometheus datasource
            returning matching series
```

If Grafana itself is not reachable, start with the monitoring namespace:

```bash
kubectl get pods,svc,ingress,pvc -n monitoring
kubectl describe ingress -n monitoring kube-prometheus-stack-grafana
kubectl logs -n monitoring deploy/kube-prometheus-stack-grafana -c grafana
```

If a dashboard is missing, find the ConfigMap:

```bash
kubectl get configmaps --all-namespaces -l grafana_dashboard=1
kubectl describe configmap -n <namespace> <dashboard-configmap>
```

Check three things:

- The ConfigMap exists in the live cluster.
- It has `grafana_dashboard=1`.
- The dashboard JSON appears under `data` with the filename you expect.

Then check the sidecar:

```bash
kubectl logs -n monitoring deploy/kube-prometheus-stack-grafana -c grafana-sc-dashboard
```

This repo sets Grafana sidecar `skipReload` to true for dashboards and
datasources. That means the sidecar should still sync files, but it will not
call Grafana's reload endpoint after every change. If a newly created dashboard
ConfigMap is present and the sidecar has seen it, but Grafana still does not
show the dashboard, a Grafana pod restart may be required after the Pulumi apply.
Do that deliberately and only after confirming the ConfigMap path is correct.

If a dashboard appears but panels are empty, do not start by editing Grafana.
Check the query in Prometheus:

```bash
kubectl -n monitoring port-forward svc/kube-prometheus-stack-prometheus 9090:9090
```

Then paste the panel's PromQL into the Prometheus expression browser. If the
query returns no series, compare the label filters with live target labels. The
common issue is a dashboard filtering on `service`, `namespace`, `job`, or
`pod` labels that differ from the labels actually produced by the monitor.

If Grafana says the datasource is missing, inspect the dashboard JSON before
changing the platform. Imported dashboards often carry datasource placeholders
or UIDs from the system where they were exported. Prefer a portable datasource
reference or a dashboard variable over a private UID.

## Safe Change Workflow

Monitoring changes can affect every service dashboard, so make small changes
and preview them.

For a service-owned metrics or dashboard change:

```bash
just sync pulumi/<area>/<service>
just check-python
just lint
git diff --check
just preview pulumi/<area>/<service> stack=mx
```

For a monitoring platform change:

```bash
just sync pulumi/ops/monitoring
just check-python
just lint
git diff --check
just preview pulumi/ops/monitoring stack=mx
```

Do not run `pulumi up`, `pulumi destroy`, or `just up` unless the user
explicitly asks for an apply or destructive action.

When changing the Prometheus Operator CRD chart version, also regenerate and
check the repo-local bindings:

```bash
just generate-monitoring-crds
just check-python
just lint
git diff --check
```

Generated SDK files under `pulumi/lib/monitoring_crds` are not hand-edited.
Regenerate them from the CRDs.

For Helm chart upgrades, treat the preview as a migration plan, not as a box to
check. Look for:

- CRD additions or schema changes.
- Prometheus or Grafana resource replacements.
- PVC changes.
- Same-name resources that need `delete_before_replace`.
- Monitor selector changes.
- Default dashboard or datasource changes.
- New high-cardinality metrics or shorter scrape intervals.

For Prometheus storage and retention changes, reason from ingestion rate.
Retention, scrape interval, number of targets, and label cardinality all affect
disk use. A small-looking service change can have a large storage effect if it
adds unbounded labels.

For Grafana access changes, remember that Grafana is exposed through Tailscale
ingress and anonymous users currently have Viewer access. Keep admin access
behind secrets and avoid broadening access casually.

## Practical Triage Checklist

When something "is not in Grafana", ask the more precise question first:

```text
Is Grafana unavailable?
Is the dashboard missing?
Is the dashboard present but empty?
Is the Prometheus target missing?
Is the target present but down?
Is the target up but the metric missing?
Is the metric present but the PromQL label filter wrong?
```

Then follow the chain in order. Most problems are one link earlier than the
screen you are looking at. Empty Grafana panels are often scrape or label
problems. Missing Prometheus targets are often monitor label, namespace, or port
name problems. Missing monitors are often chart value or Pulumi ownership
problems.

The clean fix should usually land in the owning service stack. Save monitoring
stack edits for platform behavior: Prometheus, Grafana, CRDs, retention,
storage, and platform-wide chart configuration.
