import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { applyLanguage, i18n } from '@/i18n';
import { LANGUAGES } from '@/i18n/language';
import type { SessionUserBody } from '@/features/auth/session';
import {
  meAs,
  problem,
  stubApi,
  TEST_USER,
  type ApiStub,
  type Responder,
} from '@/test/api-stub';
import { alerts, click, setFieldValue } from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  settle,
  settleUntil,
  type RenderedApp,
} from '@/test/render-app';
import {
  MAX_FILE_BYTES,
  POLL_INTERVAL_MS,
  POLL_TIMEOUT_MS,
  type ImportJob,
} from './api';
import { CONFLICT_CODES, ROW_CODES } from './codes';
import {
  COMMITTED,
  COMMITTED_ROWS,
  COMMITTING,
  DISCARDED,
  FAILED,
  JOB_ID,
  jobPage,
  rowPage,
  STAGED,
  STAGED_ROWS,
  STUDENT_ID,
  TUTOR_ROWS,
  TUTOR_USER_ID,
  TUTORS_JOB_ID,
  TUTORS_STAGED,
} from './fixtures';
import { IMPORT_KINDS, importKind, templateCsv } from './kinds';
// Loaded up front, so the lazy routes resolve without real I/O (fake timers).
import './ImportDetailPage';
import './ImportsPage';

// M8-05b (#518): the /imports page (new import and history) and the
// /imports/:importId report, in the real App with a stubbed fetch: nav and
// kinds per user (A1, B1), the header links (A2), the template (B2), the
// file refusals (B3), the history (B4), the report and its filters (C1,
// C2), commit, discard and the conflict (C3), polling (E3) and the pinned
// row codes (C4).

const LIST = 'GET /api/v1/imports';
const CREATE = 'POST /api/v1/imports';
const DETAIL = `GET /api/v1/imports/${JOB_ID}`;
const ROWS = `GET /api/v1/imports/${JOB_ID}/rows`;
const COMMIT = `POST /api/v1/imports/${JOB_ID}/commit`;
const DISCARD = `POST /api/v1/imports/${JOB_ID}/discard`;

const user = (
  roles: SessionUserBody['roles'],
  permissions: SessionUserBody['permissions'],
): SessionUserBody => ({ ...TEST_USER, roles, permissions });

const OWNER = user(
  ['owner'],
  [
    'user.view',
    'user.invite',
    'user.role.assign',
    'student.view',
    'student.manage',
    'tutor.view',
  ],
);
const COORDINATOR = user(
  ['academic_coordinator'],
  ['student.view', 'student.manage', 'tutor.view'],
);
const FINANCE = user(['finance_staff'], ['student.view', 'schedule.view']);

let app: RenderedApp | undefined;

const main = () => app!.container.querySelector('main')!;
const field = <T extends HTMLElement = HTMLInputElement>(label: string): T => {
  const found = [...main().querySelectorAll('label')].find(
    (candidate) => candidate.textContent === label,
  );
  const element = found?.htmlFor
    ? document.getElementById(found.htmlFor)
    : null;
  if (!element) {
    throw new Error(`No field labelled "${label}"`);
  }
  return element as T;
};
const liveText = () =>
  main().querySelector('p[aria-live="polite"]')?.textContent ?? '';
const dialog = () =>
  document.querySelector<HTMLElement>('[role="alertdialog"]');
const queryOf = (api: ApiStub, path: string) =>
  api.fetch.mock.calls
    .map(([request]) => new URL(request.url))
    .filter((url) => url.pathname === path)
    .map((url) => Object.fromEntries(url.searchParams));

function problemWith(
  status: number,
  code: string,
  details?: Record<string, unknown>,
): Response {
  return new Response(
    JSON.stringify({
      type: 'about:blank',
      title: 'Problem',
      status,
      code,
      request_id: `req-${code}`,
      detail: 'Server detail that must never reach the UI',
      ...(details ? { details } : {}),
    }),
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  );
}

function stub(
  as: SessionUserBody = OWNER,
  routes: Record<string, Responder | Responder[]> = {},
): ApiStub {
  return stubApi({
    'GET /api/v1/auth/me': meAs(as),
    [LIST]: () => jsonResponse(200, jobPage([COMMITTED, TUTORS_STAGED])),
    [DETAIL]: () => jsonResponse(200, STAGED),
    [ROWS]: () => jsonResponse(200, rowPage(STAGED_ROWS)),
    ...routes,
  });
}

async function open(path: string) {
  app = await renderApp(path);
  await settleUntil(() => main()?.querySelector('h1'));
  await settle();
}

function chooseFile(file: File) {
  const input = field('CSV file');
  Object.defineProperty(input, 'files', { value: [file], configurable: true });
  act(() => {
    input.dispatchEvent(new Event('change', { bubbles: true }));
  });
}

/** The message linked to the file input (aria-describedby). */
function fileMessage(): string {
  const input = field('CSV file');
  return (input.getAttribute('aria-describedby') ?? '')
    .split(' ')
    .map((id) => document.getElementById(id)?.textContent ?? '')
    .join(' | ');
}

const CSV =
  'given_names,family_names,birthdate,contact_name,contact_relationship,contact_phone\r\n' +
  'Ana,Ejemplo,2014-03-15,María Ejemplo,Madre,55 0000 0000\r\n';

beforeEach(() => {
  applyLanguage('en');
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('navigation and kinds (A1, A2, B1)', () => {
  it.each([
    ['an Owner', OWNER, true],
    ['a Coordinator', COORDINATOR, true],
    ['Finance', FINANCE, false],
  ] as const)('%s sees the Imports item: %s', async (_who, as, visible) => {
    stub(as);
    app = await renderApp('/');
    await settleUntil(() => app!.container.querySelector('header nav'));
    const link = app.container.querySelector('header nav a[href="/imports"]');
    expect(link !== null).toBe(visible);
    if (link) {
      expect(link.textContent).toBe('Imports');
    }
  });

  it('offers an Owner both kinds, with ?kind= preselecting tutors', async () => {
    stub(OWNER);
    await open('/imports?kind=tutors');
    expect(main().querySelector('h1')!.textContent).toBe('Imports');
    expect(document.title).toContain('Imports');
    const select = field<HTMLSelectElement>('Data type');
    expect([...select.options].map((option) => option.textContent)).toEqual([
      'Choose a type',
      'Students',
      'Tutors',
    ]);
    expect(select.value).toBe('tutors');
    expect(main().textContent).toContain(
      'Each tutor gets a pending invitation. Copy their link from Invitations → New link.',
    );
    const required = [...main().querySelectorAll('dd')][0]!.textContent;
    expect(required).toBe('usernamedisplay_name');
  });

  it('offers a Coordinator only students, preselected', async () => {
    stub(COORDINATOR);
    await open('/imports');
    const select = field<HTMLSelectElement>('Data type');
    expect([...select.options].map((option) => option.value)).toEqual([
      'students',
    ]);
    expect(select.value).toBe('students');
    expect(main().textContent).toContain('Save your Excel sheet as CSV UTF-8.');
    expect(main().textContent).not.toContain('pending invitation');
  });

  it('asks for a kind before anything is sent', async () => {
    const api = stub(OWNER);
    await open('/imports');
    chooseFile(new File([CSV], 'alumnos.csv', { type: 'text/csv' }));
    await click(buttonNamed(main(), 'Check file'));
    const select = field<HTMLSelectElement>('Data type');
    expect(select.getAttribute('aria-invalid')).toBe('true');
    expect(
      document.getElementById(select.getAttribute('aria-describedby')!)
        ?.textContent,
    ).toBe('Choose the data type.');
    expect(api.calls(CREATE)).toHaveLength(0);
  });

  it('links /students and /tutors to their import (A2)', async () => {
    stub(OWNER);
    await open('/students');
    const students = main().querySelector('a[href="/imports?kind=students"]');
    expect(students?.textContent).toBe('Import');
    app!.unmount();

    stub(OWNER);
    await open('/tutors');
    const tutors = main().querySelector('a[href="/imports?kind=tutors"]');
    expect(tutors?.textContent).toBe('Import');
  });

  it('shows no import link on /students without student.manage', async () => {
    stub(FINANCE);
    await open('/students');
    expect(main().querySelector('a[href^="/imports"]')).toBeNull();
  });
});

describe('the template (B2, E2)', () => {
  it.each([
    [
      'students',
      'given_names,family_names,birthdate,email,phone,school_name,school_grade,education_level,contact_name,contact_relationship,contact_phone,contact2_name,contact2_relationship,contact2_phone\r\n' +
        'Ana Sofía,Ejemplo Pérez,2014-03-15,,,Escuela Ejemplo,4.º,Primaria,María Ejemplo,Madre,55 0000 0000,,,\r\n',
    ],
    [
      'tutors',
      'username,display_name,email,phone\r\n' +
        'tutor.ejemplo,Tutor Ejemplo,tutor@example.com,\r\n',
    ],
  ])('%s: a BOM, the header and one example row', (code, expected) => {
    expect(templateCsv(importKind(code)!)).toBe(`\uFEFF${expected}`);
  });

  it('quotes a cell with a comma or a quote', () => {
    const csv = templateCsv({
      ...IMPORT_KINDS[1]!,
      example: ['a,b', 'say "hi"', '', ''],
    });
    expect(csv.split('\r\n')[1]).toBe('"a,b","say ""hi""",,');
  });

  it('downloads the template built in memory', async () => {
    stub(OWNER);
    const created: Blob[] = [];
    const createObjectURL = vi.fn((blob: Blob) => {
      created.push(blob);
      return 'blob:template';
    });
    const revokeObjectURL = vi.fn();
    vi.stubGlobal(
      'URL',
      Object.assign(URL, { createObjectURL, revokeObjectURL }),
    );
    const clicked: string[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      clicked.push(this.download);
    });
    await open('/imports?kind=students');
    await click(buttonNamed(main(), 'Download template'));

    expect(clicked).toEqual(['classora-students-template.csv']);
    expect(created).toHaveLength(1);
    expect(created[0]!.type).toBe('text/csv;charset=utf-8');
    const bytes = new Uint8Array(await created[0]!.arrayBuffer());
    expect(new TextDecoder('utf-8', { ignoreBOM: true }).decode(bytes)).toBe(
      templateCsv(importKind('students')!),
    );
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:template');
  });
});

describe('a new import (B3)', () => {
  it('sends the file text as JSON and opens the report', async () => {
    const api = stub(OWNER, {
      [CREATE]: () => jsonResponse(201, STAGED),
    });
    await open('/imports?kind=students');
    chooseFile(new File([CSV], ' alumnos.csv ', { type: 'text/csv' }));
    await click(buttonNamed(main(), 'Check file'));
    await settleUntil(() => app!.location() === `/imports/${JOB_ID}`);

    expect(api.calls(CREATE).map((call) => call.body)).toEqual([
      { kind: 'students', file_name: 'alumnos.csv', csv: CSV },
    ]);
    await settleUntil(() => main().querySelector('tbody'));
    expect(main().querySelector('h1')!.textContent).toBe('alumnos.csv');
  });

  it('refuses a file over 2 MiB without sending it', async () => {
    const api = stub(OWNER);
    await open('/imports?kind=students');
    chooseFile(
      new File([new Uint8Array(MAX_FILE_BYTES + 1)], 'big.csv', {
        type: 'text/csv',
      }),
    );
    expect(fileMessage()).toContain('The file is over 2 MB.');
    await click(buttonNamed(main(), 'Check file'));

    expect(api.calls(CREATE)).toHaveLength(0);
    expect(field('CSV file').getAttribute('aria-invalid')).toBe('true');
    expect(fileMessage()).toContain('The file is over 2 MB.');
  });

  it('asks for a file', async () => {
    const api = stub(OWNER);
    await open('/imports?kind=students');
    await click(buttonNamed(main(), 'Check file'));
    expect(fileMessage()).toContain('Choose a CSV file.');
    expect(api.calls(CREATE)).toHaveLength(0);
  });

  it.each([
    [
      'encoding',
      problemWith(422, 'import_file_invalid', { reason: 'encoding' }),
      "The file isn't UTF-8. Save it from Excel as “CSV UTF-8”.",
    ],
    [
      'empty',
      problemWith(422, 'import_file_invalid', { reason: 'empty' }),
      'The file is empty or has no data rows.',
    ],
    [
      'malformed with a line',
      problemWith(422, 'import_file_invalid', {
        reason: 'malformed',
        line: 7,
      }),
      'The CSV file is malformed at line 7.',
    ],
    [
      'duplicate_column',
      problemWith(422, 'import_file_invalid', { reason: 'duplicate_column' }),
      'The header repeats a column.',
    ],
    [
      'missing_columns',
      problemWith(422, 'import_file_invalid', {
        reason: 'missing_columns',
        columns: ['birthdate', 'contact_phone'],
      }),
      'Required columns are missing: birthdate, contact_phone.',
    ],
    [
      'too_many_columns',
      problemWith(422, 'import_file_invalid', {
        reason: 'too_many_columns',
        max: 30,
      }),
      'The file has too many columns (at most 30).',
    ],
    [
      'an unknown reason',
      problemWith(422, 'import_file_invalid', { reason: 'brand_new' }),
      "The CSV file couldn't be read.",
    ],
    [
      'import_too_many_rows',
      problemWith(422, 'import_too_many_rows', { max: 1000 }),
      'The file has too many rows (at most 1000 rows).',
    ],
    [
      'import_too_large',
      problemWith(413, 'import_too_large', { max_bytes: MAX_FILE_BYTES }),
      'The file is over 2 MB.',
    ],
    [
      'validation_error',
      problem(422, 'validation_error'),
      i18n.t('validation_error', { ns: 'errors', lng: 'en' }),
    ],
  ])('shows %s on the file input', async (_name, response, message) => {
    stub(OWNER, { [CREATE]: () => response.clone() });
    await open('/imports?kind=students');
    chooseFile(new File([CSV], 'alumnos.csv', { type: 'text/csv' }));
    await click(buttonNamed(main(), 'Check file'));
    await settleUntil(() => alerts(main()).length > 0);

    expect(fileMessage()).toContain(message);
    expect(field('CSV file').getAttribute('aria-invalid')).toBe('true');
    expect(document.activeElement).toBe(field('CSV file'));
    expect(main().textContent).not.toContain('Server detail');
    expect(app!.location()).toBe('/imports?kind=students');
  });
});

describe('the history (B4)', () => {
  it('lists each import with its kind, status, counts and author', async () => {
    const api = stub(OWNER);
    await open('/imports');
    await settleUntil(() => main().querySelector('tbody'));

    const rows = [...main().querySelectorAll('tbody > tr')].map(
      (row) => row.textContent ?? '',
    );
    expect(rows).toHaveLength(2);
    expect(rows[0]).toContain('alumnos.csv');
    expect(rows[0]).toContain('Students');
    expect(rows[0]).toContain('Imported');
    expect(rows[0]).toContain('4 rows: 2 created, 0 skipped, 0 failed');
    expect(rows[0]).toContain('Demo Owner');
    expect(rows[0]).toContain('Nov 5, 2026');
    expect(rows[1]).toContain('Tutors');
    expect(rows[1]).toContain('To review');
    expect(rows[1]).toContain('1 rows: 1 ready, 0 duplicates, 0 with errors');
    expect(
      main().querySelector(`tbody a[href="/imports/${TUTORS_JOB_ID}"]`),
    ).not.toBeNull();
    expect(queryOf(api, '/api/v1/imports').at(-1)).toEqual({
      limit: '25',
      offset: '0',
    });
  });

  it('filters by kind and status in the URL', async () => {
    const api = stub(OWNER);
    await open('/imports?page=2');
    setFieldValue(field<HTMLSelectElement>('Type'), 'tutors');
    await settle();
    setFieldValue(field<HTMLSelectElement>('Status'), 'staged');
    await settle();

    expect(app!.location()).toBe('/imports?historyKind=tutors&status=staged');
    expect(queryOf(api, '/api/v1/imports').at(-1)).toEqual({
      kind: 'tutors',
      status: 'staged',
      limit: '25',
      offset: '0',
    });
  });

  it('shows the empty and error states', async () => {
    stub(OWNER, { [LIST]: () => jsonResponse(200, jobPage([])) });
    await open('/imports');
    await settleUntil(() => main().textContent?.includes('No imports yet.'));
    app!.unmount();

    const api = stub(OWNER, {
      [LIST]: [
        () => problem(422, 'validation_error'),
        () => jsonResponse(200, jobPage([STAGED])),
      ],
    });
    await open('/imports');
    await settleUntil(() => alerts(main()).length > 0);
    await click(buttonNamed(main(), 'Try again'));
    await settleUntil(() => main().querySelector('tbody'));
    expect(api.calls(LIST).length).toBeGreaterThanOrEqual(2);
  });
});

describe('the report (C1, C2)', () => {
  it('shows the summary, ignored columns, expiry and the rows', async () => {
    stub(OWNER);
    await open(`/imports/${JOB_ID}`);
    await settleUntil(() => main().querySelector('tbody'));

    const text = main().textContent ?? '';
    expect(main().querySelector('h1')!.textContent).toBe('alumnos.csv');
    expect(text).toContain('To review');
    expect(text).toContain('These columns were ignored: notas.');
    expect(text).toContain('It is discarded automatically on Nov 12, 2026.');
    expect(text).not.toContain('Created2');

    const rows = [...main().querySelectorAll('tbody > tr')];
    expect(rows.map((row) => row.querySelector('td')!.textContent)).toEqual([
      '2',
      '3',
      '4',
      '6',
    ]);
    expect(rows[0]!.textContent).toContain('Ready');
    expect(rows[0]!.textContent).toContain('Ana Sofía');
    expect(rows[1]!.textContent).toContain('Duplicate');
    expect(rows[1]!.textContent).toContain(
      "Warning: It repeats an earlier row of the file; it isn't imported.",
    );
    expect(rows[2]!.textContent).toContain('Has errors');
    expect(rows[2]!.textContent).toContain(
      "Error: birthdate: The date isn't valid (use YYYY-MM-DD or DD/MM/YYYY).",
    );
    expect(rows[2]!.textContent).toContain(
      "Error: email: The email isn't valid.",
    );
    // An unknown code: the generic message with the code (C2).
    expect(rows[2]!.textContent).toContain(
      'Error: phone: Invalid value (brand_new_code).',
    );
  });

  it('filters the rows by status in the URL', async () => {
    const api = stub(OWNER);
    await open(`/imports/${JOB_ID}`);
    await settleUntil(() => main().querySelector('tbody'));
    const select = field<HTMLSelectElement>('Show');
    expect([...select.options].map((option) => option.textContent)).toEqual([
      'All',
      'Ready',
      'Duplicates',
      'With errors',
    ]);
    setFieldValue(select, 'error');
    await settle();

    expect(app!.location()).toBe(`/imports/${JOB_ID}?rows=error`);
    expect(queryOf(api, `/api/v1/imports/${JOB_ID}/rows`).at(-1)).toEqual({
      status: 'error',
      limit: '50',
      offset: '0',
    });
  });

  it('after the commit: outcomes, no values and links to the records', async () => {
    stub(OWNER, {
      [DETAIL]: () => jsonResponse(200, COMMITTED),
      [ROWS]: () => jsonResponse(200, rowPage(COMMITTED_ROWS)),
    });
    await open(`/imports/${JOB_ID}?rows=created`);
    await settleUntil(() => main().querySelector('tbody'));

    const text = main().textContent ?? '';
    expect(text).toContain('Imported');
    expect(text).toContain("The file's data was deleted when it was imported.");
    expect(text).not.toContain('discarded automatically');
    expect(text).toContain('Created2');
    expect(buttonsText()).not.toContain('Discard');
    const select = field<HTMLSelectElement>('Show');
    expect(select.value).toBe('created');
    expect([...select.options].map((option) => option.textContent)).toEqual([
      'All',
      'Duplicates',
      'With errors',
      'Created',
      'Skipped',
      'Failed',
    ]);
    const link = main().querySelector<HTMLAnchorElement>(
      `tbody a[href="/students/${STUDENT_ID}"]`,
    );
    expect(link?.textContent).toBe('View student (line 2)');
    expect(main().querySelector('tbody')!.textContent).not.toContain(
      'Ana Sofía',
    );
  });

  it('links a created tutor to the tutor page', async () => {
    stub(OWNER, {
      [`GET /api/v1/imports/${TUTORS_JOB_ID}`]: () =>
        jsonResponse(200, { ...TUTORS_STAGED, ...committedCounts() }),
      [`GET /api/v1/imports/${TUTORS_JOB_ID}/rows`]: () =>
        jsonResponse(200, rowPage(TUTOR_ROWS)),
    });
    await open(`/imports/${TUTORS_JOB_ID}`);
    await settleUntil(() => main().querySelector('tbody'));
    expect(
      main().querySelector(`tbody a[href="/tutors/${TUTOR_USER_ID}"]`)
        ?.textContent,
    ).toBe('View tutor (line 2)');
  });

  it('says the rows of a discarded import are gone', async () => {
    const api = stub(OWNER, { [DETAIL]: () => jsonResponse(200, DISCARDED) });
    await open(`/imports/${JOB_ID}`);
    expect(main().textContent).toContain(
      'The import was discarded and its rows were deleted.',
    );
    expect(api.calls(ROWS)).toHaveLength(0);
    expect(buttonsText()).not.toContain('Discard');
  });

  it('shows a 404 with a retry', async () => {
    stub(OWNER, { [DETAIL]: () => problem(404, 'not_found') });
    await open(`/imports/${JOB_ID}`);
    await settleUntil(() => alerts(main()).length > 0);
    expect(buttonNamed(main(), 'Try again')).toBeDefined();
  });
});

function committedCounts(): Partial<ImportJob> {
  return {
    status: 'committed',
    counts: {
      total: 1,
      ready: 0,
      duplicate: 0,
      error: 0,
      created: 1,
      skipped: 0,
      failed: 0,
    },
    expires_at: null,
  };
}

function buttonsText(): string[] {
  return [...main().querySelectorAll('button')].map(
    (button) => button.textContent ?? '',
  );
}

describe('commit and discard (C3)', () => {
  it('confirms, commits with the version and starts following it', async () => {
    const api = stub(OWNER, {
      [COMMIT]: () => jsonResponse(202, COMMITTING),
    });
    await open(`/imports/${JOB_ID}`);
    const trigger = buttonNamed(main(), 'Import 2 rows');
    await click(trigger);

    const confirm = dialog()!;
    expect(confirm.textContent).toContain('Import 2 rows?');
    expect(confirm.textContent).toContain(
      "2 records will be created. Rows with errors or duplicates aren't imported.",
    );
    await click(buttonNamed(confirm, 'Cancel'));
    expect(dialog()).toBeNull();
    await settle();
    expect(document.activeElement).toBe(trigger);
    expect(api.calls(COMMIT)).toHaveLength(0);

    await click(buttonNamed(main(), 'Import 2 rows'));
    await click(buttonNamed(dialog()!, 'Import'));
    await settle();

    expect(api.calls(COMMIT).map((call) => call.body)).toEqual([
      { version: 1 },
    ]);
    expect(dialog()).toBeNull();
    expect(main().textContent).toContain('Importing');
    expect(liveText()).toBe('Importing the rows. This page updates by itself.');
    expect(buttonsText()).not.toContain('Import 2 rows');
    expect(document.activeElement).toBe(main().querySelector('h1'));
  });

  it('reloads the import after a conflict', async () => {
    const api = stub(OWNER, {
      [COMMIT]: () => problem(409, 'version_conflict'),
      [DETAIL]: [
        () => jsonResponse(200, STAGED),
        () => jsonResponse(200, { ...STAGED, version: 2 }),
      ],
    });
    await open(`/imports/${JOB_ID}`);
    await click(buttonNamed(main(), 'Import 2 rows'));
    await click(buttonNamed(dialog()!, 'Import'));
    await settleUntil(() => alerts(main()).length > 0);

    expect(alerts(main()).join()).toContain(
      'The import changed; it was reloaded.',
    );
    expect(api.calls(DETAIL).length).toBeGreaterThanOrEqual(2);
  });

  it('shows the error of a refused commit', async () => {
    stub(OWNER, {
      [COMMIT]: () => problem(422, 'import_nothing_to_commit'),
    });
    await open(`/imports/${JOB_ID}`);
    await click(buttonNamed(main(), 'Import 2 rows'));
    await click(buttonNamed(dialog()!, 'Import'));
    await settleUntil(() => alerts(main()).length > 0);
    expect(alerts(main()).join()).toContain('No rows are ready to import.');
  });

  it('confirms and discards', async () => {
    const api = stub(OWNER, {
      [DISCARD]: () => jsonResponse(200, DISCARDED),
    });
    await open(`/imports/${JOB_ID}`);
    await click(buttonNamed(main(), 'Discard'));
    const confirm = dialog()!;
    expect(confirm.textContent).toContain('Discard this import?');
    await click(buttonNamed(confirm, 'Discard'));
    await settle();

    expect(api.calls(DISCARD).map((call) => call.body)).toEqual([
      { version: 1 },
    ]);
    expect(liveText()).toBe('Import discarded.');
    expect(main().textContent).toContain(
      'The import was discarded and its rows were deleted.',
    );
  });

  it('offers a failed import again', async () => {
    stub(OWNER, { [DETAIL]: () => jsonResponse(200, FAILED) });
    await open(`/imports/${JOB_ID}`);
    expect(buttonsText()).toEqual(
      expect.arrayContaining(['Import 1 row', 'Discard']),
    );
    expect(main().textContent).toContain('Failed');
  });
});

describe('following the commit (E3)', () => {
  // The clock is faked but keeps moving (shouldAdvanceTime), so fetch
  // bodies and React Query's notifications settle as in the other tests.
  function useFakeClock() {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  }

  async function mountAt(path: string) {
    app = await renderApp(path);
    await settleUntil(() => main()?.querySelector('h1'));
    await settle();
  }

  async function advance(ms: number) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ms);
    });
    await settle();
  }

  it('polls every 3 s while committing and stops when done', async () => {
    useFakeClock();
    const api = stub(OWNER, {
      [DETAIL]: [
        () => jsonResponse(200, COMMITTING),
        () => jsonResponse(200, COMMITTING),
        () => jsonResponse(200, COMMITTED),
      ],
    });
    await mountAt(`/imports/${JOB_ID}`);
    expect(api.calls(DETAIL)).toHaveLength(1);

    await advance(POLL_INTERVAL_MS);
    expect(api.calls(DETAIL)).toHaveLength(2);
    await advance(POLL_INTERVAL_MS);
    expect(api.calls(DETAIL)).toHaveLength(3);
    expect(liveText()).toBe('Import finished: 2 created, 0 skipped, 0 failed.');

    await advance(POLL_INTERVAL_MS * 5);
    expect(api.calls(DETAIL)).toHaveLength(3);
  });

  it('announces a failed commit', async () => {
    useFakeClock();
    stub(OWNER, {
      [DETAIL]: [
        () => jsonResponse(200, COMMITTING),
        () => jsonResponse(200, FAILED),
      ],
    });
    await mountAt(`/imports/${JOB_ID}`);
    await advance(POLL_INTERVAL_MS);
    expect(liveText()).toBe(
      'The import failed. You can import the remaining rows again.',
    );
  });

  it('does not poll an import that is not committing', async () => {
    useFakeClock();
    const api = stub(OWNER);
    await mountAt(`/imports/${JOB_ID}`);
    await advance(POLL_INTERVAL_MS * 3);
    expect(api.calls(DETAIL)).toHaveLength(1);
  });

  it('stops after 10 minutes and says it is still running', async () => {
    useFakeClock();
    const api = stub(OWNER, {
      [DETAIL]: () => jsonResponse(200, COMMITTING),
    });
    await mountAt(`/imports/${JOB_ID}`);
    await advance(POLL_TIMEOUT_MS + POLL_INTERVAL_MS);
    const calls = api.calls(DETAIL).length;
    expect(calls).toBeGreaterThan(150);
    expect(liveText()).toBe('Still in progress; come back later.');

    await advance(POLL_INTERVAL_MS * 10);
    expect(api.calls(DETAIL)).toHaveLength(calls);
  });
});

// C4: the row codes of #122 B4 (and D1's commit-time code), each labelled
// in both languages; a new API code needs a label here too.
describe('the row codes (C4)', () => {
  it('pins the codes', () => {
    expect(ROW_CODES).toEqual([
      'required',
      'invalid',
      'too_long',
      'invalid_date',
      'future_date',
      'invalid_email',
      'invalid_phone',
      'education_level_unknown',
      'education_level_unavailable',
      'username_invalid',
      'username_taken',
      'duplicate_existing',
      'duplicate_in_file',
      'student_unknown',
      'student_ambiguous',
      'student_inactive',
      'subject_unknown',
      'subject_inactive',
      'tutor_unknown',
      'tutor_inactive',
      'classroom_unknown',
      'classroom_inactive',
      'time_format',
      'duration_out_of_range',
      'weekdays_empty',
      'weekday_out_of_range',
      'weekdays_duplicate',
      'interval_out_of_range',
      'until_and_count',
      'count_out_of_range',
      'until_before_start',
      'no_occurrences',
      'starts_on_in_past',
      'crosses_local_date',
      'scheduling_conflict',
      'series_not_offerable',
      'student_has_no_family',
      'student_not_in_family',
      'invalid_amount',
      'amount_not_positive',
      'date_out_of_range',
    ]);
    // #123 B4: the ADR-0005 hard and soft codes of a scheduling_conflict.
    expect(CONFLICT_CODES).toEqual([
      'tutor_overlap',
      'room_overlap',
      'student_overlap',
      'room_capacity_exceeded',
      'tutor_unavailable',
      'room_unavailable',
      'center_closed',
      'outside_operating_hours',
      'tutor_not_qualified',
      'group_capacity_exceeded',
      'notice_window',
    ]);
  });

  it.each(
    CONFLICT_CODES.flatMap((code) => LANGUAGES.map((lng) => [code, lng])),
  )('conflict %s has a %s label (the scheduling rule name)', (code, lng) => {
    expect(i18n.exists(`scheduling.rules.${code}`, { lng })).toBe(true);
  });

  it.each(ROW_CODES.flatMap((code) => LANGUAGES.map((lng) => [code, lng])))(
    '%s has a %s label',
    (code, lng) => {
      expect(i18n.exists(`imports.rowCodes.${code}`, { lng })).toBe(true);
    },
  );
});

// #123 F1-F4: the recurring schedules and opening balances kinds on the same
// page: who sees which kind, their templates and help, a failed schedules
// row with its conflicts, the record cells and the balances commit dialog.
describe('schedules and opening balances (#123)', () => {
  const FULL_OWNER = user(
    ['owner'],
    [
      'user.view',
      'user.invite',
      'user.role.assign',
      'student.view',
      'student.manage',
      'tutor.view',
      'schedule.view',
      'schedule.manage',
      'billing.view',
      'billing.manage',
    ],
  );
  const BILLING = user(
    ['finance_staff'],
    ['student.view', 'billing.view', 'billing.manage'],
  );
  const SERIES_ID = '0199b000-0000-7000-8000-0000000000c1';
  const CHARGE_ID = '0199b000-0000-7000-8000-0000000000d1';

  it('shows Finance the Imports item with opening balances only', async () => {
    stub(BILLING);
    await open('/imports');
    expect(
      app!.container.querySelector('header nav a[href="/imports"]'),
    ).not.toBeNull();
    const select = field<HTMLSelectElement>('Data type');
    expect([...select.options].map((option) => option.value)).toEqual([
      'opening_balances',
    ]);
    expect(select.value).toBe('opening_balances');
    expect(main().textContent).toContain(
      'Each row creates a pending manual charge for the student',
    );
    const required = [...main().querySelectorAll('dd')][0]!.textContent;
    expect(required).toBe(
      'student_given_namesstudent_family_namesstudent_birthdatedescriptionamountservice_datedue_date',
    );
  });

  it('offers an Owner with every permission the four kinds', async () => {
    stub(FULL_OWNER);
    await open('/imports?kind=schedules');
    const select = field<HTMLSelectElement>('Data type');
    expect([...select.options].map((option) => option.textContent)).toEqual([
      'Choose a type',
      'Students',
      'Tutors',
      'Recurring schedules',
      'Opening balances',
    ]);
    expect(select.value).toBe('schedules');
    expect(main().textContent).toContain(
      'Each row creates a weekly series. Conflicts are checked when importing',
    );
    const optional = [...main().querySelectorAll('dd')][1]!.textContent;
    expect(optional).toBe('intervaluntilcounttutor_usernameclassroom');
  });

  it('keeps a Coordinator to students', async () => {
    stub(COORDINATOR);
    await open('/imports?kind=opening_balances');
    const select = field<HTMLSelectElement>('Data type');
    expect([...select.options].map((option) => option.value)).toEqual([
      'students',
    ]);
  });

  it.each([
    [
      'schedules',
      'student_given_names,student_family_names,student_birthdate,subject,education_level,starts_on,start_time,duration_minutes,weekdays,interval,until,count,tutor_username,classroom\r\n' +
        'Ana Sofía,Ejemplo Pérez,2014-03-15,Matemáticas,Primaria,2031-01-06,16:00,60,1 3,1,,24,tutor.ejemplo,\r\n',
    ],
    [
      'opening_balances',
      'student_given_names,student_family_names,student_birthdate,description,amount,service_date,due_date\r\n' +
        'Ana Sofía,Ejemplo Pérez,2014-03-15,Saldo inicial,1250.00,2031-01-01,2031-01-15\r\n',
    ],
  ])('%s: the template', (code, expected) => {
    expect(templateCsv(importKind(code)!)).toBe(`\uFEFF${expected}`);
  });

  it('shows a failed schedules row with its conflicts and links a created series', async () => {
    const job: ImportJob = {
      ...COMMITTED,
      kind: 'schedules',
      counts: { ...COMMITTED.counts, created: 1, failed: 1 },
    };
    stub(FULL_OWNER, {
      [DETAIL]: () => jsonResponse(200, job),
      [ROWS]: () =>
        jsonResponse(
          200,
          rowPage([
            {
              row_number: 2,
              status: 'created',
              values: null,
              errors: [],
              warnings: [],
              record_id: SERIES_ID,
            },
            {
              row_number: 3,
              status: 'failed',
              values: null,
              errors: [
                { column: null, code: 'scheduling_conflict' },
                { column: null, code: 'tutor_overlap' },
                { column: null, code: 'outside_operating_hours' },
              ],
              warnings: [],
              record_id: null,
            },
          ]),
        ),
    });
    await open(`/imports/${JOB_ID}`);
    await settleUntil(() => main().querySelector('tbody'));

    const link = main().querySelector<HTMLAnchorElement>(
      `tbody a[href="/schedule/series/${SERIES_ID}"]`,
    );
    expect(link?.textContent).toBe('View series (line 2)');
    const failed = main().querySelectorAll('tbody tr')[1]!.textContent;
    expect(failed).toContain(
      'There are scheduling conflicts; the series wasn’t created.',
    );
    expect(failed).toContain('Tutor overlap');
    expect(failed).toContain('Opening hours');
    expect(failed).not.toContain('Invalid value');
  });

  it('says a created balance is a charge, with no link', async () => {
    const job: ImportJob = { ...COMMITTED, kind: 'opening_balances' };
    stub(BILLING, {
      [DETAIL]: () => jsonResponse(200, job),
      [ROWS]: () =>
        jsonResponse(
          200,
          rowPage([
            {
              row_number: 2,
              status: 'created',
              values: null,
              errors: [],
              warnings: [],
              record_id: CHARGE_ID,
            },
          ]),
        ),
    });
    await open(`/imports/${JOB_ID}`);
    await settleUntil(() => main().querySelector('tbody'));

    const cells = main().querySelectorAll('tbody td');
    expect(cells[cells.length - 1]!.textContent).toBe('Charge created');
    expect(main().querySelector('tbody a')).toBeNull();
  });

  it('warns before posting the balances', async () => {
    stub(BILLING, {
      [DETAIL]: () =>
        jsonResponse(200, { ...STAGED, kind: 'opening_balances' }),
    });
    await open(`/imports/${JOB_ID}`);
    await click(buttonNamed(main(), 'Import 2 rows'));

    expect(dialog()!.textContent).toContain(
      '2 charges will be posted to the families’ accounts. It can’t be undone; it’s corrected with adjustments.',
    );
  });

  it('says the same in Spanish', async () => {
    applyLanguage('es');
    stub(BILLING, {
      [DETAIL]: () =>
        jsonResponse(200, { ...STAGED, kind: 'opening_balances' }),
    });
    await open(`/imports/${JOB_ID}`);
    await click(buttonNamed(main(), 'Importar 2 filas'));

    expect(dialog()!.textContent).toContain(
      'Se publicarán 2 cargos en las cuentas de las familias. No se puede deshacer; se corrige con ajustes.',
    );
  });
});
