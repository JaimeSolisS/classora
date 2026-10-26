import { describe, expect, it } from 'vitest';

// #322 A3: Recharts stays in the progress pages' lazy route chunk. Only
// modules under features/progress/charts/ import `recharts`; only the
// progress feature imports those modules; and outside the feature, static
// imports of features/progress/ are limited to access.ts (route handles,
// the nav item and the link helpers). The pages themselves are loaded with
// a dynamic import() by the router. The ESLint rule enforces the first
// point while editing; this test enforces all three in CI.

const SOURCES = import.meta.glob<string>('/src/**/*.{ts,tsx}', {
  query: '?raw',
  import: 'default',
  eager: true,
});

/** The module specifiers of a file's static imports and re-exports. */
function staticImports(source: string): string[] {
  const found: string[] = [];
  for (const match of source.matchAll(
    /^\s*(?:import|export)\s[^'"]*?from\s+['"]([^'"]+)['"]/gm,
  )) {
    found.push(match[1]!);
  }
  for (const match of source.matchAll(/^\s*import\s+['"]([^'"]+)['"]/gm)) {
    found.push(match[1]!);
  }
  return found;
}

const files = Object.entries(SOURCES).filter(
  ([path]) => !/\.(test|stories)\.tsx?$/.test(path),
);

describe('the Recharts boundary (A3)', () => {
  it('reads the sources', () => {
    expect(files.length).toBeGreaterThan(100);
    expect(
      files.some(([path]) =>
        path.endsWith('progress/charts/AttendanceChart.tsx'),
      ),
    ).toBe(true);
  });

  it('only features/progress/charts/ imports recharts', () => {
    const importers = files
      .filter(([, source]) =>
        staticImports(source).some(
          (name) => name === 'recharts' || name.startsWith('recharts/'),
        ),
      )
      .map(([path]) => path);
    expect(importers.length).toBeGreaterThan(0);
    for (const path of importers) {
      expect(path).toMatch(/^\/src\/features\/progress\/charts\//);
    }
  });

  it('only the progress feature imports its charts', () => {
    for (const [path, source] of files) {
      if (path.startsWith('/src/features/progress/')) {
        continue;
      }
      const charts = staticImports(source).filter((name) =>
        name.includes('features/progress/charts'),
      );
      expect(charts, path).toEqual([]);
    }
  });

  it('outside the feature, static imports of features/progress/ are access.ts only', () => {
    for (const [path, source] of files) {
      if (path.startsWith('/src/features/progress/')) {
        continue;
      }
      const progress = staticImports(source).filter((name) =>
        /features\/progress(\/|$)/.test(name),
      );
      for (const name of progress) {
        expect(name, path).toBe('@/features/progress/access');
      }
    }
  });
});
