# LiteLLM

Source: `pulumi/apps/litellm`

LiteLLM in this repository is the shared OpenAI-compatible model proxy. From a client's point of view, it looks like an OpenAI API endpoint: the client sends an HTTP request to `/v1/...`, includes a bearer token, names a model, and receives a response in the same general shape that OpenAI SDKs already understand.

That familiar API shape is the reason this stack exists. Applications, agents, notebooks, and local experiments should not each carry their own provider credentials, pricing assumptions, model aliases, routing rules, and logging behavior. They should know a small contract:

```text
base URL  -> where the proxy is
API key   -> who is allowed to use it
model     -> which route the proxy should invoke
request   -> an OpenAI-compatible payload
```

Everything behind that contract is infrastructure and operations: the proxy pod, the database, provider tokens, model mapping, metrics, spend logs, and private ingress. When the proxy is working well, clients can move between providers or model versions by changing a model name or server-side route instead of rewriting every downstream integration.

## What Pulumi Builds

The Pulumi program installs the LiteLLM Helm chart into its own namespace and gives it the state it needs to act as a durable shared service. It is not only a Deployment.

The stack creates:

- a `litellm` namespace by default
- a PostgreSQL role and database in the shared PostgreSQL stack
- a generated LiteLLM master key
- a generated LiteLLM salt key
- a generated database password for the LiteLLM database role
- Kubernetes Secrets for LiteLLM environment values and database credentials
- a `ReadWriteOnce` PVC for ChatGPT token/session storage
- a single-replica LiteLLM Helm release
- a ClusterIP Service on the LiteLLM proxy port
- a Tailscale Ingress for private access
- a Prometheus ServiceMonitor and LiteLLM Prometheus callback configuration

The current defaults come directly from `pulumi/apps/litellm/__main__.py`:

```text
Namespace:               litellm
Chart:                   oci://docker.litellm.ai/berriai/litellm-helm
Chart version:           1.84.0
Image repository:        docker.litellm.ai/berriai/litellm
Image tag:               main-stable
Replicas:                1
Service name:            litellm
Service type:            ClusterIP
Service port:            4000
Ingress class:           tailscale
Ingress host:            litellm
Proxy URL:               https://litellm
OpenAI base URL:         https://litellm/v1
Database name:           litellm
Database user:           litellm
PostgreSQL stack:        kzh/postgresql/mx
Token PVC:               litellm-chatgpt-tokens
Token PVC size:          2Gi
Token path in pod:       /data/chatgpt
Metrics callback:        prometheus
ServiceMonitor release:  kube-prometheus-stack
```

The single replica is intentional for the current shape. The ChatGPT token volume is `ReadWriteOnce`, and token-backed provider behavior is stateful. Scaling the Deployment is not a harmless capacity knob unless the storage and provider behavior are also designed for multiple writers or multiple independent token stores.

## The Client Contract

Clients should not need to know that this is a Helm chart, that it uses a PostgreSQL database, or that one provider path stores tokens on a PVC. They need the exported base URL, an API key, and a configured model name.

Read the endpoint and key locally:

```bash
cd pulumi/apps/litellm

pulumi stack output --stack mx openai_base_url
pulumi stack output --stack mx --show-secrets master_key
```

Use the output values as standard OpenAI client environment variables:

```bash
export OPENAI_BASE_URL="$(pulumi stack output --stack mx openai_base_url)"
export OPENAI_API_KEY="$(pulumi stack output --stack mx --show-secrets master_key)"
```

The exported `openai_base_url` includes `/v1`. That detail matters. Most OpenAI-compatible clients expect the base URL to end at the versioned API root, not at the service root.

For a backend service, put those values in that service's normal secret/config path. For a local shell, environment variables are fine. Do not hard-code the key into source code, notebooks, checked-in `.env` files, screenshots, or docs.

The master key is powerful. It is acceptable for direct local operation, but it should not be shipped to browser clients or broadly copied into every service. If a client needs long-lived access, prefer a scoped LiteLLM virtual key or a service-specific secret path when that access model is configured. This Pulumi stack currently creates the proxy-level master key; it does not define every downstream client's key lifecycle.

## A Minimal Real Request

Listing models proves that the proxy is reachable, the base URL is right, and the key can authenticate to LiteLLM:

```bash
curl -fsS "$OPENAI_BASE_URL/models" \
  -H "Authorization: Bearer $OPENAI_API_KEY"
```

That is useful, but it does not prove any upstream provider can generate text. A real validation request should call a configured model:

```bash
curl -fsS "$OPENAI_BASE_URL/responses" \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "chatgpt/gpt-5.4-mini",
    "input": "Reply with one short sentence confirming the LiteLLM route works."
  }'
```

The same shape works through an OpenAI-compatible SDK:

```python
import os
from openai import OpenAI

client = OpenAI(
    api_key=os.environ["OPENAI_API_KEY"],
    base_url=os.environ["OPENAI_BASE_URL"],
)

response = client.responses.create(
    model="chatgpt/gpt-5.4-mini",
    input="Reply with one short sentence confirming the LiteLLM route works.",
)

print(response.output_text)
```

Use a small prompt for operations checks. It should be enough to prove routing, auth, token storage, and provider access without creating noisy usage or logs.

## Configured Model Routes

The model list is part of the public API of this stack. In the current Pulumi program, the configured routes are:

```text
chatgpt/gpt-5.5
chatgpt/gpt-5.4-mini
chatgpt/gpt-5.3-codex-spark
```

Each route has:

- `model_name`, which is the name clients send
- `litellm_params.model`, which tells LiteLLM which provider/model path to invoke
- `model_info.mode`, currently `responses`
- pricing metadata for input, cached-input, and output tokens

That separation is important. The name a client sends is not automatically the same thing as a provider's internal model identifier, even though this stack currently keeps them aligned. LiteLLM is the translation layer. A future route could expose a stable client-facing name while changing provider details behind it.

Treat model names like API endpoints. Adding a new name is usually low risk. Renaming a name breaks any client still using the old string. Removing a name can break jobs, agents, notebooks, and services that may not be obvious from this stack alone.

The pricing entries are also operational behavior. LiteLLM uses pricing metadata for accounting and spend reporting. If a route changes provider, mode, or model family, revisit the cost fields in the same change.

## Routing, Providers, and Token State

LiteLLM is a proxy, not a model provider. The proxy accepts the request, authenticates it, looks up the model route, calls the configured provider, records metadata, and returns the response. If the upstream provider rejects auth, is rate-limited, changes behavior, or has an outage, LiteLLM can only surface that failure.

The current routes are ChatGPT-backed routes. They depend on:

```text
CHATGPT_TOKEN_DIR=/data/chatgpt
```

The stack mounts that path from the `litellm-chatgpt-tokens` PVC. Treat that PVC as credential-bearing state. It can contain provider/session material even though it is not a Kubernetes Secret. Do not delete it during cleanup unless the goal is to intentionally reset the provider state and rebuild access.

This is also why pod health is not enough. A pod can start, the Service can have endpoints, and `/v1/models` can return data while a ChatGPT-backed route still fails because the token directory is empty, unreadable, expired, or not mounted.

If you add a conventional API-key provider later, keep provider secrets out of inline chart values. Put provider keys in Kubernetes Secrets or Pulumi secret config, expose them as environment variables, and refer to those variables from LiteLLM config. The route should document the client-facing model name, the provider model it maps to, and how to test that exact path without revealing the secret.

## Keys and Secrets

This stack has several different secret classes. Mixing them up leads to confusing debugging.

`master_key` is the client-facing LiteLLM bearer token exported by the Pulumi stack. Clients use it as `OPENAI_API_KEY` unless they have a more specific LiteLLM key. Rotating it breaks every client still using the old key.

`LITELLM_SALT_KEY` is an internal LiteLLM secret placed in the `litellm-env` Kubernetes Secret. It is not a client API key. Treat rotation as an application state migration, not a routine refresh.

The database password belongs to the `litellm` PostgreSQL role and is stored in the `litellm-db-credentials` Kubernetes Secret. It lets the proxy use its database; it should not be given to clients.

Provider/session material for the current ChatGPT-backed routes lives under `/data/chatgpt` on the PVC. It is credential-like even though it is file state rather than a Pulumi secret output.

Prompt and usage data also deserve care. The current config sets:

```text
store_model_in_db: true
store_prompts_in_spend_logs: true
```

That means the database can contain LiteLLM model state, spend records, and prompt content. Do not paste raw spend-log rows, prompt-bearing logs, or database dumps into issues, docs, or chat. Summarize the failure and redact user content.

## Health Is Layered

There are several different questions people call "is LiteLLM up?" They are not equivalent.

The pod can be running. That means Kubernetes started the container.

The Service can have endpoints. That means Kubernetes sees ready pods behind the ClusterIP Service.

The Tailscale Ingress can resolve and connect. That means the private network path reaches the Service.

`/v1/models` can return. That means the proxy is reachable and the LiteLLM key works.

A `/v1/responses` request can succeed for a specific model. That means the proxy, route, provider auth, token storage, and upstream provider path all worked for that model at that moment.

Use the lightest check that answers the question you actually have, but do not stop at a shallow check when validating provider behavior. After model, provider, token, chart, or key changes, run a real generation request through the changed route.

## Operating Commands

Start from the stack outputs so commands follow the configured namespace and service name:

```bash
cd pulumi/apps/litellm

NS="$(pulumi stack output --stack mx namespace)"
SVC="$(pulumi stack output --stack mx service)"
BASE_URL="$(pulumi stack output --stack mx openai_base_url)"
TOKEN_PVC="$(pulumi stack output --stack mx chatgpt_token_pvc)"
```

Inspect the Kubernetes shape:

```bash
kubectl get pods,deploy,svc,ingress,pvc,secrets -n "$NS"
kubectl get endpoints -n "$NS" "$SVC"
kubectl get pvc -n "$NS" "$TOKEN_PVC"
```

If the monitoring CRDs are installed in the cluster, inspect the ServiceMonitor too:

```bash
kubectl get servicemonitors -n "$NS"
```

Read recent proxy logs without dumping secrets or prompt bodies into a permanent place:

```bash
kubectl logs -n "$NS" deploy/"$SVC" --tail=200
```

If that Deployment name is not present because the chart naming changed, list Deployments first and use the LiteLLM Deployment name shown by Kubernetes:

```bash
kubectl get deploy -n "$NS"
```

Check the token mount from inside the pod by confirming the directory exists. Avoid printing token file contents:

```bash
kubectl exec -n "$NS" deploy/"$SVC" -- sh -lc 'test -d "$CHATGPT_TOKEN_DIR" && pwd && ls -ld "$CHATGPT_TOKEN_DIR"'
```

The Helm chart also enables a migration Job. If the proxy fails during install or chart upgrade, inspect Jobs before focusing on ingress:

```bash
kubectl get jobs -n "$NS"
kubectl get pods -n "$NS" --sort-by=.metadata.creationTimestamp
```

Then read the relevant Job logs by name. Job names are chart-rendered, so list them first instead of guessing.

## Logs, Metrics, and Spend Data

Use pod logs for immediate request failures: authentication errors, model-not-found errors, provider exceptions, rate limits, and startup problems usually show there first.

Use Kubernetes events when the pod does not start cleanly: image pull errors, PVC mount failures, scheduling issues, and failed Jobs are often clearer in `kubectl describe pod` or namespace events than in application logs.

Use Prometheus metrics for aggregate behavior: request counts, latency, status classes, and LiteLLM callback metrics. This stack enables the Prometheus callback and creates a ServiceMonitor labeled for the monitoring release. The metrics endpoint is configured without LiteLLM auth so Prometheus can scrape it inside the cluster; do not treat that as permission to expose metrics publicly.

Use LiteLLM spend/model data carefully. Because prompt storage in spend logs is enabled, those records may contain user-provided text. When reporting an incident, prefer high-level facts such as model name, status code, provider class, and time window. Redact prompt text and tokens.

## Common Failure Paths

If the client cannot resolve or connect to `https://litellm`, check Tailscale, DNS, ingress, and service endpoints. That is a network path problem before it is a model problem.

If the client gets `401` or another auth error, check which key it is using and whether the base URL points at this proxy. A valid provider key is not the same thing as a valid LiteLLM proxy key.

If the client gets model-not-found, compare the exact model string in the request with the configured `model_name` entries. Case, slashes, and suffixes are part of the name.

If `/v1/models` works but `/v1/responses` fails, the proxy is reachable but the selected route or provider path is failing. Check provider auth, token PVC mount, provider limits, and LiteLLM logs.

If failures begin after a pod restart or redeploy, check the token PVC mount and permissions before changing model routes. Token-backed providers can fail even when the container and database are healthy.

If metrics disappear, check the LiteLLM pod, ServiceMonitor, release label, namespace selector, and Prometheus target discovery. The current default release label is `kube-prometheus-stack`.

If an upgrade fails, check the Helm migration Job and database connectivity. The chart is configured with `wait_for_jobs=True`, `cleanup_on_fail=True`, and a 600 second timeout, so failed Jobs may block or roll back the release path.

## Safe Model Changes

A model route is part of the client contract. Make route changes like API changes:

```text
1. Identify the clients that use the model name.
2. Add new routes before removing old ones.
3. Keep old aliases during migration when possible.
4. Update pricing metadata with the provider/model change.
5. Preview the stack.
6. After an intentional apply, test a real request through each changed route.
7. Only remove old routes after clients have moved.
```

For a new route in this repo, edit `chart_values["proxy_config"]["model_list"]` in `pulumi/apps/litellm/__main__.py`. Keep the route explicit:

```python
{
    "model_name": "client-facing/name",
    "model_info": {
        "mode": "responses",
        "input_cost_per_token": usd_per_token(...),
        "cache_read_input_token_cost": usd_per_token(...),
        "output_cost_per_token": usd_per_token(...),
    },
    "litellm_params": {
        "model": "provider/model-name",
    },
}
```

Use `usd_per_token(...)` for costs expressed per million tokens so the units stay consistent with the existing routes. If the provider has not published final rates, say that in a code comment near the pricing choice and revisit it when final rates exist.

Do not use a model rename as a cleanup shortcut. If a better name is needed, add the better name as a new route, migrate clients, and remove the old name later.

## Safe Config Changes

The safest change path is repo-backed and previewed:

```bash
just sync pulumi/apps/litellm
just check-python
just lint
git diff --check
just preview pulumi/apps/litellm stack=mx
```

Use Pulumi for durable configuration. Do not edit chart-rendered ConfigMaps, Secrets, Deployments, or Services by hand as the final fix; Helm/Pulumi will not preserve those edits as source of truth.

Be extra careful with these fields:

- `masterkey`: rotating it breaks clients using the exported master key
- `LITELLM_SALT_KEY`: changing it can affect stored LiteLLM state
- `db`: changing database name, endpoint, or credentials can detach LiteLLM from its stored state
- `replicaCount`: token storage and provider behavior are currently single-replica oriented
- `model_list`: client-facing API surface
- `store_prompts_in_spend_logs`: privacy and retention behavior
- `require_auth_for_metrics_endpoint`: metrics exposure boundary
- `serviceMonitor.labels.release`: Prometheus discovery path
- `CHATGPT_TOKEN_DIR` and the token PVC mount: provider-session state

For chart or image upgrades, read the chart value changes before editing. Helm chart upgrades can rename values, change generated object names, alter migration behavior, or modify how the master key and database settings are wired. A clean Python check does not prove the rendered chart still has the same Kubernetes shape.

## Adding a Provider

A provider addition should answer these questions in the same change:

```text
What client-facing model name will callers use?
Which provider/model does that name route to?
Where is the provider credential stored?
Does the provider need file-backed token/session state?
Which endpoint should be used for validation?
What pricing metadata should LiteLLM record?
Which clients are allowed to use it?
```

For provider credentials, prefer Pulumi secrets or Kubernetes Secrets surfaced as environment variables. Avoid putting literal provider keys in `chart_values`, docs, shell history, or examples. If a provider uses token files, create an explicit PVC/mount and document the reset procedure before relying on it.

After adding the provider, validate in this order:

```text
Kubernetes objects are created.
The proxy pod starts.
/v1/models shows the expected client-facing name.
A real request to the new model succeeds.
Logs and metrics show the request without exposing secret material.
The downstream client can call the same route using its own secret path.
```

## Security Boundaries

The Tailscale Ingress keeps the service private to the network path, but private network access is not a replacement for LiteLLM authentication. Clients still need a LiteLLM key.

The Service is ClusterIP, so in-cluster clients can call it through Kubernetes DNS if they are configured that way. Those clients still need keys too.

The service account sets `automount` to false in the chart values. Keep that posture unless the proxy genuinely needs Kubernetes API access.

The database, spend logs, and token PVC are sensitive operational state. Treat access to them like access to model credentials and user prompts.

## Practical Runbook

For a client integration:

```text
1. Read `openai_base_url`.
2. Put the LiteLLM key in the client's secret store.
3. Configure the client with a current `model_name`.
4. Call `/v1/models` to confirm proxy auth.
5. Call `/v1/responses` with a small prompt to confirm provider routing.
6. Record the chosen model name in the client config or docs.
```

For an incident:

```text
1. Capture the failing client, model name, endpoint path, and time window.
2. Check pod, service endpoint, ingress, and PVC status.
3. Check proxy logs around the time window.
4. Separate proxy auth/config errors from provider errors.
5. Run a small real request with the same model.
6. If token-backed routes fail, verify the token PVC mount.
7. Summarize without exposing keys, token files, prompts, or raw spend rows.
```

For a model/config change:

```text
1. Make the Pulumi change.
2. Run the repo checks and targeted preview.
3. Review the diff for secrets and accidental route removals.
4. Apply only through the normal intentional infrastructure workflow.
5. Validate `/v1/models`.
6. Validate a real request for every changed route.
7. Watch logs and metrics for the first client traffic.
```

The most important operating habit is to verify the same layer you changed. A pod check is enough after a scheduling fix. A model-list check is enough after a key or alias visibility check. A provider or token change needs a real model request.
