# Superset

Source: `pulumi/data/analytics/superset`

Apache Superset is the BI workbench for this repo. It is where SQL queries turn
into reusable datasets, charts, and dashboards. The important first principle is
that Superset is not the data warehouse. It is an application that sits above
SQL-speaking systems and stores its own metadata about how to reach them, how to
query them, and how to present the results.

That boundary explains most of the operational behavior. When a chart is wrong,
the source data may be wrong, the SQL may be wrong, the dataset definition may
be wrong, the dashboard filter may be wrong, or Superset may be unable to reach
the datasource. Those are different problems. The fastest way to use Superset
well is to keep those layers separate in your head and debug from the bottom
up.

## What This Stack Deploys

The Pulumi project is intentionally small. It creates one Kubernetes namespace,
installs the Apache Superset Helm chart, enables a private Tailscale ingress,
generates the Superset secret key through Pulumi, and adds a bootstrap script
for a few Python database packages.

Current source-backed shape:

```text
Pulumi project:       pulumi/data/analytics/superset
Runtime:              Python 3.12 through uv
Helm chart:           apache/superset, version 0.15.5
Helm release name:    chart
Namespace:            required Pulumi config value
Ingress class:        tailscale
Ingress host:         superset
Metadata database:    chart-managed PostgreSQL
Cache/broker:         chart-managed Redis
Extra DB packages:    ijson, psycopg2-binary, sqlalchemy-drill
```

The chart-managed PostgreSQL database is Superset's metadata database. It stores
things like users, roles, saved queries, database connection definitions,
datasets, charts, dashboards, annotations, and some encrypted connection
material. It is not where analytical data should live. Analytical data should
stay in the systems Superset connects to, such as Trino, PostgreSQL,
ClickHouse, Drill, or other SQL backends.

The chart-managed Redis instance is used by Superset for cache and background
work plumbing. Treat it as part of the Superset application, not as a general
repo cache.

The generated secret key is also part of the application state. Pulumi creates
it with a `random.RandomBytes` resource and passes it into the chart as a
Superset config override. Do not casually rename or replace that resource. A
Superset secret-key rotation is an application migration because the key is used
for Flask session security and for protecting sensitive Superset metadata. If it
must be rotated, plan it deliberately and use Superset's supported previous-key
rotation path rather than making an accidental Pulumi replacement.

## Opening Superset

The stack exposes Superset through Tailscale:

```text
https://superset
```

That hostname is intentionally private to the tailnet path. If the browser
cannot reach it, first separate "the app is down" from "the private ingress path
is not resolving or routing." The ingress can be broken even when the web pods
are healthy, and the pods can be broken even when Tailscale DNS is fine.

From the project directory, inspect the namespace configured for the live stack:

```bash
cd pulumi/data/analytics/superset
NS="$(pulumi config get namespace --stack mx)"

kubectl get pods,svc,ingress,pvc,jobs -n "$NS"
kubectl describe ingress -n "$NS"
kubectl get deploy,statefulset -n "$NS"
```

Do not add login details to this page. Admin credentials, database passwords,
connection strings with passwords, and Superset secret values belong in the live
system or secret management, not in docs. If credentials need to be recovered,
rotated, or audited, do that from the current cluster and chart state.

## The BI Model

Superset has a small set of concepts. They are simple, but using them in the
right order matters.

A **database** in Superset is a connection to a SQL engine. It might point at
Trino, PostgreSQL, Drill, ClickHouse, or another backend. The database
connection tells Superset which Python SQLAlchemy driver to use, which host and
port to reach, and which credentials or authentication settings to apply.

A **dataset** is Superset's semantic handle for a table, view, or SQL query.
This is where BI meaning starts to become durable. A dataset can give columns
human-readable names, mark a time column, define metrics, hide columns that
should not be charted, and describe the grain of the data. Charts should usually
be built on datasets rather than one-off SQL fragments.

A **chart** is a visualization backed by a dataset query. It chooses dimensions,
metrics, filters, ordering, time grain, and visualization settings. The chart is
where a reusable question becomes a reusable answer.

A **dashboard** is a composed page of charts, filters, and layout. It should not
be the first place where the meaning of the data is invented. A dashboard is at
its best when it collects already-understood datasets and charts into a view
that someone can revisit.

SQL Lab is the workbench that cuts across the model. Use SQL Lab to prove that a
database connection works, inspect raw data, design queries, and validate the
logic that later becomes a dataset or chart. SQL Lab is where you explore.
Datasets, charts, and dashboards are where you preserve.

The practical flow is:

```text
connect to a database
prove the connection in SQL Lab
understand the table or view shape
create or select a dataset
define useful metrics and time columns
build one chart
add dashboard filters
assemble the dashboard
open it again as the intended viewer
```

Skipping steps usually moves confusion upward. If the connection is untested,
the chart error is noisy. If the dataset has no clear grain, the dashboard
filters behave unpredictably. If the chart relies on a complex ad hoc SQL query,
the next person has to rediscover the model before they can make a small
change.

## Connections

Connections are the point where Superset crosses from BI metadata into real data
access. Be careful here. The credentials used by a Superset database connection
define what Superset can query. The private Tailscale ingress controls who can
reach the Superset UI, but it does not replace database authorization.

Use read-only database users for BI. Prefer a credential that can read the
schemas needed for dashboards and nothing else. Avoid superuser credentials,
owner credentials, migration credentials, or application write credentials. If
someone can create a chart or run SQL Lab through a powerful connection, they
effectively inherit that connection's read surface.

For this repo, Trino is often the cleanest first connection because it is the
federated SQL layer. One Superset database can reach multiple catalogs through
Trino, and the Trino catalog grants become a natural boundary for what BI can
see.

The secret-free shape of a Trino connection is:

```text
trino://<user>@trino.trino.svc.cluster.local:8080/<catalog>/<schema>
```

Examples of the shape, not credentialed production values:

```text
trino://bi_reader@trino.trino.svc.cluster.local:8080/tpch/tiny
trino://bi_reader@trino.trino.svc.cluster.local:8080/iceberg/analytics
trino://bi_reader@trino.trino.svc.cluster.local:8080/postgres/public
```

Use a harmless catalog such as `tpch.tiny` for first connection tests when it is
available. That lets you prove Superset, the driver, DNS, and Trino routing
without involving private application data.

Direct PostgreSQL connections are also possible because the Superset bootstrap
script installs `psycopg2-binary`. The secret-free shape is:

```text
postgresql+psycopg2://<user>:<password>@<service>.<namespace>.svc.cluster.local:5432/<database>
```

Do not paste the real password into docs or tickets. Put it only in the
Superset connection form or the secret mechanism being used for the change.

The stack also installs `sqlalchemy-drill`, so Drill-style SQLAlchemy
connections are intended to work without changing the image. Other direct
database engines may need additional Python packages. For example, direct
ClickHouse connections often require a ClickHouse SQLAlchemy dialect or
connector package depending on the chosen connection type. Add missing drivers
through Pulumi by changing the chart bootstrap or by moving to a custom image.
Do not `pip install` inside a running Superset pod and call it fixed; that
change disappears on restart and is not represented in the repo.

Kubernetes service DNS is usually the right address form for in-cluster
databases:

```text
<service>.<namespace>.svc.cluster.local
```

When a connection fails, test from Superset's point of view. A database can be
reachable from your laptop and still unreachable from the Superset pod because
the pod uses cluster DNS, cluster network policy, service ports, and in-cluster
credentials.

## SQL Lab

SQL Lab is the first tool to use for any new datasource. Start with queries that
prove one idea at a time.

For Trino:

```sql
select 1;

show catalogs;

show schemas from tpch;

select *
from tpch.tiny.nation
limit 5;
```

For a real dataset, begin with size, shape, and freshness:

```sql
select count(*) as rows
from analytics.some_table;

select *
from analytics.some_table
limit 20;

select
    min(created_at) as first_created_at,
    max(created_at) as last_created_at
from analytics.some_table;
```

Then check the dimensions and measures you plan to chart:

```sql
select
    status,
    count(*) as rows
from analytics.some_table
group by 1
order by rows desc;
```

These tiny queries are not busywork. They answer different questions:

- Can Superset reach the SQL engine?
- Is the Python driver installed?
- Does the user have permission to see the catalog, schema, table, or view?
- Is the expected data present?
- Does the table have the time column and dimensions needed for charts?
- Are there nulls, unexpected enum values, or duplicate rows that affect the
  intended metric?

When SQL Lab fails, do not start by editing dashboards. Read the error and
classify it. A missing driver, a DNS error, a database authentication error, a
permission error, and a SQL syntax error all point to different layers.

When SQL Lab succeeds but a chart fails, compare the chart-generated query to
the query you proved manually. The chart may be adding a time filter, grouping
by a column with unexpected nulls, using a metric that divides by zero, or
querying through a dataset that no longer matches the source table.

Saved SQL Lab queries are useful for exploration and handoff, but they should
not become the only place where business meaning lives. If a query becomes a
dashboard dependency, consider promoting its logic into a source-system view, a
Trino view, or a clearly named virtual dataset.

## Datasets And The Semantic Layer

The dataset is Superset's local semantic layer. It is where raw database shape
turns into chartable meaning.

Before creating a dataset, answer these questions in plain language:

```text
What does one row mean?
Which column is the event or reporting time?
Which columns are dimensions?
Which columns are measures?
Which measures are additive?
Which filters should dashboard viewers naturally use?
Which columns should stay hidden?
Who is allowed to see this data?
```

The row-grain question is the most important. A table with one row per request,
one row per user, one row per job run, and one row per day can all have a
`status` column, but they cannot be charted the same way. If the grain is not
clear, totals and ratios will be wrong.

Prefer stable views for dashboard-facing datasets. If the raw source schema is
messy, has internal names, contains implementation columns, or changes often,
create a view that expresses the BI contract and point Superset at the view.

Example view shape:

```sql
create view analytics.daily_job_runs as
select
    date_trunc('day', created_at) as day,
    status,
    count(*) as run_count,
    avg(duration_seconds) as avg_duration_seconds
from raw.job_runs
group by 1, 2;
```

Then the Superset dataset can be named `Daily Job Runs`, with `day` marked as
the temporal column, `status` as a dimension, and metrics such as `Run Count`
and `Average Duration` defined once.

Metrics should be named for the question a person asks, not for the expression
alone. `count(*)` is a SQL expression. `Runs` or `Completed Runs` is a BI
metric. Ratios should make their denominator obvious:

```sql
sum(case when status = 'success' then 1 else 0 end)
/
nullif(count(*), 0)
```

That might be named `Success Rate`. The `nullif` is intentional; it keeps an
empty group from becoming a divide-by-zero error.

Calculated columns and virtual datasets are convenient, but avoid scattering the
same logic across many charts. If five charts need the same transformation, put
the transformation in the dataset or, better, in a source view that can be
tested outside Superset too.

Treat dataset edits as user-facing changes. Renaming a metric, changing a time
column, or removing a column can break charts and filters. If a dataset backs a
dashboard, inspect the dependent charts before changing it.

## Charts

A chart is a saved query plus a visualization. The query matters more than the
visualization. A pretty chart with an unclear metric is worse than a plain table
with a trustworthy number.

Build charts one at a time. Start from a dataset that has a known grain and a
known time column. Pick the smallest visualization that answers the question:

- Use a time-series line chart for trend over time.
- Use a bar chart for comparing categories.
- Use a table when the exact rows matter.
- Use a big number only for a metric with an obvious time window.
- Use a heatmap or pivot-style view only when both axes have manageable
  cardinality.

Large raw tables are rarely good dashboards. If the chart needs to scan millions
of rows and then group them the same way every time, create a pre-aggregated
view or table in the source system. Superset can issue the query, but it cannot
make a poorly shaped analytical query cheap.

Time filters deserve special attention. Superset's dashboard-level time range
only works correctly when the chart dataset has the right temporal column. If a
chart seems empty, check whether the dashboard time filter excludes all rows.
This is common when the dataset's time column is an ingestion timestamp but the
viewer expects business event time, or the other way around.

When a chart returns surprising values, open the chart query or reproduce the
same grouping in SQL Lab. Compare:

```sql
select
    date_trunc('day', event_time) as day,
    status,
    count(*) as rows
from analytics.events
where event_time >= current_date - interval '7' day
group by 1, 2
order by 1, 2;
```

That manual query gives you a baseline before you tune visualization settings.

## Dashboards

A dashboard should feel boring in the best way: open it, understand what it is
about, change the expected filters, and trust the numbers. Most dashboard
problems come from trying to use layout to compensate for unclear data modeling.

Build dashboards from stable charts. Add global filters only after the charts
work individually. For each filter, decide which charts it should affect. A
dashboard-wide filter that silently changes an unrelated chart is confusing; a
filter that only applies to half the page without an obvious reason is also
confusing.

A practical dashboard review loop:

```text
open the dashboard with the default filters
change the main time range
change each categorical filter
open the slowest chart query
open as the intended non-admin role
check that empty states are explainable
check that each chart title names the question being answered
```

Use titles that describe the metric and grain. `Runs by Day` is better than
`Chart 14`. `Failed Runs by Service, Last 7 Days` is better than `Failures`
when the time range is fixed by the chart or dashboard.

Avoid dashboards that mix unrelated grains without explaining the relationship.
A page with one chart about individual requests, another about daily aggregates,
and another about user accounts can be useful, but the filters and labels need
to make the grain clear.

If a dashboard is meant to be operational, keep it fast and narrow. If it is
meant to be exploratory, make the datasets and filters obvious enough that a
viewer can ask follow-up questions without editing every chart.

## Permissions

There are two permission systems to think about: Superset permissions and
datasource permissions.

Superset permissions decide who can log in, use SQL Lab, create datasets, edit
charts, edit dashboards, manage connections, and view particular assets.
Datasource permissions decide what the configured database credential can
actually read.

Do not rely on network privacy alone. Tailscale limits who can reach the UI, but
once a user is inside Superset, Superset's roles and the datasource credentials
control the data surface. A user with SQL Lab access to a broad database
connection can often query more than a dashboard viewer.

Good defaults:

- Keep admin access small.
- Use read-only database users for Superset connections.
- Grant dashboard viewing separately from chart and dataset editing.
- Grant SQL Lab access only to people who should run ad hoc queries.
- Prefer role-based grants over one-off individual exceptions.
- For sensitive datasets, enforce the strongest practical restriction in the
  source database or Trino catalog, not only in dashboard layout.

If a dashboard needs row-level separation, design that explicitly. Superset has
features for row-level security, but source-system permissions are easier to
reason about and test. For example, a Trino catalog or schema exposed through a
BI-specific role can be audited independently from the dashboard that uses it.

Connection passwords and secrets stored in Superset are part of the metadata
database. That is another reason the chart-managed PostgreSQL volume matters.
Losing or replacing the metadata database can mean losing not only dashboards
but also the connection definitions needed to rebuild them.

## Debugging From The Bottom Up

When Superset misbehaves, start by locating the layer.

For Kubernetes health:

```bash
cd pulumi/data/analytics/superset
NS="$(pulumi config get namespace --stack mx)"

kubectl get pods,svc,ingress,pvc,jobs -n "$NS"
kubectl get deploy,statefulset -n "$NS"
kubectl describe ingress -n "$NS"
```

If pods are not ready, inspect the relevant pod:

```bash
kubectl describe pod -n "$NS" <pod-name>
kubectl logs -n "$NS" <pod-name> --tail=200
```

If the web deployment is running but the page fails, find the rendered
deployment name and read its logs:

```bash
kubectl get deploy -n "$NS"
kubectl logs -n "$NS" deploy/<superset-web-deployment> --tail=200
```

If initialization or migrations failed, jobs are often the useful object:

```bash
kubectl get jobs -n "$NS"
kubectl logs -n "$NS" job/<job-name> --tail=200
```

If the page loads but login fails, check the Superset web logs, initialization
jobs, chart-managed secrets, and metadata database readiness. Do not solve a
login problem by changing unrelated ingress settings.

If SQL Lab reports a missing driver, the fix belongs in the Pulumi-managed
bootstrap script or image. The current bootstrap script installs:

```text
ijson
psycopg2-binary
sqlalchemy-drill
```

Any other driver needs to be added deliberately. After changing driver
installation, preview the stack and verify that the package is available in a
fresh pod after the change is applied.

If SQL Lab reports DNS or connection failures, exec into the Superset web pod
and test from there:

```bash
kubectl exec -n "$NS" -it deploy/<superset-web-deployment> -- sh
```

From inside the pod, test the target service name and port with the tools
available in the image. The exact utilities can vary by image, so use what is
present rather than installing debugging packages into the running container.

If SQL Lab reports permission errors, verify the database user or Trino role.
Superset may be working perfectly while the datasource correctly refuses the
query.

If a dashboard is slow, inspect the generated SQL before scaling Superset. Many
slow BI dashboards are slow because every refresh performs a large scan, a high
cardinality group-by, or repeated joins on raw tables. Pre-aggregation, source
views, and better dataset metrics often help more than webserver CPU.

If charts show stale data, check all of these before assuming the source system
is wrong:

- dashboard time range
- chart-level filters
- dataset SQL or table mapping
- Superset cache behavior
- source view freshness
- source ingestion freshness

If a chart works for an admin but not for a viewer, inspect Superset roles and
asset permissions first, then datasource permissions. The same dashboard can
fail differently depending on whether the user lacks dashboard access, dataset
access, database access, SQL Lab access, or source-system privileges.

## Safe Changes

Make durable changes in Pulumi or in the source database layer, not by mutating
running pods. The repo is the record of how Superset should be deployed.

For code and chart-value changes, use the normal repo checks:

```bash
just sync pulumi/data/analytics/superset
just check-python
just lint
git diff --check
just preview pulumi/data/analytics/superset stack=mx
```

Do not run `pulumi up`, `pulumi destroy`, or `just up` unless the user has
explicitly asked for an apply or destructive action.

Treat Helm chart upgrades as migrations. A Superset chart upgrade can change
deployment names, init jobs, environment variables, default security settings,
metadata migrations, worker behavior, and bundled dependencies. Read the chart
and application release notes, preview the diff, and make sure the metadata
database can be recovered before applying.

Treat metadata database changes as data changes. The chart currently manages
PostgreSQL itself. Moving Superset metadata to a shared PostgreSQL service would
be a real migration involving backup, restore, connection-secret handling,
cutover, and rollback planning. It should not be bundled into a cosmetic chart
cleanup.

Preserve stable Pulumi names unless replacement is intended. In this project,
renaming the namespace resource, Helm release, generated secret-key resource, or
important chart values can cause replacements or application-level disruption.
If a rename is unavoidable, use Pulumi aliases or a planned migration path where
appropriate.

Add Python database drivers through the chart bootstrap or a custom image. The
current bootstrap script writes into Superset's Python environment at startup.
That is acceptable for a small set of drivers, but if the list grows or package
resolution becomes fragile, a custom image may become cleaner.

After a change is applied by an operator, verify the application path that users
care about:

```text
open https://superset
log in with the expected role
open SQL Lab
run a tiny query through a real datasource
open an existing dashboard
change the main dashboard filter
confirm no chart reports a driver, permission, or metadata error
```

That final user-path check matters because Pulumi can report a clean Kubernetes
deployment while Superset still fails at the BI layer.

## A Good First Dashboard

For a new data source, keep the first dashboard intentionally small. The goal is
to prove the full path rather than build the final analytics surface.

Start in SQL Lab:

```sql
select
    date_trunc('day', created_at) as day,
    status,
    count(*) as rows
from analytics.example_events
where created_at >= current_date - interval '14' day
group by 1, 2
order by 1, 2;
```

If that works, create a view with the same shape in the source system or Trino:

```sql
create view analytics.daily_example_events as
select
    date_trunc('day', created_at) as day,
    status,
    count(*) as event_count
from raw.example_events
group by 1, 2;
```

Then create a Superset dataset on `analytics.daily_example_events`:

```text
Dataset name:       Daily Example Events
Time column:        day
Dimension:          status
Metric:             Event Count = sum(event_count)
Default time range: last 14 days, if appropriate
```

Build one time-series chart for `Event Count` by `day`, split by `status`.
Add it to a dashboard with a time filter. Open the dashboard as the intended
viewer. If that path works, the connection, permissions, dataset, metric, chart,
dashboard, and ingress are all proven at least once.

From there, grow the semantic layer deliberately. Add metrics that answer real
questions, promote repeated SQL into views, and keep permissions close to the
source data. Superset is most valuable when it makes the trusted path easy to
reuse.
