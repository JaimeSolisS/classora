import viteReact from '@vitejs/plugin-react';
import { describe, expect, it } from 'vitest';
import {
  DEVELOPMENT_CSP,
  DEVELOPMENT_HEADERS,
  PRODUCTION_CSP,
  PRODUCTION_HEADERS,
  REACT_REFRESH_PREAMBLE_HASH,
  serializeCsp,
  type CspDirectives,
} from './security-headers';

// The SPA's security headers (#24 E3; _docs/security-headers.md).

function directives(header: string): Map<string, string[]> {
  return new Map(
    header.split(';').map((part) => {
      const [name = '', ...sources] = part.trim().split(/\s+/);
      return [name, sources];
    }),
  );
}

const POLICIES = [
  ['production', PRODUCTION_HEADERS],
  ['development', DEVELOPMENT_HEADERS],
] as const;

describe.each(POLICIES)('the %s headers', (_, headers) => {
  const csp = directives(headers['Content-Security-Policy']);

  it('forbid framing, plugins, base and form targets elsewhere', () => {
    expect(csp.get('frame-ancestors')).toEqual(["'none'"]);
    expect(csp.get('object-src')).toEqual(["'none'"]);
    expect(csp.get('base-uri')).toEqual(["'self'"]);
    expect(csp.get('form-action')).toEqual(["'self'"]);
    expect(csp.get('default-src')).toEqual(["'self'"]);
  });

  it('never allow eval or another host', () => {
    const header = headers['Content-Security-Policy'];
    expect(header).not.toContain("'unsafe-eval'");
    expect(header).not.toMatch(/https?:|wss?:|\*/);
    expect(csp.get('script-src')).not.toContain("'unsafe-inline'");
  });

  it('also send nosniff and X-Frame-Options DENY', () => {
    expect(headers).toEqual({
      'Content-Security-Policy': expect.any(String) as string,
      'X-Content-Type-Options': 'nosniff',
      'X-Frame-Options': 'DENY',
    });
  });
});

describe('the production policy', () => {
  it('is exactly the documented one', () => {
    expect(PRODUCTION_HEADERS['Content-Security-Policy']).toBe(
      "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline';" +
        " img-src 'self' data:; font-src 'self'; connect-src 'self';" +
        " object-src 'none'; base-uri 'self'; form-action 'self';" +
        " frame-ancestors 'none'",
    );
  });

  it("allows scripts from 'self' only", () => {
    const csp = directives(PRODUCTION_HEADERS['Content-Security-Policy']);
    expect(csp.get('script-src')).toEqual(["'self'"]);
  });
});

describe('the development policy', () => {
  it('differs from production only by the React Refresh preamble hash', () => {
    const production = new Map(PRODUCTION_CSP);
    const development = new Map(DEVELOPMENT_CSP);

    expect([...development.keys()]).toEqual([...production.keys()]);
    for (const [name, sources] of production) {
      const expected =
        name === 'script-src'
          ? [...sources, REACT_REFRESH_PREAMBLE_HASH]
          : sources;
      expect(development.get(name), name).toEqual(expected);
    }
  });

  it('allows the preamble of the installed @vitejs/plugin-react by hash', async () => {
    const preamble = viteReact.preambleCode.replace('__BASE__', '/');
    const digest = await crypto.subtle.digest(
      'SHA-256',
      new TextEncoder().encode(preamble),
    );
    const base64 = btoa(String.fromCharCode(...new Uint8Array(digest)));

    expect(REACT_REFRESH_PREAMBLE_HASH).toBe(`'sha256-${base64}'`);
  });
});

describe('serializeCsp', () => {
  it('joins directives with "; " and sources with spaces', () => {
    const csp: CspDirectives = [
      ['default-src', ["'self'"]],
      ['img-src', ["'self'", 'data:']],
    ];
    expect(serializeCsp(csp)).toBe("default-src 'self'; img-src 'self' data:");
  });
});
