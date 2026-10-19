import { describe, expect, it } from 'vitest';
import { safeReturnPath } from './returnPath';

// B5: the `next` parameter of /login only ever leads to a path on this origin.

describe('safeReturnPath', () => {
  it.each([
    ['/students?x=1#a', '/students?x=1#a'],
    ['/', '/'],
    ['/a/b/c', '/a/b/c'],
    ['/e2e-return-path?x=1', '/e2e-return-path?x=1'],
    ['/search?q=a%26b', '/search?q=a%26b'],
  ])('keeps %s', (raw, expected) => {
    expect(safeReturnPath(raw)).toBe(expected);
  });

  it.each([
    ['a protocol-relative URL', '//evil.example'],
    ['a backslash after the slash', '/\\evil.example'],
    ['an absolute URL', 'https://evil.example'],
    ['a javascript: URL', 'javascript:alert(1)'],
    ['encoded slashes', '%2F%2Fevil.example'],
    ['an encoded backslash', '/%5Cevil.example'],
    ['the login page', '/login?next=/x'],
    ['the login page with a trailing slash', '/login/'],
    ['the login page in capitals', '/LOGIN'],
    ['the login page encoded', '/%6Cogin'],
    ['a tab that the URL parser drops', '/\t/evil.example'],
    ['a newline', '/\n/evil.example'],
    ['an encoded newline', '/%0A/evil.example'],
    ['a relative path', 'students'],
    ['malformed percent-encoding', '/%E0%A4%A'],
    ['an empty value', ''],
    ['a missing value (null)', null],
    ['a missing value (undefined)', undefined],
  ])('falls back to / for %s', (_name, raw) => {
    expect(safeReturnPath(raw)).toBe('/');
  });
});
