# Marimo

Source: `pulumi/data/analytics/marimo`

Marimo is the reactive notebook workspace for this cluster. It is for fast,
reproducible exploration close to the data services: Trino, Spark Connect,
MLflow, Kafka, RustFS/S3, ClickHouse, and PostgreSQL.

The stack creates:

- a `marimo` namespace;
- a persistent `marimo-workspace` PVC for notebooks, the local Python venv,
  and Marimo editor settings;
- a generated Marimo token stored in the `marimo-secrets` Secret;
- a Deployment running `ghcr.io/marimo-team/marimo` pinned by digest;
- a ClusterIP Service on port `8080`;
- a private Tailscale Ingress with a stack-configured public host;
- read-only PostgreSQL grants through a `marimo_reader` role;
- environment variables for the cluster's analytics services.

The pod starts Marimo in edit mode with token authentication:

```text
marimo edit --headless --host 0.0.0.0 --port 8080 --proxy <public_url>
```

The `public_host` stack config should match the browser-facing Tailscale
hostname. The deployment passes that value to Marimo through `--proxy`, so
Marimo builds API, websocket, and startup URLs for the external HTTPS site
rather than the pod's internal `0.0.0.0:8080` listener.

Marimo writes editor preferences to `/home/appuser/.config/marimo`. The stack
mounts that directory from the workspace PVC so settings such as package
manager selection, AI provider setup, display preferences, and keymaps survive
pod restarts. The initial config selects `uv` as Marimo's package manager when
no saved config exists yet.

The token is intentionally not written into docs. Retrieve it only when opening
the UI:

```bash
cd pulumi/data/analytics/marimo
pulumi stack output --show-secrets --stack mx token
```

## Service Wiring

Marimo receives non-secret connection hints through the `marimo-environment`
ConfigMap and secret values through `marimo-secrets`.

Useful environment variables:

```text
TRINO_SQLALCHEMY_URI
TRINO_CATALOGS
SPARK_REMOTE
SPARK_UI_URL
MLFLOW_TRACKING_URI
KAFKA_BOOTSTRAP_SERVERS
RUSTFS_ENDPOINT_URL
CLICKHOUSE_HTTP_URL
POSTGRES_HOST
POSTGRES_DATABASES
```

Secret-backed variables include PostgreSQL, ClickHouse, and RustFS credentials.
Do not print them in notebooks, logs, docs, or pull requests.

Those secret-backed names are `POSTGRES_PASSWORD`, `CLICKHOUSE_USER`,
`CLICKHOUSE_PASSWORD`, `AWS_ACCESS_KEY_ID`, and `AWS_SECRET_ACCESS_KEY`.

Trino is the safest first SQL path because it already federates PostgreSQL
catalogs, ClickHouse, Iceberg on RustFS, and sample data. Start there before
opening direct database connections.

## Smoke Checks

Check the stack and live objects:

```bash
cd pulumi/data/analytics/marimo
pulumi stack output --stack mx
kubectl get pods,svc,ingress,pvc -n marimo
```

From inside the pod, a tiny Trino check should work:

```bash
kubectl exec -n marimo deploy/marimo -- \
  /workspace/.venv/bin/python -c 'import os, trino; c=trino.dbapi.connect(host=os.environ["TRINO_HOST"], port=8080, user="marimo", catalog="tpch", schema="tiny"); cur=c.cursor(); cur.execute("select count(*) from nation"); print(cur.fetchone())'
```

Open the `url` stack output, enter the token, and open `welcome.py`. The
notebook shows which service environment variables are present without printing
secret values.
