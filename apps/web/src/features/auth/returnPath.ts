// The `next` parameter of /login (#28 B5): the one open-redirect guard of the
// web app. Only a path on this origin is accepted; anything else is "/".

const FALLBACK = '/';
// A throwaway base: a relative path resolved against it must keep it.
const BASE = 'https://classora.invalid';
// C0 controls, DEL and C1 controls. The URL parser silently drops tab and
// newline, which could turn "/\t/evil" into "//evil".
// eslint-disable-next-line no-control-regex
const CONTROL_CHARACTERS = /[\u0000-\u001f\u007f-\u009f]/;

function isLocalPath(value: string): boolean {
  return (
    value.startsWith('/') &&
    !value.startsWith('//') &&
    !value.startsWith('/\\') &&
    !CONTROL_CHARACTERS.test(value)
  );
}

/**
 * The validated return path, or "/". `raw` is the value of the `next` query
 * parameter (already decoded once by URLSearchParams). It is accepted only if
 * both it and its decoded form start with a single "/" (not "//" or "/\"),
 * contain no control characters, have no scheme, resolve to this origin, and
 * do not point at /login.
 */
export function safeReturnPath(raw: string | null | undefined): string {
  if (!raw) {
    return FALLBACK;
  }
  let decoded: string;
  try {
    decoded = decodeURIComponent(raw);
  } catch {
    return FALLBACK;
  }
  if (!isLocalPath(raw) || !isLocalPath(decoded)) {
    return FALLBACK;
  }
  let url: URL;
  try {
    url = new URL(raw, BASE);
    decodeURIComponent(url.pathname);
  } catch {
    return FALLBACK;
  }
  if (url.origin !== BASE) {
    return FALLBACK;
  }
  // The router matches paths case-insensitively and decoded.
  const path =
    decodeURIComponent(url.pathname).toLowerCase().replace(/\/+$/, '') || '/';
  if (path === '/login') {
    return FALLBACK;
  }
  return `${url.pathname}${url.search}${url.hash}`;
}
