# Classora security headers

The HTTP security headers of the web app (SPA) and the API, and the CSRF
rules they work with. Implemented by M2-06 (issue #24; ADR-0007 D9).

The SPA's headers have **one source of truth**: the TypeScript module
[`apps/web/src/security-headers.ts`](../apps/web/src/security-headers.ts)
(`PRODUCTION_HEADERS`, `DEVELOPMENT_HEADERS`). Change the policy there and in
this document together; the web unit tests check the invariants below.

## Where the headers are enforced

| Where | Headers | Status |
| ----- | ------- | ------ |
| Vite dev server (`make dev`, `make e2e`) | `DEVELOPMENT_HEADERS`, enforced (not `-Report-Only`) through `server.headers` in `apps/web/vite.config.ts` | Now. The E2E spec `e2e/tests/security/headers.spec.ts` checks the headers, zero CSP violations during a sign-in round trip and that the app cannot be framed. |
| `vite preview` of a production build | `PRODUCTION_HEADERS` through `preview.headers` | Now. Checked by hand (see below). |
| Caddy in front of the built SPA | `PRODUCTION_HEADERS` | **M9-09** copies them into the Caddyfile and tests them automatically against the built SPA, together with HSTS, `Referrer-Policy` and `Permissions-Policy` (edge headers, out of scope here). |
| Storybook | none (Storybook keeps its own headers) | Not a deployed surface. |

## Production Content Security Policy

```
default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'
```

| Directive | Value | Reason |
| --------- | ----- | ------ |
| `default-src` | `'self'` | Anything not listed below loads only from the app's own origin. |
| `script-src` | `'self'` | Only the built bundles of the app's origin run. No inline script, no `eval`, no other host. |
| `style-src` | `'self' 'unsafe-inline'` | Radix UI's scroll lock (`react-remove-scroll`, used by dialogs) injects `<style>` elements, and a static SPA has no server-side templating to add nonces. Inline CSS cannot run script, and `script-src` stays exactly `'self'`. Accepted by the owner (issue #24, E2). |
| `img-src` | `'self' data:` | App images and small inline (`data:`) images, for example icons. |
| `font-src` | `'self'` | Fonts are served by the app itself. |
| `connect-src` | `'self'` | The SPA calls only its own origin; the API is behind the same origin (`/api/*`), so no CORS. |
| `object-src` | `'none'` | No plugins (`<object>`, `<embed>`). |
| `base-uri` | `'self'` | An injected `<base>` cannot redirect relative URLs to another host. |
| `form-action` | `'self'` | Forms submit only to the app's origin. |
| `frame-ancestors` | `'none'` | No page may frame the app (clickjacking). |

Also on every SPA response:

- `X-Content-Type-Options: nosniff`;
- `X-Frame-Options: DENY`, for browsers without `frame-ancestors` support.

Neither policy contains `'unsafe-eval'` or any host other than `'self'`.

## Development additions

The Vite dev server needs one thing the built app does not:

- **`script-src` gets the hash of the React Refresh preamble.**
  `@vitejs/plugin-react` injects a small inline module script into the dev
  server's `index.html` (hot reloading of React components). It is allowed by
  its `sha256-` hash, not with `'unsafe-inline'`. A web unit test recomputes
  the hash from the installed plugin, so an upgrade that changes the preamble
  fails there first.

Nothing else differs: `default-src`, `object-src`, `base-uri`, `form-action`
and `frame-ancestors` are identical. The HMR WebSocket needs no addition,
because Chromium matches `ws://` to the same host and port with `'self'` in
`connect-src` (the E2E spec runs the app under this policy). Storybook and
Vitest reuse `vite.config.ts` but serve their own inline scripts, so the
config does not apply the policy to them.

## Checking the production policy by hand

Until M9-09 automates it in Caddy, check the production build once by hand
(with `make dev` running, so `/api` answers):

```bash
source ~/.nvm/nvm.sh && nvm use 24
corepack pnpm --filter @classora/web build
(cd apps/web && corepack pnpm exec vite preview --host 127.0.0.1 --port 4173 --strictPort) &
curl -sI http://127.0.0.1:4173/ | grep -i content-security-policy   # the production policy
# Open http://127.0.0.1:4173 in Chromium: the shell renders and the console
# shows no CSP violation.
kill %1
```

## API headers

Every API response, errors and documentation pages included, carries exactly
one `Cache-Control: no-store` (responses hold personal data and are never
cached) and one `X-Content-Type-Options: nosniff` (JSON is never sniffed as
HTML or script). A pure ASGI middleware outside the request-context middleware
sets them (`apps/api/app/core/security_headers.py`), so the last-resort `500`
has them too. The API sends no Content Security Policy: it answers JSON only,
and FastAPI's `/docs` page needs inline scripts and a CDN.

## Origin and CSRF

In short (details in [`api.md`](api.md) "CSRF and `Origin`"):

- `POST`, `PUT`, `PATCH` and `DELETE` need exactly one `Origin` equal to
  `APP_ORIGIN` (every route), and on signed-in routes an `X-CSRF-Token` equal
  to the session's token, which login and `/auth/me` return. Both failures are
  `403 csrf_failed`.
- The SPA keeps the token in memory only, never in a cookie or web storage.
- The browser must open the app at exactly `APP_ORIGIN` (locally
  `http://127.0.0.1:5173`, not `localhost`).

## Later changes

- **Another origin.** Anything the SPA loads from or sends to another origin
  (for example presigned object-storage URLs for documents in M5) needs a
  change to `connect-src`/`img-src` in `apps/web/src/security-headers.ts` and
  in this document, with the reason.
- **New UI libraries.** A library that injects inline `<script>` elements or
  needs `eval` violates the policy; the E2E headers spec catches it under the
  development policy. Prefer a library that does not.
- **M9-09.** Copy `PRODUCTION_HEADERS` into the Caddyfile for the SPA, test
  them there with zero CSP violations against the built SPA, and add the edge
  headers. CloudFront must forward `Origin` and `X-CSRF-Token` for `/api/*`.
  The production `APP_ORIGIN` is the `https://` customer hostname.
