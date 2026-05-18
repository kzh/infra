import hashlib

import pulumi_kubernetes as k8s

import pulumi


def secret_env_var(
    name: str,
    secret_name: pulumi.Input[str],
    key: str,
) -> k8s.core.v1.EnvVarArgs:
    return k8s.core.v1.EnvVarArgs(
        name=name,
        value_from=k8s.core.v1.EnvVarSourceArgs(
            secret_key_ref=k8s.core.v1.SecretKeySelectorArgs(
                name=secret_name,
                key=key,
            )
        ),
    )


def stable_task_id(values: list[object]) -> str:
    payload = "|".join(str(value) for value in values).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]
