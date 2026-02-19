# Repository Guidelines

## Project Structure & Module Organization
This repository is an infra monorepo built around Pulumi Python projects plus one Modal service.
- `pulumi/<domain>/<service>/` is the main unit of work (for example `pulumi/core/networking/tailscale`, `pulumi/data/databases/postgres`).
- Each Pulumi project typically contains `__main__.py`, `Pulumi.yaml`, `pyproject.toml`, and `uv.lock`.
- `modal/golink/` contains the Modal deployment code (`golink.py`).
- Root docs and formatting rules live in `README.md` and `.editorconfig`.

## Build, Test, and Development Commands
Run commands from the target project directory.
- `uv sync`: install project dependencies.
- `pulumi stack select <stack>` (or `pulumi stack init <stack>`): choose environment stack.
- `pulumi preview`: required dry-run before apply.
- `pulumi up`: apply infrastructure changes.
- `pulumi destroy`: tear down only the current project+stack.
- `modal deploy modal/golink/golink.py`: deploy the Modal app.
- `just build` / `just push` (in `pulumi/data/analytics/jupyterhub`): build or publish the singleuser image.

## Coding Style & Naming Conventions
- Follow `.editorconfig`: 4 spaces for Python, 2 for YAML, LF line endings, final newline.
- Keep Pulumi entrypoints small and explicit in `__main__.py`.
- Prefer hyphenated infrastructure/resource names and stable labels such as `{"app": "<name>"}`.
- Never hard-code credentials; use `pulumi config set --secret <key> <value>`.

## Testing Guidelines
There is no centralized automated test suite today. Verification is deployment-focused:
- Always run `pulumi preview` before `pulumi up`.
- After apply, validate with `kubectl` (for example `kubectl get all -n <namespace>`) and check outputs via `pulumi stack output`.
- If you add reusable Python logic, add `tests/test_*.py` and run with `uv run pytest`.

## Commit & Pull Request Guidelines
Recent history uses both Conventional Commit prefixes and component-scoped subjects.
- Preferred examples: `feat: ...`, `chore: ...`, `refactor: ...`, or `<component>: ...` (for example `jupyterhub: upgrade chart...`).
- Keep commits scoped to one service/stack change.
- PRs should include impacted paths/stacks, key config changes (without secrets), `pulumi preview` results, and rollback notes.

## Security & Configuration Tips
- Confirm context before changes: `kubectl config current-context`.
- Treat stack config as sensitive; store secrets in Pulumi’s secret store, not source files.
