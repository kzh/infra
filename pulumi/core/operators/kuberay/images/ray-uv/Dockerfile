ARG PYTHON_VERSION=3.13.13
ARG UV_VERSION=0.11.14
FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM python:${PYTHON_VERSION}-slim-bookworm

ARG RAY_VERSION=2.55.1

COPY --from=uv /uv /uvx /usr/local/bin/

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

LABEL org.opencontainers.image.source="https://github.com/kzh/infra"

ENV HOME=/home/ray \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        iproute2 \
        libgomp1 \
        libjemalloc2 \
        procps \
        tini \
    && rm -rf /var/lib/apt/lists/* \
    && if ! getent group users >/dev/null; then groupadd --gid 100 users; fi \
    && useradd --uid 1000 --gid users --create-home --shell /bin/bash ray \
    && uv pip install --system --no-cache \
        "ray[default,client]==${RAY_VERSION}" \
    && mkdir -p /tmp/ray \
    && chown -R ray:users /home/ray /tmp/ray

USER ray
WORKDIR /home/ray
