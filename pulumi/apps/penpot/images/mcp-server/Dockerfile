FROM node:25-bookworm-slim AS build

ARG PENPOT_REF=mcp-prod-2.14.1
ARG PENPOT_REPO=https://github.com/penpot/penpot.git
ARG PNPM_VERSION=10.28.2

WORKDIR /src

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 --branch "${PENPOT_REF}" "${PENPOT_REPO}" penpot

WORKDIR /src/penpot/mcp

RUN npm install --global "pnpm@${PNPM_VERSION}" \
    && pnpm -r install \
    && pnpm --filter mcp-server run build


FROM node:25-bookworm-slim

WORKDIR /app

COPY --from=build /src/penpot/mcp /app
RUN mkdir -p /app/data \
    && cp -r /app/packages/server/data/* /app/data/

ENV PENPOT_MCP_SERVER_HOST=0.0.0.0 \
    PENPOT_MCP_SERVER_PORT=4401 \
    PENPOT_MCP_WEBSOCKET_PORT=4402 \
    PENPOT_MCP_REPL_PORT=4403 \
    PENPOT_MCP_REMOTE_MODE=true

EXPOSE 4401 4402 4403

CMD ["node", "packages/server/dist/index.js"]
