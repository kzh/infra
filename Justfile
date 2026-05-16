set shell := ["bash", "-lc"]

generated_crd_excludes := "--exclude pulumi/lib"

# List Pulumi project directories.
projects:
	@find pulumi -name Pulumi.yaml -not -path '*/.venv/*' -print | sed 's#/Pulumi.yaml$##' | sort

# Install or update dependencies in one Pulumi project.
sync project:
	cd {{project}} && uv sync

# Regenerate typed Python bindings for the MySQL operator CRDs.
generate-mysql-crds version="":
	cd pulumi/core/operators/mysql && ./scripts/generate_crds.sh "{{version}}"

# Regenerate MySQL operator CRD bindings and fail if committed files changed.
check-mysql-crds version="":
	cd pulumi/core/operators/mysql && ./scripts/generate_crds.sh "{{version}}"
	git diff --exit-code -- pulumi/core/operators/mysql/crds pulumi/lib/mysql_operator_crds

# Regenerate typed Python bindings for Prometheus Operator monitoring CRDs.
generate-monitoring-crds version="29.0.0":
	just generate-crd-package monitoring_crds "Monitoring CRDs" prometheus-operator-crds https://prometheus-community.github.io/helm-charts "{{version}}" podmonitors.monitoring.coreos.com,servicemonitors.monitoring.coreos.com pulumi/ops/monitoring prometheus-operator-crds

# Regenerate typed Python bindings for Tailscale operator CRDs.
generate-tailscale-crds version="1.96.5":
	just generate-crd-package tailscale_crds "Tailscale Operator CRDs" tailscale-operator https://pkgs.tailscale.com/helmcharts "{{version}}" proxyclasses.tailscale.com pulumi/core/networking/tailscale tailscale-operator

# Regenerate typed Python bindings for KubeRay operator CRDs.
generate-kuberay-crds version="1.6.1":
	just generate-crd-package kuberay_crds "KubeRay Operator CRDs" kuberay-operator https://ray-project.github.io/kuberay-helm "{{version}}" rayclusters.ray.io pulumi/core/operators/kuberay kuberay-operator

# Regenerate typed Python bindings for Spark operator CRDs.
generate-spark-crds version="2.5.0":
	just generate-crd-package spark_operator_crds "Spark Operator CRDs" spark-operator https://kubeflow.github.io/spark-operator "{{version}}" sparkconnects.sparkoperator.k8s.io pulumi/data/analytics/spark spark-operator

# Regenerate typed Python bindings for ClickHouse operator CRDs.
generate-clickhouse-crds version="0.27.0":
	just generate-crd-package clickhouse_operator_crds "ClickHouse Operator CRDs" altinity-clickhouse-operator https://docs.altinity.com/clickhouse-operator "{{version}}" clickhouseinstallations.clickhouse.altinity.com pulumi/data/analytics/clickhouse altinity-clickhouse-operator

# Generate one typed CRD package from a Helm chart. Use crd_names="-" to include every CRD in the chart.
generate-crd-package python_name title chart repo version crd_names owner_dir release_name="":
	@set -euo pipefail; \
	crd_file="{{owner_dir}}/crds/{{chart}}-{{version}}.crds.yaml"; \
	out_dir="pulumi/lib/{{python_name}}"; \
	args=( "{{python_name}}" "{{title}}" "{{chart}}" "{{repo}}" "{{version}}" "$crd_file" "$out_dir" ); \
	if [ -n "{{release_name}}" ]; then args+=( "{{release_name}}" ); fi; \
	if [ "{{crd_names}}" != "-" ]; then export CRD_NAMES="{{crd_names}}"; else unset CRD_NAMES; fi; \
	./scripts/generate_crd_package.sh "${args[@]}"

# Regenerate every typed CRD package used by local Pulumi stacks.
generate-crds:
	just generate-mysql-crds
	just generate-monitoring-crds
	just generate-tailscale-crds
	just generate-kuberay-crds
	just generate-spark-crds
	just generate-clickhouse-crds

# Regenerate every typed CRD package and fail if committed files changed.
check-crds:
	just generate-crds
	git diff --exit-code -- pulumi/core/operators/mysql/crds pulumi/ops/monitoring/crds pulumi/core/networking/tailscale/crds pulumi/core/operators/kuberay/crds pulumi/data/analytics/spark/crds pulumi/data/analytics/clickhouse/crds pulumi/lib/mysql_operator_crds pulumi/lib/monitoring_crds pulumi/lib/tailscale_crds pulumi/lib/kuberay_crds pulumi/lib/spark_operator_crds pulumi/lib/clickhouse_operator_crds

# Preview one Pulumi project. Pass stack=mx when you want an explicit stack.
preview project stack="":
	cd {{project}} && if [ -n "{{stack}}" ]; then pulumi preview --stack "{{stack}}"; else pulumi preview; fi

# Preview every mx stack managed by this checkout.
preview-all mode="normal":
	@set -euo pipefail; \
	if [ "{{mode}}" = "normal" ]; then \
		flags=(--non-interactive --suppress-outputs --suppress-permalink --suppress-progress --color never); \
	elif [ "{{mode}}" = "refresh" ]; then \
		flags=(--refresh --run-program --non-interactive --suppress-outputs --suppress-permalink --suppress-progress --color never); \
	else \
		echo "mode must be normal or refresh" >&2; \
		exit 2; \
	fi; \
	stack="mx"; \
	logdir="/tmp/pulumi-${stack}-previews-$(date +%Y%m%d%H%M%S)"; \
	mkdir -p "$logdir"; \
	failed=0; \
	while IFS= read -r project; do \
		if ! (cd "$project" && pulumi stack ls --json | jq -e --arg stack "$stack" '.[] | select(.name == $stack)' >/dev/null); then continue; fi; \
		name="$(printf '%s__%s' "$project" "$stack" | tr '/' '_')"; \
		logfile="$logdir/$name.log"; \
		printf '\n== %s [%s] ==\n' "$project" "$stack"; \
		if (cd "$project" && pulumi preview --stack "$stack" "${flags[@]}") >"$logfile" 2>&1; then \
			awk '/Resources:/,/Duration:/' "$logfile" | tail -25; \
		else \
			rc=$?; \
			printf 'FAILED exit=%s\n' "$rc"; \
			grep -E '^(Diagnostics:|error:|Resources:|Duration:|Previewing|  pulumi:|  kubernetes:)' "$logfile" | tail -80 || true; \
			failed=1; \
		fi; \
	done < <(just projects); \
	printf '\nLOGDIR %s\n' "$logdir"; \
	exit "$failed"

# Apply one Pulumi project. Pass stack=mx when you want an explicit stack.
up project stack="":
	cd {{project}} && if [ -n "{{stack}}" ]; then pulumi up --stack "{{stack}}"; else pulumi up; fi

# Syntax-check all Pulumi Python entrypoints without contacting a cluster.
check-python:
	find pulumi -name __main__.py -not -path '*/.venv/*' -print0 | xargs -0 -n1 python -m py_compile

# Run the Python style and static checks used by this repository.
lint:
	ruff check pulumi --preview {{generated_crd_excludes}}
	ruff format --check pulumi {{generated_crd_excludes}}

# Format Pulumi Python entrypoints.
format:
	ruff format pulumi {{generated_crd_excludes}}
