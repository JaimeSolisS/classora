import { describe, expect, it } from 'vitest';
import caddyfile from '../../../infrastructure/docker/edge/Caddyfile?raw';
import { PRODUCTION_HEADERS } from './security-headers';

// Header parity (#610): the edge's Caddyfile sends PRODUCTION_HEADERS value for
// value. The edge smoke test (`make smoke-edge`) checks the served headers too.

/** The `Name "value"` lines of a Caddyfile snippet's `header { … }` block. */
function snippetHeaders(source: string, snippet: string): Map<string, string> {
  const start = source.indexOf(`(${snippet}) {`);
  if (start === -1) throw new Error(`no (${snippet}) snippet in the Caddyfile`);
  const end = source.indexOf('\n}', start);
  const headers = new Map<string, string>();
  for (const line of source.slice(start, end).split('\n')) {
    const match = /^\s*([A-Za-z-]+)\s+"(.*)"\s*$/.exec(line);
    if (match?.[1] && match[2] !== undefined) headers.set(match[1], match[2]);
  }
  return headers;
}

const edge = snippetHeaders(caddyfile, 'spa_security_headers');

describe('the edge Caddyfile', () => {
  it.each(Object.entries(PRODUCTION_HEADERS))(
    'sends %s exactly as PRODUCTION_HEADERS',
    (name, value) => {
      expect(edge.get(name)).toBe(value);
    },
  );

  it('adds only the documented edge headers', () => {
    expect([...edge.keys()].sort()).toEqual(
      [
        ...Object.keys(PRODUCTION_HEADERS),
        'Permissions-Policy',
        'Referrer-Policy',
      ].sort(),
    );
    expect(edge.get('Referrer-Policy')).toBe('strict-origin-when-cross-origin');
  });

  it('sends HSTS on HTTPS only', () => {
    expect(caddyfile).toContain('@https protocol https');
    expect(caddyfile).toContain(
      'header @https Strict-Transport-Security "max-age=31536000"',
    );
  });
});
