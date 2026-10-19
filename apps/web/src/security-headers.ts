// HTTP response headers of the SPA: the one source of truth (#24, ADR-0007 D9).
// Directive by directive, with the reasons: _docs/security-headers.md.
//
// - PRODUCTION_HEADERS: the built SPA. `vite preview` serves them today
//   (vite.config.ts `preview.headers`); M9-09 copies them into the Caddyfile
//   and tests them there.
// - DEVELOPMENT_HEADERS: the Vite dev server (`make dev`, `make e2e`;
//   vite.config.ts `server.headers`). The same policy plus only what the dev
//   server demonstrably needs.
//
// Anything loaded from another origin later (for example presigned
// object-storage URLs in M5) must change the policy here and in
// _docs/security-headers.md. This module is read only by vite.config.ts and
// the unit tests; it never reaches the browser bundle.

/** A Content Security Policy as an ordered list of directives. */
export type CspDirectives = ReadonlyArray<readonly [string, readonly string[]]>;

/** The production policy (the reasons are in _docs/security-headers.md). */
export const PRODUCTION_CSP: CspDirectives = [
  ['default-src', ["'self'"]],
  ['script-src', ["'self'"]],
  // Radix UI's scroll lock (react-remove-scroll) injects <style> elements, and
  // a static SPA cannot use nonces. Inline CSS cannot run script.
  ['style-src', ["'self'", "'unsafe-inline'"]],
  ['img-src', ["'self'", 'data:']],
  ['font-src', ["'self'"]],
  ['connect-src', ["'self'"]],
  ['object-src', ["'none'"]],
  ['base-uri', ["'self'"]],
  ['form-action', ["'self'"]],
  ['frame-ancestors', ["'none'"]],
];

/**
 * The React Refresh preamble that @vitejs/plugin-react injects into the dev
 * server's index.html as an inline module script (`viteReact.preambleCode`
 * with base `/`). Allowed by its hash, not with 'unsafe-inline'; the unit
 * test recomputes the hash from the installed plugin, so an upgrade that
 * changes the preamble fails there.
 */
export const REACT_REFRESH_PREAMBLE_HASH =
  "'sha256-Z2/iFzh9VMlVkEOar1f/oSHWwQk3ve1qk/C2WdsC4Xk='";

/**
 * The development policy: the production policy plus, in `script-src` only,
 * the React Refresh preamble hash. Nothing else differs. The HMR WebSocket
 * needs no addition: Chromium matches ws://<same host:port> with 'self' in
 * `connect-src` (the E2E headers spec runs the app under this policy).
 */
export const DEVELOPMENT_CSP: CspDirectives = PRODUCTION_CSP.map(
  ([name, sources]) =>
    name === 'script-src'
      ? [name, [...sources, REACT_REFRESH_PREAMBLE_HASH]]
      : [name, sources],
);

/** The `Content-Security-Policy` header value of a policy. */
export function serializeCsp(directives: CspDirectives): string {
  return directives
    .map(([name, sources]) => [name, ...sources].join(' '))
    .join('; ');
}

/** The headers of every SPA response. */
export type SecurityHeaders = Readonly<{
  'Content-Security-Policy': string;
  'X-Content-Type-Options': 'nosniff';
  'X-Frame-Options': 'DENY';
}>;

function headersFor(csp: CspDirectives): SecurityHeaders {
  return {
    'Content-Security-Policy': serializeCsp(csp),
    'X-Content-Type-Options': 'nosniff',
    // For browsers without frame-ancestors support.
    'X-Frame-Options': 'DENY',
  };
}

/** Headers of every SPA response in production (M9-09: the Caddyfile). */
export const PRODUCTION_HEADERS = headersFor(PRODUCTION_CSP);

/** Headers of every response of the Vite dev server. */
export const DEVELOPMENT_HEADERS = headersFor(DEVELOPMENT_CSP);
