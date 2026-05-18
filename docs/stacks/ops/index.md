# Operations

Operations stacks provide the shared feedback loop for the cluster. Application,
data, networking, and operator stacks can all converge successfully and still
fail the thing a person actually cares about: a page loads, a job completes, a
database accepts writes, a queue drains, or a controller reconciles. The ops
area is where those assumptions become measurable.

Today the ops area is centered on [Monitoring](/stacks/ops/monitoring). That
stack installs the Prometheus Operator CRDs, `kube-prometheus-stack`, Prometheus,
Grafana, and the discovery paths that let other stacks publish their own scrape
targets and dashboards. Treat the monitoring stack as platform plumbing, not as
a warehouse for every service-specific chart.

## Monitoring As Feedback Loop

Monitoring is useful when it closes the loop between a repo change and observed
behavior. A stack change creates or changes runtime objects. Those objects emit
metrics. Prometheus scrapes the metrics. Grafana turns them into views that are
fast to read under pressure. Incidents then feed back into the repo as better
selectors, clearer dashboards, sharper alerts, safer defaults, or more honest
docs.

That loop matters because Kubernetes health is only one layer of truth. A Pod
can be `Ready` while the app is returning errors. A Service can exist while it
has no useful endpoints. A dashboard can load while every panel is empty. An
operator can be running while its custom resources are stuck. Good operations
docs and dashboards should make those gaps visible.

The healthy habit is to ask what decision a signal supports. If a panel would
not change what you do during an incident, it is probably decoration. If a
metric tells you whether to roll back, scale, repair storage, inspect an
operator, or follow the network path, it belongs close to the owning stack.

## Ownership Split

The monitoring stack owns the shared platform:

- Prometheus Operator CRDs such as `ServiceMonitor`, `PodMonitor`, and
  `PrometheusRule`.
- The `kube-prometheus-stack` release in the `monitoring` namespace.
- Prometheus retention, storage, target discovery, and scrape execution.
- Grafana availability, datasource wiring, and dashboard discovery.
- The cross-stack contract for labels such as `release=kube-prometheus-stack`
  and `grafana_dashboard=1`.

Service stacks own the meaning of their own telemetry. A Spark change should own
Spark scrape targets and Spark dashboards. Tailscale should own proxy/operator
metrics. Airflow should own scheduler, worker, DAG, and StatsD visibility.
Cloudflare Tunnel should own tunnel transport signals. This repo follows that
pattern by keeping dashboard JSON beside the stack that understands it, usually
under a service-local `dashboards/` directory, then publishing it through a
Grafana-discoverable ConfigMap.

The split keeps reviews useful. If a dashboard changes because the service
changed, reviewers see the runtime change and the visibility change together.
The monitoring stack should change when the shared observability platform
changes: chart versions, CRDs, storage, retention, Grafana discovery, ingress,
or global Prometheus behavior.

## Scrape Path

When metrics vanish, follow the scrape path instead of starting with Grafana
JSON:

```text
process/exporter emits metrics
  -> Pod exposes a metrics port
  -> Service names and exposes that port
  -> ServiceMonitor or PodMonitor selects the workload
  -> Prometheus Operator renders scrape config
  -> Prometheus scrapes a target
  -> Grafana queries Prometheus with PromQL
```

`PodMonitor` can skip the Service hop, but the debugging idea is the same:
prove each handoff before moving to the next one. Label selectors, namespace
selectors, port names, paths, and release labels are all part of the contract.
A ServiceMonitor that points at port `metrics` will not work if the Service
renamed that port to `http-metrics`. A monitor without the release label
selected by `kube-prometheus-stack` can exist in Kubernetes while Prometheus
ignores it.

The target state tells you where to look:

- Missing target: Prometheus did not discover the monitor, or the monitor did
  not select a Service or Pod.
- Down target: Prometheus found the target but cannot scrape it; check
  endpoints, port names, paths, TLS, and network reachability.
- Up target with empty panels: the exporter is reachable, so check metric
  names, labels, PromQL, dashboard variables, and whether the service version
  still emits the expected series.

Useful read-only checks:

```bash
kubectl get servicemonitors,podmonitors,prometheusrules --all-namespaces
kubectl get configmaps --all-namespaces -l grafana_dashboard=1
kubectl get svc -n <namespace> <service> --show-labels
kubectl get endpoints -n <namespace> <service>
kubectl describe servicemonitor -n <namespace> <monitor>
kubectl describe podmonitor -n <namespace> <monitor>
```

For the platform itself:

```bash
kubectl get pods,svc,ingress,pvc -n monitoring
kubectl get prometheus,grafana,alertmanager -n monitoring
```

## Dashboards

Dashboards should be treated as operational code. They should live where the
service lives, be reviewed with the service, and answer questions the owner
would ask while debugging. The monitoring stack only needs to make discovery
work; it does not need to know every service's domain model.

Good dashboard checks start before Grafana. If a service dashboard is missing,
look for the dashboard ConfigMap and its `grafana_dashboard=1` label. If the
dashboard exists but panels are empty, look at Prometheus targets and metric
names. If the targets are missing, inspect the monitor selectors. If the targets
are down, inspect the Service endpoints and the metrics endpoint.

The best dashboards usually have a small number of decisive views:

- Availability: whether the thing is reachable and serving successful work.
- Error rate: failed requests, failed reconciliations, failed jobs, or failed
  task attempts.
- Latency or duration: request latency, DAG duration, query duration, reconcile
  duration, or job runtime.
- Saturation: CPU, memory, PVC usage, queue depth, consumer lag, executor
  capacity, connection pools, or worker slots.
- Control-plane health: operator reconcile errors, rollout status, target
  scrape health, and custom resource conditions.
- Data-path health: database primary availability, object storage failures,
  ingestion stalls, compaction pressure, or replication lag.

Avoid panels whose only purpose is to show that a metric exists. Avoid labels
with unbounded values such as request IDs, user IDs, raw paths, generated object
names, or pod-unique data unless there is a deliberate cardinality plan. High
cardinality consumes Prometheus storage quickly and can turn a local service
change into a cluster-wide monitoring problem.

## Incident Signals

During an incident, start with the user-visible symptom and work backward
through ownership boundaries. If Grafana itself is unavailable, inspect the
monitoring namespace, Grafana ingress, and persistent volume first. If Grafana
works but a service dashboard is empty, move to Prometheus targets before
editing dashboard JSON. If Prometheus targets are healthy, then inspect PromQL,
metric names, and service-specific labels.

Some signals are broadly useful across stacks:

- `up` and target health show whether Prometheus can scrape a component.
- Endpoint count shows whether a Service has backing Pods.
- Ingress or proxy metrics show whether traffic reaches the cluster edge.
- 5xx/error counters show whether traffic succeeds after it arrives.
- Queue depth, consumer lag, pending tasks, and worker slots show backpressure.
- PVC usage and database availability show whether durable state is at risk.
- Operator reconcile errors show whether declarative state is stuck.
- Restart rate and OOM events show whether the runtime is unstable.
- Prometheus storage and scrape sample volume show whether observability itself
  is under stress.

`kubectl top` and Metrics Server are useful for quick resource checks, but they
are not the same path as Prometheus scraping. If `kubectl top` is broken, debug
Metrics Server. If Grafana panels are empty, debug Prometheus targets and
dashboard discovery.

## Changing Operations Safely

For monitoring stack changes, use the normal repo validation path and stop at a
preview unless an apply was explicitly requested:

```bash
just sync pulumi/ops/monitoring
just check-python
just lint
git diff --check
just preview pulumi/ops/monitoring stack=mx
```

CRD and chart changes can affect every stack that publishes monitors, rules, or
dashboards. When Prometheus Operator CRDs change, regenerate the repo-local
bindings through the CRD generation targets instead of hand-editing generated
SDK files. When dashboard discovery changes, verify at least one service-owned
dashboard. When scrape selection changes, verify at least one service-owned
`ServiceMonitor` or `PodMonitor` from outside the `monitoring` namespace.

The success condition is not merely "the monitoring stack previewed cleanly."
The success condition is that the cluster still has a working feedback loop:
Prometheus has targets, Grafana can query them, service-owned dashboards appear,
and the signals you would use in an incident are still visible.
