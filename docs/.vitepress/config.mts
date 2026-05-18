import { defineConfig } from 'vitepress'

export default defineConfig({
  title: 'kzh Infra',
  description: 'Pulumi Kubernetes infrastructure handbook',
  cleanUrls: true,
  lastUpdated: true,
  themeConfig: {
    logo: '/topology.svg',
    search: {
      provider: 'local'
    },
    nav: [
      { text: 'Repository', link: '/repository/' },
      { text: 'Stacks', link: '/stacks/' },
      { text: 'Operations', link: '/repository/operations' }
    ],
    outline: {
      level: [2, 3]
    },
    sidebar: [
      {
        text: 'Repository',
        items: [
          { text: 'Overview', link: '/repository/' },
          { text: 'Layout', link: '/repository/layout' },
          { text: 'Pulumi Model', link: '/repository/pulumi-model' },
          { text: 'Workflow', link: '/repository/workflow' },
          { text: 'Configuration', link: '/repository/configuration' },
          { text: 'CRDs', link: '/repository/crds' },
          { text: 'Observability', link: '/repository/observability' },
          { text: 'Operations', link: '/repository/operations' },
          { text: 'Security', link: '/repository/security' }
        ]
      },
      {
        text: 'Stacks',
        items: [
          { text: 'Stack Index', link: '/stacks/' },
          { text: 'Core', link: '/stacks/core/' },
          { text: 'Data', link: '/stacks/data/' },
          { text: 'Apps', link: '/stacks/apps/' },
          { text: 'Ops', link: '/stacks/ops/' }
        ]
      },
      {
        text: 'Core Networking',
        collapsed: true,
        items: [
          { text: 'Cloudflare Tunnel', link: '/stacks/core/networking/cf-tunnel' },
          { text: 'Tailscale Operator', link: '/stacks/core/networking/tailscale' }
        ]
      },
      {
        text: 'Core Operators',
        collapsed: true,
        items: [
          { text: 'CloudNativePG', link: '/stacks/core/operators/cnpg' },
          { text: 'KubeRay', link: '/stacks/core/operators/kuberay' },
          { text: 'MySQL Operator', link: '/stacks/core/operators/mysql' }
        ]
      },
      {
        text: 'Core Security',
        collapsed: true,
        items: [
          { text: 'cert-manager', link: '/stacks/core/security/cert-manager' },
          { text: 'Vault', link: '/stacks/core/security/vault' }
        ]
      },
      {
        text: 'Data',
        collapsed: true,
        items: [
          { text: 'PostgreSQL', link: '/stacks/data/databases/postgres' },
          { text: 'CockroachDB', link: '/stacks/data/databases/cockroach' },
          { text: 'ConvexDB', link: '/stacks/data/databases/convexdb' },
          { text: 'RustFS', link: '/stacks/data/storage/rustfs' },
          { text: 'Flink', link: '/stacks/data/streaming/flink' },
          { text: 'Kafka', link: '/stacks/data/streaming/kafka' },
          { text: 'Redpanda', link: '/stacks/data/streaming/redpanda' },
          { text: 'Airflow', link: '/stacks/data/workflow/airflow' },
          { text: 'Dagster', link: '/stacks/data/workflow/dagster' },
          { text: 'n8n', link: '/stacks/data/workflow/n8n' },
          { text: 'Temporal', link: '/stacks/data/workflow/temporal' },
          { text: 'ClickHouse', link: '/stacks/data/analytics/clickhouse' },
          { text: 'JupyterHub', link: '/stacks/data/analytics/jupyterhub' },
          { text: 'MLflow', link: '/stacks/data/analytics/mlflow' },
          { text: 'Slurm', link: '/stacks/data/analytics/slurm' },
          { text: 'Spark', link: '/stacks/data/analytics/spark' },
          { text: 'Superset', link: '/stacks/data/analytics/superset' },
          { text: 'Trino', link: '/stacks/data/analytics/trino' }
        ]
      },
      {
        text: 'Apps',
        collapsed: true,
        items: [
          { text: 'Coder', link: '/stacks/apps/coder' },
          { text: 'golink', link: '/stacks/apps/golink' },
          { text: 'Hermes', link: '/stacks/apps/hermes' },
          { text: 'Immich', link: '/stacks/apps/immich' },
          { text: 'LiteLLM', link: '/stacks/apps/litellm' },
          { text: 'MediaWiki', link: '/stacks/apps/mediawiki' },
          { text: 'Stitch', link: '/stacks/apps/stitch' },
          { text: 'WordPress', link: '/stacks/apps/wordpress' }
        ]
      },
      {
        text: 'Ops',
        collapsed: true,
        items: [
          { text: 'Monitoring', link: '/stacks/ops/monitoring' }
        ]
      }
    ],
    socialLinks: [
      { icon: 'github', link: 'https://github.com/kzh/infra' }
    ]
  }
})
