# Local development image for the React SPA (Vite dev server). Build context:
# repository root. Not a production image (that is edge.Dockerfile, #610).
# The Node 24 pin is set by _docs/adr/0001-runtime-version-pins.md (accepted).
# Keep in sync with .nvmrc and engines.node in the root package.json.
#
# Stages (#672 D5): `base` (dependencies and the web source), `test` (the web
# and API client Vitest suites, `make test-web-docker`) and the dev server as
# the LAST stage, so Compose `web` (no target) builds it, unchanged.
FROM node:24-slim AS base

# pnpm comes from the root packageManager field through Corepack (bundled with
# Node 24); no global pnpm install and no download prompt.
ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0

WORKDIR /app

# Install dependencies first so the layer is reused while only source changes.
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml .npmrc ./
COPY apps/web/package.json apps/web/
COPY packages/api-client packages/api-client
RUN corepack pnpm install --frozen-lockfile

COPY apps/web apps/web

# Test image (#672 D5): security-headers.caddy.test.ts reads the edge's
# Caddyfile. Runs from the workspace root (`pnpm --filter ...`).
FROM base AS test

COPY infrastructure/docker/edge/Caddyfile infrastructure/docker/edge/Caddyfile

# Dev server (the default build): Compose `web`.
FROM base

WORKDIR /app/apps/web

EXPOSE 5173

# strictPort comes from vite.config.ts, so a port conflict fails instead of
# moving to another port.
CMD ["corepack", "pnpm", "exec", "vite", "--host", "0.0.0.0", "--port", "5173"]
