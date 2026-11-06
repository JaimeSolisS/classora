import { describe, expect, it } from 'vitest';
import {
  findHardcodedText,
  isProductionSource,
  LITERAL_ALLOWLIST,
  type Finding,
} from './literals';

// #130 C3 (E4): production TS/TSX shows no hard-coded text; every label,
// accessible name, announcement and title comes from the catalogs.

const sources = import.meta.glob<string>(['../**/*.ts', '../**/*.tsx'], {
  eager: true,
  import: 'default',
  query: '?raw',
});

function scan(source: string, fileName = 'x.tsx'): Omit<Finding, 'file'>[] {
  return findHardcodedText(source, fileName).map(
    ({ file: _file, ...rest }) => rest,
  );
}

describe('findHardcodedText', () => {
  it('reports JSX text', () => {
    expect(scan('const a = <p>Save changes</p>;')).toEqual([
      { kind: 'jsx-text', text: 'Save changes', line: 1 },
    ]);
  });

  it.each([
    'aria-label',
    'aria-description',
    'aria-roledescription',
    'aria-valuetext',
    'title',
    'placeholder',
    'alt',
    'label',
  ])('reports a literal %s, as a string or a template', (name) => {
    expect(scan(`const a = <i ${name}="Close" />;`)).toEqual([
      { kind: 'attribute', name, text: 'Close', line: 1 },
    ]);
    expect(scan(`const a = <i ${name}={\`Close\`} />;`)).toEqual([
      { kind: 'attribute', name, text: 'Close', line: 1 },
    ]);
  });

  it.each([
    ['announce', 'announce("Saved")'],
    ['setAnnouncement', 'setAnnouncement(`Saved`)'],
    ['useDocumentTitle', 'useDocumentTitle("Students")'],
    ['toast', 'toast("Saved")'],
    ['toast', 'toast.error("Failed")'],
    ['toastSuccess', 'toastSuccess("Saved")'],
  ])('reports a literal first argument of %s', (name, call) => {
    const [finding] = scan(`${call};`, 'x.ts');
    expect(finding).toMatchObject({ kind: 'call', name });
  });

  it.each([
    ['a className', 'const a = <p className="flex gap-2">{x}</p>;'],
    ['a space', "const a = <p>{x}{' '}{y}</p>;"],
    ['punctuation only', 'const a = <p>· — → / ( ) :</p>;'],
    ['numbers', 'const a = <p>12 3.5</p>;'],
    ['a translated text', "const a = <p title={t('x')}>{t('y')}</p>;"],
    ['an import', "import { x } from 'some module';"],
    [
      'an id or data attribute',
      'const a = <p id="main title" data-x="Hello" />;',
    ],
    ['a dynamic label', 'const a = <i aria-label={label} />;'],
    ['a dynamic announcement', 'announce(t("saved"));'],
    ['another call', 'console.error("Not shown to anyone");'],
  ])('ignores %s', (_name, source) => {
    expect(scan(source)).toEqual([]);
  });

  it('gives the line of each finding', () => {
    expect(
      scan('const a = (\n  <div>\n    <b>Hi there</b>\n  </div>\n);'),
    ).toEqual([{ kind: 'jsx-text', text: 'Hi there', line: 3 }]);
  });
});

describe('isProductionSource', () => {
  it.each([
    ['../features/students/StudentsPage.tsx', true],
    ['../lib/time.ts', true],
    ['../features/students/students.test.tsx', false],
    ['../features/students/StudentsPage.stories.tsx', false],
    ['../test/render-app.ts', false],
    ['../features/students/fixtures.ts', false],
    ['../features/dashboard/storyKit.tsx', false],
    ['../features/auth/storySession.ts', false],
    ['../i18n/i18next.d.ts', false],
  ])('%s -> %s', (path, expected) => {
    expect(isProductionSource(path.replace(/^\.\./, 'src'))).toBe(expected);
  });
});

describe('production source', () => {
  it('has no hard-coded text outside the allowlist', () => {
    const files = Object.entries(sources).filter(([path]) =>
      isProductionSource(path.replace(/^\.\./, 'src')),
    );
    // The scan really reads the app (about 500 files).
    expect(files.length).toBeGreaterThan(300);
    const allowed = new Set(
      LITERAL_ALLOWLIST.map(({ file, text }) => `${file}\u0000${text}`),
    );
    const findings = files
      .flatMap(([path, source]) =>
        findHardcodedText(source, path.replace(/^\.\.\//, 'src/')),
      )
      .filter(({ file, text }) => !allowed.has(`${file}\u0000${text}`));
    const report = findings
      .map(
        (f) =>
          `${f.file}:${f.line} ${f.kind}${f.name ? ` ${f.name}` : ''}: ${JSON.stringify(f.text)}`,
      )
      .join('\n');
    expect(findings, `\n${report}\n`).toEqual([]);
  });

  it('allows each listed literal for a written reason', () => {
    for (const entry of LITERAL_ALLOWLIST) {
      expect(entry.reason.trim()).not.toBe('');
    }
  });
});
