FROM node:25-bookworm-slim AS build

ARG PENPOT_REF=mcp-prod-2.14.1
ARG PENPOT_REPO=https://github.com/penpot/penpot.git
ARG PNPM_VERSION=10.28.2
ARG WS_URI=wss://penpot-mcp-ws

WORKDIR /src

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 --branch "${PENPOT_REF}" "${PENPOT_REPO}" penpot

WORKDIR /src/penpot/mcp

RUN npm install --global "pnpm@${PNPM_VERSION}" \
    && pnpm -r install

RUN WS_URI="${WS_URI}" pnpm --filter mcp-plugin run build


FROM nginx:1.30.0-alpine

COPY --from=build /src/penpot/mcp/packages/plugin/dist/ /usr/share/nginx/html/
COPY images/mcp-plugin/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
