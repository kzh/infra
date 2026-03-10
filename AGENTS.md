# Repository Guidelines

## Project Structure & Module Organization
This repository is an infrastructure monorepo built around Pulumi Python projects. The main unit of work is `pulumi/<domain>/<service>/`, for example `pulumi/core/networking/tailscale` or `pulumi/data/databases/postgres`. Most Pulumi projects contain `__main__.py`, `Pulumi.yaml`, `pyproject.toml`, and `uv.lock`. Root-level docs and formatting rules live in `README.md` and `.editorconfig`.

## Build, Test, and Development Commands
Run commands from the target project directory unless noted otherwise.

- `uv sync`: install or update project dependencies.
- `pulumi stack select <stack>` or `pulumi stack init <stack>`: choose or create a stack.
- `pulumi preview`: review infrastructure changes before apply.
- `pulumi up`: apply the current project changes.
- `pulumi destroy`: tear down only the selected project and stack.
- `just build` / `just push`: build or publish the JupyterHub singleuser image from `pulumi/data/analytics/jupyterhub`.

## Coding Style & Naming Conventions
Follow `.editorconfig`: 4 spaces for Python, 2 spaces for YAML, LF endings, final newline. Keep Pulumi entrypoints small and explicit in `__main__.py`. Prefer stable, descriptive resource names and labels such as `{"app": "mlflow"}`. Do not hard-code credentials; use `pulumi config set --secret <key> <value>`.

## Testing Guidelines
There is no centralized automated test suite. Validation is deployment-focused:

- Run `pulumi preview` before `pulumi up`.
- After apply, verify with `kubectl get all -n <namespace>` and `pulumi stack output`.
- If you add reusable Python logic, place tests in `tests/test_*.py` and run them with `uv run pytest`.

## Commit & Pull Request Guidelines
Keep commits scoped to one service or stack. Preferred commit subjects follow either Conventional Commits like `feat: ...`, `chore: ...`, or component-scoped messages like `rustfs: add single-node deployment`. PRs should list impacted paths and stacks, key config changes without secrets, `pulumi preview` results, and rollback notes.

## Security & Configuration Tips
Confirm the target cluster before changes with `kubectl config current-context`. Treat stack config as sensitive. Store secrets in Pulumi’s secret store, not in source-controlled files.
