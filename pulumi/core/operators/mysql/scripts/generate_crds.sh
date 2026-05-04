#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$PROJECT_DIR/../../../.." && pwd)"

CHART_NAME="mysql-operator"
CHART_REPO="https://mysql.github.io/mysql-operator/"
GENERATED_PROVIDER_VERSION="4.30.0"
STACK_CONFIG="$PROJECT_DIR/Pulumi.mx.yaml"
CHART_VERSION="${1:-}"

if [[ -z "$CHART_VERSION" ]]; then
    CHART_VERSION="$(
        awk -F': ' '/mysql-operator:chartVersion:/ { print $2; exit }' "$STACK_CONFIG"
    )"
fi

if [[ -z "$CHART_VERSION" ]]; then
    echo "could not determine MySQL operator chart version" >&2
    exit 1
fi

CRD_DIR="$PROJECT_DIR/crds"
CACHE_DIR="$PROJECT_DIR/.chart-cache"
CRD_FILE="$CRD_DIR/mysql-operator-${CHART_VERSION}.crds.yaml"
OUT_DIR="$REPO_ROOT/pulumi/lib/mysql_operator_crds"

case "$OUT_DIR" in
    "$REPO_ROOT"/pulumi/lib/mysql_operator_crds) ;;
    *)
        echo "refusing to write unexpected output directory: $OUT_DIR" >&2
        exit 1
        ;;
esac

mkdir -p "$CRD_DIR" "$CACHE_DIR"
if [[ -d "$CACHE_DIR/$CHART_NAME" ]]; then
    find "$CACHE_DIR/$CHART_NAME" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
    rmdir "$CACHE_DIR/$CHART_NAME"
fi

helm pull "$CHART_NAME" \
    --repo "$CHART_REPO" \
    --version "$CHART_VERSION" \
    --untar \
    --untardir "$CACHE_DIR"

helm show crds "$CACHE_DIR/$CHART_NAME" > "$CRD_FILE"

if [[ -d "$OUT_DIR" ]]; then
    find "$OUT_DIR" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
fi

crd2pulumi \
    --force \
    --pythonName mysql_operator_crds \
    --pythonPath "$OUT_DIR" \
    --version "$GENERATED_PROVIDER_VERSION" \
    "$CRD_FILE"

perl -0pi -e 's/"pulumi>=3\.165\.0,<4\.0\.0"/"pulumi>=3.234.0,<4.0.0"/g; s/"pulumi-kubernetes==4\.23\.0"/"pulumi-kubernetes>=4.30.0,<5.0.0"/g' "$OUT_DIR/pyproject.toml"
printf '%s\n' \
    '# MySQL Operator CRDs' \
    '' \
    'Generated Pulumi Python bindings for the MySQL operator CRDs.' \
    '' \
    "Source chart: ${CHART_NAME} ${CHART_VERSION}" \
    "Generated provider package version: ${GENERATED_PROVIDER_VERSION}" \
    > "$OUT_DIR/README.md"
