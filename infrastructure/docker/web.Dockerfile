# Local development image for the React SPA (Vite dev server). Build context:
# repository root. Not a production image (that is M9-06).
# The Node 24 pin is set by _docs/adr/0001-runtime-version-pins.md (accepted).
# Keep in sync with .nvmrc and engines.node in the root package.json.
FROM node:24-slim

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

WORKDIR /app/apps/web

EXPOSE 5173

# strictPort comes from vite.config.ts, so a port conflict fails instead of
# moving to another port.
CMD ["corepack", "pnpm", "exec", "vite", "--host", "0.0.0.0", "--port", "5173"]
