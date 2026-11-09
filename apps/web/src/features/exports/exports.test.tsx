import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { applyLanguage } from '@/i18n';
import type { SessionUserBody } from '@/features/auth/session';
import {
  meAs,
  problem,
  stubApi,
  TEST_USER,
  type ApiStub,
  type Responder,
} from '@/test/api-stub';
import {
  alerts,
  click,
  comboboxOption,
  comboboxOptions,
  fieldByLabel,
  openDialog,
  setFieldValue,
  typeInto,
} from '@/test/dom';
import {
  buttonNamed,
  jsonResponse,
  renderApp,
  settle,
  settleUntil,
  type RenderedApp,
} from '@/test/render-app';
import { todayInZone } from '@/lib/time';
import { EXPORT_DATASETS, mayExport, type ExportDatasetCode } from './access';
import { POLL_INTERVAL_MS, POLL_TIMEOUT_MS, type ExportJob } from './api';
import {
  ALL_DATASETS,
  EXPENSES_DATASET,
  EXPIRED,
  FAILED,
  jobPage,
  QUEUED,
  READY,
  SESSIONS_DATASET,
  STUDENTS_DATASET,
} from './fixtures';
import { currentMonth, formatBytes, moveColumn, validate } from './rules';
// Loaded up front, so the lazy route resolves without real I/O (fake timers).
import './ExportsPage';

// M8-07b (#526): the /exports page in the real App with a stubbed fetch:
// access per role (A1, A3), the datasets and `?dataset=` (A2), the form
// (B1, B2), the request with its 409, 422 and reauthentication branches
// (B3, E3), the history (C1), polling (C2, E2) and the "Export" links of
// /students, /schedule and /expenses/records (A3).

const DATASETS = 'GET /api/v1/exports/datasets';
const LIST = 'GET /api/v1/exports';
const CREATE = 'POST /api/v1/exports';
const REAUTH = 'POST /api/v1/auth/reauthenticate';

const user = (
  roles: SessionUserBody['roles'],
  permissions: SessionUserBody['permissions'],
): SessionUserBody => ({ ...TEST_USER, roles, permissions });

// The seeded roles' permissions that #124 A4 reads.
const OWNER = user(
  ['owner'],
  ['student.view', 'schedule.view', 'expense.view', 'student.manage'],
);
const ADMINISTRATOR = user(
  ['administrator'],
  ['student.view', 'schedule.view', 'expense.configure'],
);
const COORDINATOR = user(
  ['academic_coordinator'],
  ['student.view', 'schedule.view'],
);
const FINANCE = user(
  ['finance_staff'],
  ['student.view', 'schedule.view', 'expense.view', 'expense.configure'],
);
const TUTOR = user(['tutor'], ['student.view', 'tutor.view']);
const STUDENT = user(['student'], ['student.view']);
const GUARDIAN = user(['guardian'], ['student.view', 'guardian.view']);

let app: RenderedApp | undefined;

const main = () => app!.container.querySelector('main')!;
const liveText = () =>
  main().querySelector('p[aria-live="polite"]:not(.sr-only)')?.textContent ??
  '';
const radio = (label: string) => fieldByLabel(main(), label);
// A column checkbox may share the text ("Classroom"): the select's label.
const select = (label: string): HTMLSelectElement => {
  const element = [...main().querySelectorAll('label')]
    .filter((candidate) => candidate.textContent === label)
    .map((candidate) => document.getElementById(candidate.htmlFor))
    .find((candidate) => candidate instanceof HTMLSelectElement);
  if (!(element instanceof HTMLSelectElement)) {
    throw new Error(`No select labelled "${label}"`);
  }
  return element;
};
const columnOrder = () =>
  [...main().querySelectorAll('li[data-column]')].map((li) =>
    li.getAttribute('data-column'),
  );
const optionTexts = (element: HTMLSelectElement) =>
  [...element.options].map((option) => option.textContent);
const rowOf = (id: string) =>
  main().querySelector<HTMLTableRowElement>(`tr[data-export-id="${id}"]`);

function hoursFromNow(hours: number): string {
  return new Date(Date.now() + hours * 3_600_000).toISOString();
}

function problemWith(
  status: number,
  code: string,
  extra: Record<string, unknown> = {},
): Response {
  return new Response(
    JSON.stringify({
      type: 'about:blank',
      title: 'Problem',
      status,
      code,
      request_id: `req-${code}`,
      detail: 'Server detail that must never reach the UI',
      ...extra,
    }),
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  );
}

function datasetsFor(as: SessionUserBody) {
  return ALL_DATASETS.filter((dataset) => mayExport(as, dataset.code));
}

function stub(
  as: SessionUserBody = OWNER,
  routes: Record<string, Responder | Responder[]> = {},
): ApiStub {
  return stubApi({
    'GET /api/v1/auth/me': meAs(as),
    [DATASETS]: () => jsonResponse(200, { items: datasetsFor(as) }),
    [LIST]: () => jsonResponse(200, jobPage([])),
    'GET /api/v1/classrooms': () =>
      jsonResponse(200, {
        items: [{ id: 'room-1', name: 'Aula 1', is_active: true }],
        total: 1,
        limit: 100,
        offset: 0,
      }),
    'GET /api/v1/education-levels': () =>
      jsonResponse(200, { items: [], total: 0, limit: 100, offset: 0 }),
    ...routes,
  });
}

async function open(path = '/exports') {
  app = await renderApp(path);
  await settleUntil(() => main()?.querySelector('h1'));
  await settleUntil(
    () =>
      main().querySelector('form') ||
      main().textContent?.includes("You don't have access"),
  );
  await settle();
}

async function submit() {
  await click(buttonNamed(main(), 'Request export'));
}

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

describe('access (A1, A3; #124 A4)', () => {
  // #124 A4's table with the seeded roles.
  it.each([
    ['an Owner', OWNER, [true, true, true]],
    ['an Administrator', ADMINISTRATOR, [true, true, false]],
    ['a Coordinator', COORDINATOR, [true, true, false]],
    ['Finance', FINANCE, [true, true, true]],
    ['a tutor', TUTOR, [false, false, false]],
    ['a student', STUDENT, [false, false, false]],
    ['a guardian', GUARDIAN, [false, false, false]],
  ] as const)(
    '%s may export students, sessions, expenses: %j',
    (_who, as, expected) => {
      expect(EXPORT_DATASETS.map((code) => mayExport(as, code))).toEqual(
        expected,
      );
    },
  );

  it('a staff role without the permission may not export, nor a non-staff user with it', () => {
    expect(mayExport(user(['owner'], []), 'students')).toBe(false);
    expect(mayExport(user(['tutor'], ['expense.view']), 'expenses')).toBe(
      false,
    );
    expect(mayExport(null, 'students')).toBe(false);
  });

  it.each([
    ['an Owner', OWNER, true],
    ['Finance', FINANCE, true],
    ['a tutor', TUTOR, false],
    ['a guardian', GUARDIAN, false],
  ] as const)('%s sees the Exports nav item: %s', async (_who, as, visible) => {
    stub(as);
    app = await renderApp('/');
    await settleUntil(() => app!.container.querySelector('header nav'));
    const link = app.container.querySelector('header nav a[href="/exports"]');
    expect(link !== null).toBe(visible);
    if (link) {
      expect(link.textContent).toBe('Exports');
    }
  });

  it('a tutor opening /exports gets the no-access page and no request', async () => {
    const api = stub(TUTOR);
    app = await renderApp('/exports');
    await settleUntil(() => main()?.querySelector('h1'));
    expect(main().querySelector('h1')?.textContent).toBe(
      "You don't have access to this page",
    );
    expect(api.calls(DATASETS)).toHaveLength(0);
  });

  it('a 403 on the datasets shows the no-access page', async () => {
    stub(OWNER, { [DATASETS]: () => problem(403, 'permission_denied') });
    await open();
    expect(main().querySelector('h1')?.textContent).toBe(
      "You don't have access to this page",
    );
    expect(main().querySelector('form')).toBeNull();
  });
});

describe('the datasets (A2)', () => {
  it('offers only the datasets returned, with the web names, the first chosen', async () => {
    stub(ADMINISTRATOR);
    await open();
    const legend = [...main().querySelectorAll('legend')].find(
      (element) => element.textContent === 'Data',
    )!;
    const names = [
      ...legend.parentElement!.querySelectorAll('input[type="radio"]'),
    ].map((input) => (input as HTMLInputElement).labels?.[0]?.textContent);
    expect(names).toEqual(['Students', 'Classes']);
    expect(radio('Students').checked).toBe(true);
    // The API's column labels.
    expect(fieldByLabel(main(), 'Family names').checked).toBe(true);
  });

  it.each([
    ['expenses', OWNER, 'Expenses'],
    ['class_sessions', OWNER, 'Classes'],
    // Unknown, and forbidden (an Administrator has no expenses): the first.
    ['payroll', OWNER, 'Students'],
    ['expenses', ADMINISTRATOR, 'Students'],
  ] as const)(
    '?dataset=%s preselects for the user: %s',
    async (code, as, checked) => {
      stub(as);
      await open(`/exports?dataset=${code}`);
      expect(radio(checked).checked).toBe(true);
    },
  );

  it('drops a dataset the web does not know', async () => {
    stub(OWNER, {
      [DATASETS]: () =>
        jsonResponse(200, {
          items: [
            { ...STUDENTS_DATASET, code: 'tutors', label: 'Tutors' },
            EXPENSES_DATASET,
          ],
        }),
    });
    await open();
    expect(radio('Expenses').checked).toBe(true);
    expect(main().textContent).not.toContain('Tutors');
  });
});

describe('the form (B1, B2)', () => {
  it('class sessions default to the current month, every column, no grouping', async () => {
    stub(OWNER);
    await open('/exports?dataset=class_sessions');
    const month = currentMonth(todayInZone('America/Mexico_City'));
    expect(fieldByLabel(main(), 'From').value).toBe(month.from);
    expect(fieldByLabel(main(), 'To').value).toBe(month.to);
    expect(columnOrder()).toEqual(
      SESSIONS_DATASET.columns.map((column) => column.code),
    );
    expect(select('Group by').value).toBe('');
    expect(radio('CSV').checked).toBe(true);
    // The personal hint (E3) describes the submit button.
    const hint = main().textContent ?? '';
    expect(hint).toContain('Requires confirming your password');
    const button = buttonNamed(main(), 'Request export');
    expect(
      document.getElementById(button.getAttribute('aria-describedby')!)
        ?.textContent,
    ).toContain('Requires confirming your password');
  });

  it('expenses show no password hint', async () => {
    stub(OWNER);
    await open('/exports?dataset=expenses');
    expect(main().textContent).not.toContain('Requires confirming');
    expect(
      buttonNamed(main(), 'Request export').hasAttribute('aria-describedby'),
    ).toBe(false);
  });

  it('reorders with Up and Down, keeps focus on the moved column and announces it', async () => {
    stub(OWNER);
    await open('/exports?dataset=students');
    const up = buttonNamed(main(), 'Move Given names up');
    expect(buttonNamed(main(), 'Move Family names up').disabled).toBe(true);
    await click(up);
    expect(columnOrder().slice(0, 2)).toEqual(['given_names', 'family_names']);
    // At the top, "Up" is disabled: focus goes to its "Down".
    expect(document.activeElement).toBe(
      buttonNamed(main(), 'Move Given names down'),
    );
    expect(main().querySelector('p.sr-only[aria-live]')?.textContent).toBe(
      'Given names: position 1 of 6.',
    );
    await click(buttonNamed(main(), 'Move Education level up'));
    expect(columnOrder().slice(-2)).toEqual([
      'education_level',
      'school_grade',
    ]);
    expect(document.activeElement).toBe(
      buttonNamed(main(), 'Move Education level up'),
    );
    expect(buttonNamed(main(), 'Move School grade down').disabled).toBe(true);
  });

  it('offers only the checked groupable columns, and drops an unchecked grouping', async () => {
    stub(OWNER);
    await open('/exports?dataset=students');
    expect(optionTexts(select('Group by'))).toEqual([
      'No grouping',
      'Active',
      'Minor',
      'School grade',
      'Education level',
    ]);
    setFieldValue(select('Group by'), 'is_minor');
    await settle();
    await click(fieldByLabel(main(), 'Minor'));
    expect(optionTexts(select('Group by'))).toEqual([
      'No grouping',
      'Active',
      'School grade',
      'Education level',
    ]);
    expect(select('Group by').value).toBe('');
    // Reordered columns reorder the options too.
    await click(buttonNamed(main(), 'Move Education level up'));
    expect(optionTexts(select('Group by')).at(-1)).toBe('School grade');
  });

  it('checks the range and the columns before sending (B2)', async () => {
    const api = stub(OWNER);
    await open('/exports?dataset=class_sessions');
    const from = fieldByLabel(main(), 'From');
    const to = fieldByLabel(main(), 'To');

    typeInto(from, '2026-05-10');
    typeInto(to, '2026-05-01');
    await submit();
    expect(api.calls(CREATE)).toHaveLength(0);
    expect(to.getAttribute('aria-invalid')).toBe('true');
    expect(
      document.getElementById(to.getAttribute('aria-describedby')!)
        ?.textContent,
    ).toBe('The end date must be on or after the start date.');
    expect(document.activeElement).toBe(to);

    // 367 days with both ends included: too long, at the start date.
    typeInto(from, '2026-01-01');
    typeInto(to, '2027-01-02');
    await submit();
    expect(api.calls(CREATE)).toHaveLength(0);
    expect(
      document.getElementById(from.getAttribute('aria-describedby')!)
        ?.textContent,
    ).toBe("The range can't be longer than 366 days.");

    typeInto(from, '');
    await submit();
    expect(
      document.getElementById(from.getAttribute('aria-describedby')!)
        ?.textContent,
    ).toBe('Choose a date.');
    expect(api.calls(CREATE)).toHaveLength(0);
  });

  it('needs at least one column', async () => {
    const api = stub(OWNER);
    await open('/exports?dataset=students');
    for (const column of STUDENTS_DATASET.columns) {
      await click(fieldByLabel(main(), column.label));
    }
    await submit();
    expect(api.calls(CREATE)).toHaveLength(0);
    expect(main().textContent).toContain('Choose at least one column.');
    expect(document.activeElement).toBe(fieldByLabel(main(), 'Family names'));
  });
});

describe('the request (B3, E3)', () => {
  const created =
    (job: ExportJob): Responder =>
    () =>
      jsonResponse(202, job);

  it('sends the defaults without columns, then lists and announces the export', async () => {
    const api = stub(OWNER, {
      [CREATE]: created(QUEUED),
      [LIST]: [
        () => jsonResponse(200, jobPage([])),
        () => jsonResponse(200, jobPage([{ ...QUEUED, expires_at: null }])),
      ],
    });
    await open('/exports?dataset=expenses');
    await submit();
    expect(api.calls(CREATE).map((call) => call.body)).toEqual([
      { dataset: 'expenses', format: 'csv', filters: {} },
    ]);
    await settleUntil(() => rowOf(QUEUED.id));
    expect(liveText()).toBe(
      "Expenses export requested. We'll let you know when it's ready.",
    );
    expect(rowOf(QUEUED.id)!.textContent).toContain('In progress');
  });

  it('sends the chosen columns, grouping, format and filters', async () => {
    const api = stub(OWNER, { [CREATE]: created(QUEUED) });
    await open('/exports?dataset=class_sessions');
    await click(radio('Excel'));
    await click(fieldByLabel(main(), 'Weekly series'));
    await click(buttonNamed(main(), 'Move Tutor up'));
    setFieldValue(select('Group by'), 'tutor');
    typeInto(fieldByLabel(main(), 'From'), '2026-01-01');
    typeInto(fieldByLabel(main(), 'To'), '2027-01-01');
    setFieldValue(select('Classroom'), 'room-1');
    setFieldValue(select('Class type'), 'group');
    await click(fieldByLabel(main(), 'Include cancelled'));
    await submit();
    const codes = SESSIONS_DATASET.columns.map((column) => column.code);
    const moved = moveColumn(
      codes.map((code) => ({ code, checked: true })),
      codes.indexOf('tutor'),
      -1,
    )
      .map((choice) => choice.code)
      .filter((code) => code !== 'series');
    expect(api.calls(CREATE).map((call) => call.body)).toEqual([
      {
        dataset: 'class_sessions',
        format: 'xlsx',
        columns: moved,
        group_by: 'tutor',
        filters: {
          from: '2026-01-01',
          to: '2027-01-01',
          include_cancelled: true,
          classroom_id: 'room-1',
          kind: 'group',
        },
      },
    ]);
  });

  it('the tutor filter lists the tutors on a click and sends the chosen one (#548)', async () => {
    const tutorUrls: URL[] = [];
    const api = stub(OWNER, {
      [CREATE]: created(QUEUED),
      'GET /api/v1/schedule/tutor-options': (request) => {
        tutorUrls.push(new URL(request.url));
        return jsonResponse(200, {
          items: [
            { id: 'tutor-1', display_name: 'Ana Tutora', state: 'active' },
            { id: 'tutor-2', display_name: 'Beto Baja', state: 'suspended' },
          ],
          total: 2,
        });
      },
    });
    await open('/exports?dataset=class_sessions');
    // A column checkbox is labelled "Tutor" too: take the combobox.
    const field = [
      ...main().querySelectorAll<HTMLInputElement>('[role="combobox"]'),
    ].find((input) => input.labels?.[0]?.textContent === 'Tutor')!;

    expect(field).toBeDefined();
    act(() => field.focus());
    await click(field);
    await settleUntil(() => comboboxOptions().length === 2);
    // The plain name, without the "(inactive)" marker, as before.
    expect(comboboxOptions().map((o) => o.textContent)).toEqual([
      'Ana Tutora',
      'Beto Baja',
    ]);
    expect(Object.fromEntries(tutorUrls[0]!.searchParams)).toEqual({
      limit: '50',
    });
    await click(comboboxOption('Ana Tutora')!);
    expect(main().textContent).toContain('Tutor: Ana Tutora');
    await submit();

    const bodies = api.calls(CREATE).map((call) => call.body) as {
      dataset: string;
      filters: Record<string, unknown>;
    }[];
    expect(bodies).toHaveLength(1);
    expect(bodies[0]!.dataset).toBe('class_sessions');
    expect(bodies[0]!.filters.tutor_user_id).toBe('tutor-1');
  });

  it('sends the expense statuses in catalogue order and the student filters', async () => {
    const api = stub(OWNER, { [CREATE]: created(QUEUED) });
    await open('/exports?dataset=expenses');
    await click(fieldByLabel(main(), 'Paid'));
    await click(fieldByLabel(main(), 'Unpaid'));
    typeInto(fieldByLabel(main(), 'Expenses from'), '2026-10-01');
    await submit();
    await click(radio('Students'));
    setFieldValue(select('Status'), 'all');
    await submit();
    expect(api.calls(CREATE).map((call) => call.body)).toEqual([
      {
        dataset: 'expenses',
        format: 'csv',
        filters: { status: ['unpaid', 'paid'], incurred_from: '2026-10-01' },
      },
      { dataset: 'students', format: 'csv', filters: { status: 'all' } },
    ]);
  });

  it('409 export_limit_reached says 3 are in progress', async () => {
    stub(OWNER, {
      [CREATE]: () =>
        problemWith(409, 'export_limit_reached', { details: { max: 3 } }),
    });
    await open('/exports?dataset=expenses');
    await submit();
    expect(alerts(main())).toContain(
      'You already have 3 exports in progress. Wait for one to finish.',
    );
  });

  it('422 export_range_invalid shows at its date field', async () => {
    stub(OWNER, {
      [CREATE]: () =>
        problemWith(422, 'export_range_invalid', {
          errors: [
            {
              path: ['body', 'filters', 'incurred_to'],
              code: 'export_range_invalid',
              message: 'Server message',
            },
          ],
        }),
    });
    await open('/exports?dataset=expenses');
    await submit();
    const to = fieldByLabel(main(), 'Expenses to');
    expect(to.getAttribute('aria-invalid')).toBe('true');
    expect(
      document.getElementById(to.getAttribute('aria-describedby')!)
        ?.textContent,
    ).toContain("The date range isn't valid");
    expect(main().textContent).not.toContain('Server message');
  });

  it('reauth_required opens the password dialog and resends once (E3)', async () => {
    const api = stub(OWNER, {
      [CREATE]: [() => problem(403, 'reauth_required'), created(QUEUED)],
      [REAUTH]: () =>
        jsonResponse(200, { reauthenticated_until: '2026-11-05T18:05:00Z' }),
    });
    await open('/exports?dataset=students');
    await submit();
    const dialog = openDialog();
    expect(dialog).not.toBeNull();
    typeInto(fieldByLabel(dialog!, 'Password'), 'my-password');
    await click(buttonNamed(dialog!, 'Confirm'));
    await settleUntil(() => liveText());

    expect(openDialog()).toBeNull();
    const bodies = api.calls(CREATE).map((call) => call.body);
    expect(bodies).toHaveLength(2);
    expect(bodies[1]).toEqual(bodies[0]);
    expect(liveText()).toBe(
      "Expenses export requested. We'll let you know when it's ready.",
    );
  });

  it('cancelling the password dialog sends nothing more and says why', async () => {
    const api = stub(OWNER, {
      [CREATE]: () => problem(403, 'reauth_required'),
    });
    await open('/exports?dataset=students');
    await submit();
    await click(buttonNamed(openDialog()!, 'Cancel'));
    expect(api.calls(CREATE)).toHaveLength(1);
    expect(alerts(main()).join(' ')).toContain(
      'Confirm your password to continue.',
    );
  });
});

describe('the history (C1)', () => {
  it('shows each status: a download link while ready, then expired, or why it failed', async () => {
    const ready = { ...READY, expires_at: hoursFromNow(5.5) };
    const soon = {
      ...READY,
      id: '00000000-0000-4000-8000-0000000000f1',
      expires_at: hoursFromNow(0.25),
    };
    const pastExpiry = {
      ...READY,
      id: '00000000-0000-4000-8000-0000000000f2',
      expires_at: hoursFromNow(-0.1),
    };
    stub(OWNER, {
      [LIST]: () =>
        jsonResponse(200, jobPage([ready, soon, pastExpiry, FAILED, EXPIRED])),
    });
    await open();

    const link = rowOf(ready.id)!.querySelector('a')!;
    expect(link.getAttribute('href')).toBe(
      `/api/v1/exports/${ready.id}/download`,
    );
    expect(link.getAttribute('download')).toBe(ready.file_name);
    expect(link.getAttribute('aria-label')).toBe(`Download ${ready.file_name}`);
    expect(rowOf(ready.id)!.textContent).toContain('expires in 5 h');
    expect(rowOf(ready.id)!.textContent).toContain('1,234');
    expect(rowOf(ready.id)!.textContent).toContain('Ready');
    expect(rowOf(soon.id)!.textContent).toMatch(/expires in 1[45] min/);

    for (const id of [pastExpiry.id, EXPIRED.id]) {
      expect(rowOf(id)!.querySelector('a')).toBeNull();
      expect(rowOf(id)!.textContent).toContain('Expired');
    }
    expect(rowOf(FAILED.id)!.querySelector('a')).toBeNull();
    expect(rowOf(FAILED.id)!.textContent).toContain('Failed');
    expect(rowOf(FAILED.id)!.textContent).toContain(
      'More than 10,000 rows: narrow the range or the filters.',
    );
    expect(rowOf(FAILED.id)!.textContent).toContain('Classes');
  });

  it.each([
    ['size_limit_exceeded', 'The file is over 16 MB'],
    ['permission_lost', 'You no longer have access to this data.'],
    ['error', "It couldn't be created."],
  ] as const)('labels %s', async (code, text) => {
    stub(OWNER, {
      [LIST]: () =>
        jsonResponse(200, jobPage([{ ...FAILED, error_code: code }])),
    });
    await open();
    expect(rowOf(FAILED.id)!.textContent).toContain(text);
  });

  it('reads Spanish labels in Spanish', async () => {
    applyLanguage('es');
    stub(OWNER, {
      [LIST]: () => jsonResponse(200, jobPage([FAILED, EXPIRED])),
    });
    app = await renderApp('/exports');
    await settleUntil(() => rowOf(FAILED.id));
    expect(rowOf(FAILED.id)!.textContent).toContain(
      'Más de 10 000 filas: acota el rango o los filtros.',
    );
    expect(rowOf(EXPIRED.id)!.textContent).toContain('Caducada');
  });

  it('formats sizes', () => {
    expect(formatBytes(512, 'en')).toBe('0.5 kB');
    expect(formatBytes(3, 'en')).toBe('0.1 kB');
    expect(formatBytes(48_300, 'es')).toBe('47,2 kB');
    expect(formatBytes(48_300, 'en')).toBe('47.2 kB');
    expect(formatBytes(3 * 1024 * 1024, 'en')).toBe('3 MB');
  });
});

describe('polling (C2, E2)', () => {
  // The clock is faked but keeps moving (shouldAdvanceTime), so fetch
  // bodies and React Query's notifications settle as in the other tests.
  async function advance(ms: number) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ms);
    });
    await settle();
  }

  it('polls every 3 s while an export is queued, announces it and stops', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = stub(OWNER, {
      [LIST]: [
        () => jsonResponse(200, jobPage([QUEUED])),
        () => jsonResponse(200, jobPage([QUEUED])),
        () =>
          jsonResponse(
            200,
            jobPage([
              { ...READY, id: QUEUED.id, expires_at: hoursFromNow(24) },
            ]),
          ),
      ],
    });
    await open();
    expect(api.calls(LIST)).toHaveLength(1);
    await advance(POLL_INTERVAL_MS);
    expect(api.calls(LIST)).toHaveLength(2);
    await advance(POLL_INTERVAL_MS);
    expect(api.calls(LIST)).toHaveLength(3);
    expect(liveText()).toBe('The Expenses export is ready.');
    expect(rowOf(QUEUED.id)!.querySelector('a')).not.toBeNull();

    await advance(POLL_INTERVAL_MS * 5);
    expect(api.calls(LIST)).toHaveLength(3);
  });

  it('announces a failure', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    stub(OWNER, {
      [LIST]: [
        () => jsonResponse(200, jobPage([QUEUED])),
        () =>
          jsonResponse(
            200,
            jobPage([{ ...FAILED, id: QUEUED.id, dataset: 'expenses' }]),
          ),
      ],
    });
    await open();
    await advance(POLL_INTERVAL_MS);
    expect(liveText()).toBe('The Expenses export failed.');
  });

  it('never polls when nothing is queued', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = stub(OWNER, {
      [LIST]: () => jsonResponse(200, jobPage([EXPIRED])),
    });
    await open();
    await advance(POLL_INTERVAL_MS * 4);
    expect(api.calls(LIST)).toHaveLength(1);
  });

  it('stops after 10 minutes and says a notification will follow', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = stub(OWNER, {
      [LIST]: () => jsonResponse(200, jobPage([QUEUED])),
    });
    await open();
    await advance(POLL_TIMEOUT_MS);
    expect(liveText()).toBe("Still in progress; you'll get a notification.");
    const calls = api.calls(LIST).length;
    expect(calls).toBeGreaterThan(150);
    await advance(POLL_INTERVAL_MS * 5);
    expect(api.calls(LIST)).toHaveLength(calls);
  });
});

describe('the Export links (A3)', () => {
  const linkOf = (dataset: ExportDatasetCode) =>
    main()?.querySelector<HTMLAnchorElement>(
      `a[data-export-link="${dataset}"]`,
    ) ?? null;

  it.each([
    ['an Owner', OWNER, true],
    ['a Coordinator', COORDINATOR, true],
    ['a tutor', TUTOR, false],
  ] as const)('/students: %s sees it: %s', async (_who, as, visible) => {
    stub(as, {
      'GET /api/v1/students': () =>
        jsonResponse(200, { items: [], total: 0, limit: 25, offset: 0 }),
    });
    app = await renderApp('/students');
    await settleUntil(() => main()?.querySelector('h1'));
    await settle();
    expect(linkOf('students') !== null).toBe(visible);
    if (visible) {
      expect(linkOf('students')!.getAttribute('href')).toBe(
        '/exports?dataset=students',
      );
      expect(linkOf('students')!.textContent).toBe('Export');
    }
  });

  it.each([
    ['an Owner', OWNER, true],
    ['Finance', FINANCE, true],
  ] as const)('/schedule: %s sees it: %s', async (_who, as, visible) => {
    stub(as);
    app = await renderApp('/schedule');
    await settleUntil(() => main()?.querySelector('h1'));
    await settle();
    expect(linkOf('class_sessions')?.getAttribute('href') ?? null).toBe(
      visible ? '/exports?dataset=class_sessions' : null,
    );
  });

  it.each([
    ['Finance', FINANCE, true],
    // expense.configure only (the Administrator, ADR-0017): no expense.view.
    ['an Administrator', ADMINISTRATOR, false],
  ] as const)(
    '/expenses/records: %s sees it: %s',
    async (_who, as, visible) => {
      stub(as);
      app = await renderApp('/expenses/records');
      await settleUntil(() => main()?.querySelector('h1'));
      await settle();
      expect(linkOf('expenses')?.getAttribute('href') ?? null).toBe(
        visible ? '/exports?dataset=expenses' : null,
      );
    },
  );

  it('the link opens /exports with its dataset chosen', async () => {
    stub(OWNER, {
      'GET /api/v1/students': () =>
        jsonResponse(200, { items: [], total: 0, limit: 25, offset: 0 }),
    });
    app = await renderApp('/students');
    await settleUntil(() => linkOf('students'));
    await click(linkOf('students')!);
    await settleUntil(() => main().querySelector('form'));
    expect(app.location()).toBe('/exports?dataset=students');
    expect(radio('Students').checked).toBe(true);
  });
});

describe('rules', () => {
  const values = (from: string, to: string) => ({
    dataset: 'class_sessions' as const,
    format: 'csv' as const,
    columns: [{ code: 'date', checked: true }],
    groupBy: '',
    sessions: {
      from,
      to,
      tutor: null,
      student: null,
      classroom: '',
      kind: '' as const,
      includeCancelled: false,
    },
    expenses: { incurredFrom: '', incurredTo: '', statuses: [] },
    students: { status: 'active' as const, educationLevel: '' },
  });

  it('allows 366 days with both ends included, as the API (#124 D5)', () => {
    expect(validate(values('2026-01-01', '2027-01-01'))).toEqual({});
    expect(validate(values('2026-01-01', '2027-01-02'))).toEqual({
      from: 'rangeTooLong',
    });
    expect(validate(values('2026-03-02', '2026-03-01'))).toEqual({
      to: 'rangeReversed',
    });
    expect(validate(values('2026-03-01', '2026-03-01'))).toEqual({});
  });

  it('the current month runs from its first to its last day', () => {
    expect(currentMonth('2026-02-14' as never)).toEqual({
      from: '2026-02-01',
      to: '2026-02-28',
    });
    expect(currentMonth('2026-12-31' as never)).toEqual({
      from: '2026-12-01',
      to: '2026-12-31',
    });
  });
});
