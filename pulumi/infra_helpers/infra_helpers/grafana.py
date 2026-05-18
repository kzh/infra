from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

import pulumi_kubernetes as k8s

import pulumi


def default_dashboard_name(dashboard_file: str) -> str:
    return dashboard_file.removesuffix(".json")


def dashboard_config_maps(
    *,
    name_prefix: str,
    namespace: pulumi.Input[str],
    dashboards_dir: Path,
    dashboard_files: Iterable[str],
    labels: Mapping[str, str] | None = None,
    opts: pulumi.ResourceOptions | None = None,
    dashboard_name: Callable[[str], str] = default_dashboard_name,
) -> list[k8s.core.v1.ConfigMap]:
    config_maps: list[k8s.core.v1.ConfigMap] = []
    config_map_labels = {
        "grafana_dashboard": "1",
        **(labels or {}),
    }

    for dashboard_file in dashboard_files:
        name = dashboard_name(dashboard_file)
        config_maps.append(
            k8s.core.v1.ConfigMap(
                f"{name_prefix}-{name}",
                metadata=k8s.meta.v1.ObjectMetaArgs(
                    name=f"{name_prefix}-{name}",
                    namespace=namespace,
                    labels=config_map_labels,
                ),
                data={
                    dashboard_file: (dashboards_dir / dashboard_file).read_text(
                        encoding="utf-8"
                    ),
                },
                opts=opts,
            )
        )

    return config_maps
