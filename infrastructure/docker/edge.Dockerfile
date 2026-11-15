# The edge image (published as `web`, ADR-0010, ADR-0011): Caddy serving the
# built SPA and proxying /api to the API, with the production security headers.
# Built for linux/arm64 in CI (`images` job) and for the host platform locally
# (`make build-images`). Build context: repository root.
# The development image for the Vite dev server is web.Dockerfile.

# Stage 1: the production build of the SPA (`make build-web`, apps/web/dist).
# The Node 24 pin is set by _docs/adr/0001-runtime-version-pins.md (accepted).
# Keep in sync with .nvmrc and engines.node in the root package.json.
FROM node:24-slim AS build

# pnpm comes from the root packageManager field through Corepack.
ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0

WORKDIR /app

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml .npmrc ./
COPY apps/web/package.json apps/web/
COPY packages/api-client packages/api-client
RUN corepack pnpm install --frozen-lockfile

COPY apps/web apps/web
RUN corepack pnpm --filter @classora/web build

# Stage 2: stock Caddy (no custom modules), pinned by its multi-arch index
# digest (the tag is informational). Runs Caddy's default command:
# `caddy run --config /etc/caddy/Caddyfile --adapter caddyfile`.
FROM caddy:2.11.4@sha256:0c994536bddb66445885237f1a5dcc1916bccea922661c76b4e9fc24061f9b52

# Alpine security fixes released after the Caddy image was built (the image
# scan fails on a fixable CRITICAL finding, ADR-0016).
RUN apk upgrade --no-cache

COPY infrastructure/docker/edge/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /app/apps/web/dist /srv

# Extra site blocks (for example Grafana when monitoring is colocated, M9-09)
# are mounted here; the Caddyfile imports every *.caddy file in it.
RUN mkdir -p /etc/caddy/sites.d
