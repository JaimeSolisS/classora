import { describe, expect, it } from 'vitest';
import {
  checkCatalogs,
  findDuplicateKeys,
  formatProblems,
  type Catalogs,
} from './check';

// Every catalog file on disk, so a namespace added in one language only is
// found too (the app bundle imports them explicitly in index.ts).
const files = import.meta.glob<unknown>('./locales/*/*.json', {
  eager: true,
  import: 'default',
});

// The same files as raw text: JSON.parse hides a repeated key.
const rawFiles = import.meta.glob<string>('./locales/*/*.json', {
  eager: true,
  import: 'default',
  query: '?raw',
});

function realCatalogs(): Catalogs {
  const catalogs: Catalogs = {};
  for (const [path, content] of Object.entries(files)) {
    const [, language, namespace] = /\/locales\/([^/]+)\/([^/]+)\.json$/.exec(
      path,
    )!;
    (catalogs[language!] ??= {})[namespace!] = content;
  }
  return catalogs;
}

describe('translation catalogs', () => {
  it('have es and en with the same namespaces and keys', () => {
    const catalogs = realCatalogs();
    expect(Object.keys(catalogs).sort()).toEqual(['en', 'es']);
    const problems = checkCatalogs(catalogs);
    // The message lists every problem with language, namespace and key.
    expect(problems, `\n${formatProblems(problems)}\n`).toEqual([]);
  });

  it('repeat no key within the same object', () => {
    expect(Object.keys(rawFiles).length).toBeGreaterThan(0);
    const duplicates = Object.entries(rawFiles).flatMap(([path, text]) => {
      const [, language, namespace] = /\/locales\/([^/]+)\/([^/]+)\.json$/.exec(
        path,
      )!;
      return findDuplicateKeys(text).map(
        (key) => `${language}/${namespace}: ${key}`,
      );
    });
    // The message names the file and the key path, e.g. en/errors: x.
    expect(duplicates, `\n${duplicates.join('\n')}\n`).toEqual([]);
  });

  it('give the language endonyms identically in both languages', () => {
    const { es, en } = realCatalogs() as {
      es: { common: { language: { options: unknown } } };
      en: { common: { language: { options: unknown } } };
    };
    expect(en.common.language.options).toEqual(es.common.language.options);
  });
});

describe('checkCatalogs', () => {
  const clean: Catalogs = {
    es: {
      common: {
        a: { b: 'Hola {{name}}' },
        items_one: '{{count}} elemento',
        items_many: '{{count}} de elementos',
        items_other: '{{count}} elementos',
      },
    },
    en: {
      common: {
        a: { b: 'Hello {{name}}' },
        items_one: '{{count}} item',
        items_other: '{{count}} items',
      },
    },
  };

  it('accepts a clean pair (es-only _many is not a mismatch)', () => {
    expect(checkCatalogs(clean)).toEqual([]);
  });

  it('reports a namespace file present in one language only', () => {
    const problems = checkCatalogs({
      es: { ...clean.es, errors: { generic: 'Error' } },
      en: clean.en!,
    });
    expect(problems).toEqual([
      expect.objectContaining({
        kind: 'missing-namespace',
        language: 'en',
        namespace: 'errors',
      }),
    ]);
  });

  it('reports missing keys in both directions, with nested key paths', () => {
    const problems = checkCatalogs({
      es: { common: { a: { b: 'x', c: 'y' } } },
      en: { common: { a: { b: 'x' }, d: { e: 'z' } } },
    });
    expect(problems.map((p) => [p.kind, p.language, p.key])).toEqual([
      ['missing-key', 'en', 'a.c'],
      ['missing-key', 'es', 'd.e'],
    ]);
    expect(formatProblems(problems)).toContain('en/common: a.c');
  });

  it.each([
    ['empty', ''],
    ['whitespace', '   '],
    ['number', 3],
    ['array', ['a']],
    ['null', null],
  ])('reports a %s value', (_label, value) => {
    const problems = checkCatalogs({
      es: { common: { k: value } },
      en: { common: { k: 'ok' } },
    });
    expect(problems).toEqual([
      expect.objectContaining({
        kind: 'invalid-value',
        language: 'es',
        key: 'k',
      }),
    ]);
  });

  it('reports different interpolation placeholders', () => {
    const problems = checkCatalogs({
      es: { common: { k: 'Hola {{nombre}}' } },
      en: { common: { k: 'Hello {{name}}' } },
    });
    expect(problems).toEqual([
      expect.objectContaining({
        kind: 'placeholder-mismatch',
        language: 'es',
        key: 'k',
      }),
    ]);
  });

  it('reports a plural category the language needs but lacks', () => {
    const problems = checkCatalogs({
      es: {
        common: { items_one: '{{count}} uno', items_other: '{{count}} otros' },
      },
      en: {
        common: { items_one: '{{count}} one', items_other: '{{count}} other' },
      },
    });
    expect(problems).toEqual([
      expect.objectContaining({
        kind: 'missing-plural-form',
        language: 'es',
        key: 'items_many',
      }),
    ]);
  });

  it('reports a plural family missing in one language', () => {
    const problems = checkCatalogs({
      es: clean.es!,
      en: { common: { a: { b: 'Hello {{name}}' } } },
    });
    expect(problems).toEqual([
      expect.objectContaining({
        kind: 'missing-key',
        language: 'en',
        key: 'items',
      }),
    ]);
  });
});

describe('findDuplicateKeys', () => {
  it('reports a top-level duplicate', () => {
    expect(findDuplicateKeys('{"a": "1", "b": "2", "a": "3"}')).toEqual(['a']);
  });

  it('reports a nested duplicate with its dotted path', () => {
    const text = '{"x": "0", "a": {"b": "1", "c": {"d": "2"}, "b": "3"}}';
    expect(findDuplicateKeys(text)).toEqual(['a.b']);
  });

  it('does not report the same key in sibling objects', () => {
    const text = '{"a": {"x": "1"}, "b": {"x": "2"}, "x": {"x": "3"}}';
    expect(findDuplicateKeys(text)).toEqual([]);
  });

  it('handles keys and values with escaped quotes and backslashes', () => {
    const text = String.raw`{"q\"k": "v\"", "b\\": "\\", "q\"k": "w", "b\\": "x"}`;
    expect(findDuplicateKeys(text)).toEqual(['q"k', 'b\\']);
  });

  it('reports a key repeated three times once', () => {
    expect(findDuplicateKeys('{"a": "1", "a": "2", "a": "3"}')).toEqual(['a']);
  });

  it('walks arrays without reporting indices', () => {
    const text =
      '{"a": [{"k": "1", "k": "2"}, {"k": "3"}, ["x", "y"]], "b": "z"}';
    expect(findDuplicateKeys(text)).toEqual(['a.k']);
  });

  it('returns nothing for a clean file', () => {
    const text =
      '{\n  "a": {"b": "1", "c": "{\\"b\\": 2}"},\n  "d": ["a", "a"]\n}';
    expect(findDuplicateKeys(text)).toEqual([]);
  });
});
