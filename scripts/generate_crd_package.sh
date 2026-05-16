#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat >&2 <<'EOF'
Usage:
  generate_crd_package.sh <python_name> <title> <chart> <repo> <version> <crd_file> <out_dir> [release_name]

Example:
  generate_crd_package.sh monitoring_crds "Monitoring CRDs" \
    prometheus-operator-crds https://prometheus-community.github.io/helm-charts 29.0.0 \
    pulumi/ops/monitoring/crds/prometheus-operator-crds-29.0.0.crds.yaml \
    pulumi/lib/monitoring_crds prometheus-operator-crds
EOF
}

if [[ $# -lt 7 || $# -gt 8 ]]; then
    usage
    exit 2
fi

PYTHON_NAME="$1"
TITLE="$2"
CHART_NAME="$3"
CHART_REPO="$4"
CHART_VERSION="$5"
CRD_FILE_ARG="$6"
OUT_DIR_ARG="$7"
RELEASE_NAME="${8:-$CHART_NAME}"
GENERATED_PROVIDER_VERSION="4.31.0"

REPO_ROOT="$(git rev-parse --show-toplevel)"

if [[ "$CRD_FILE_ARG" = /* ]]; then
    CRD_FILE="$CRD_FILE_ARG"
else
    CRD_FILE="$REPO_ROOT/$CRD_FILE_ARG"
fi

if [[ "$OUT_DIR_ARG" = /* ]]; then
    OUT_DIR="$OUT_DIR_ARG"
else
    OUT_DIR="$REPO_ROOT/$OUT_DIR_ARG"
fi

case "$OUT_DIR" in
    "$REPO_ROOT"/pulumi/lib/*) ;;
    *)
        echo "refusing to write unexpected output directory: $OUT_DIR" >&2
        exit 1
        ;;
esac

case "$CRD_FILE" in
    "$REPO_ROOT"/pulumi/*/crds/*.yaml | "$REPO_ROOT"/pulumi/*/*/crds/*.yaml | "$REPO_ROOT"/pulumi/*/*/*/crds/*.yaml) ;;
    *)
        echo "refusing to write unexpected CRD file path: $CRD_FILE" >&2
        exit 1
        ;;
esac

mkdir -p "$(dirname "$CRD_FILE")"

rendered="$(mktemp)"
trap 'rm -f "$rendered"' EXIT

helm template "$RELEASE_NAME" "$CHART_NAME" \
    --repo "$CHART_REPO" \
    --version "$CHART_VERSION" \
    --include-crds \
    > "$rendered"

if [[ -n "${CRD_NAMES:-}" ]]; then
    yq eval 'select(.kind == "CustomResourceDefinition" and (.metadata.name as $name | (strenv(CRD_NAMES) | split(",") | any_c(. == $name))))' "$rendered" > "$CRD_FILE"
else
    yq eval 'select(.kind == "CustomResourceDefinition")' "$rendered" > "$CRD_FILE"
fi

if [[ ! -s "$CRD_FILE" ]]; then
    echo "no matching CRDs rendered from chart ${CHART_NAME} ${CHART_VERSION}" >&2
    exit 1
fi

if [[ -d "$OUT_DIR" ]]; then
    find "$OUT_DIR" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
fi

crd2pulumi \
    --force \
    --pythonName "$PYTHON_NAME" \
    --pythonPath "$OUT_DIR" \
    --version "$GENERATED_PROVIDER_VERSION" \
    "$CRD_FILE"

perl -0pi -e 's/"pulumi>=3\.[0-9]+\.0,<4\.0\.0"/"pulumi>=3.239.0,<4.0.0"/g; s/"pulumi-kubernetes(?:==|>=)4\.[0-9]+\.0(?:,<5\.0\.0)?"/"pulumi-kubernetes>=4.31.0,<5.0.0"/g' "$OUT_DIR/pyproject.toml"
find "$OUT_DIR" -name '*.py' -exec perl -0pi -e 's/[ \t]+$//mg; s/\n+\z/\n/' {} +

cat > "$OUT_DIR/README.md" <<EOF
# ${TITLE}

Generated Pulumi Python bindings for Kubernetes CRDs.

Source chart: ${CHART_NAME} ${CHART_VERSION}
Generated provider package version: ${GENERATED_PROVIDER_VERSION}
EOF
