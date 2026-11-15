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
import type { LocalDate } from '@/lib/time';
import type { ExportParameters } from './api';
import {
  ALL_DATASETS,
  EXPENSES_DATASET,
  jobPage,
  QUEUED,
  SESSIONS_DATASET,
  SETUP_NOT_RUNNABLE,
  SETUP_OWN,
  SETUP_SHARED,
  STUDENTS_DATASET,
} from './fixtures';
import { defaultValues, exportParameters, valuesFromParameters } from './rules';
import { setupProblem, type ReportSetup } from './setups';
// Loaded up front, so the lazy route resolves without real I/O.
import './ExportsPage';

// M8-08 (#125 D): "Saved setups" on /exports with a stubbed fetch: the list
// (D1), Run through the request call (its password dialog and limit), Load
// into the form (an unchanged submit posts the same JSON as Run), the save,
// update, edit and delete dialogs and their problem mapping (D2–D4).

const DATASETS = 'GET /api/v1/exports/datasets';
const LIST = 'GET /api/v1/exports';
const CREATE = 'POST /api/v1/exports';
const REAUTH = 'POST /api/v1/auth/reauthenticate';
const SETUPS = 'GET /api/v1/report-setups';
const SAVE = 'POST /api/v1/report-setups';
const setupRoute = (method: 'PATCH' | 'DELETE', setup: ReportSetup) =>
  `${method} /api/v1/report-setups/${setup.id}`;

const OWNER: SessionUserBody = {
  ...TEST_USER,
  roles: ['owner'],
  permissions: ['student.view', 'schedule.view', 'expense.view'],
};

let app: RenderedApp | undefined;

const main = () => app!.container.querySelector('main')!;
const section = () =>
  [...main().querySelectorAll('section')].find(
    (candidate) =>
      candidate.querySelector('h2')?.textContent === 'Saved setups',
  )!;
const rowOf = (setup: ReportSetup) =>
  main().querySelector<HTMLLIElement>(`li[data-setup-id="${setup.id}"]`)!;
const liveText = () =>
  main().querySelector('p[aria-live="polite"]:not(.sr-only)')?.textContent ??
  '';
const columnOrder = () =>
  [...main().querySelectorAll('li[data-column]')].map((li) =>
    li.getAttribute('data-column'),
  );
const selectIn = (root: ParentNode, label: string): HTMLSelectElement => {
  const element = [...root.querySelectorAll('label')]
    .filter((candidate) => candidate.textContent === label)
    .map((candidate) => document.getElementById(candidate.htmlFor))
    .find((candidate) => candidate instanceof HTMLSelectElement);
  if (!(element instanceof HTMLSelectElement)) {
    throw new Error(`No select labelled "${label}"`);
  }
  return element;
};

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

const setupList =
  (...items: ReportSetup[]): Responder =>
  () =>
    jsonResponse(200, { items });

function stub(
  routes: Record<string, Responder | Responder[]> = {},
  as: SessionUserBody = OWNER,
): ApiStub {
  return stubApi({
    'GET /api/v1/auth/me': meAs(as),
    [DATASETS]: () => jsonResponse(200, { items: ALL_DATASETS }),
    [LIST]: () => jsonResponse(200, jobPage([])),
    [SETUPS]: setupList(SETUP_OWN, SETUP_SHARED, SETUP_NOT_RUNNABLE),
    'GET /api/v1/classrooms': () =>
      jsonResponse(200, { items: [], total: 0, limit: 100, offset: 0 }),
    'GET /api/v1/education-levels': () =>
      jsonResponse(200, { items: [], total: 0, limit: 100, offset: 0 }),
    ...routes,
  });
}

async function open(path = '/exports') {
  app = await renderApp(path);
  await settleUntil(() => main()?.querySelector('form'));
  await settleUntil(
    () =>
      main().querySelector('li[data-setup-id]') ||
      main().textContent?.includes("You don't have any saved setups yet."),
  );
  await settle();
}

beforeEach(() => {
  applyLanguage('en');
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  app?.unmount();
  app = undefined;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('the list (D1)', () => {
  it('shows each setup with its dataset, format, sharing and creator', async () => {
    stub();
    await open();
    const own = rowOf(SETUP_OWN).textContent;
    expect(own).toContain('Monthly expenses');
    expect(own).toContain('Expenses · Excel');
    expect(own).toContain('Shared with Finance staff');
    expect(own).not.toContain('by ');
    const shared = rowOf(SETUP_SHARED).textContent;
    expect(shared).toContain('Students · CSV');
    expect(shared).toContain('Shared with Owner');
    expect(shared).toContain('by Carla Coordinator');
    // Only the creator edits or deletes.
    expect(buttonNamed(rowOf(SETUP_OWN), 'Edit Monthly expenses')).toBeTruthy();
    expect(rowOf(SETUP_SHARED).textContent).not.toContain('Edit');
    expect(rowOf(SETUP_SHARED).textContent).not.toContain('Delete');
  });

  it('a setup that can no longer run says why, and may only be deleted', async () => {
    stub();
    await open();
    const row = rowOf(SETUP_NOT_RUNNABLE);
    expect(row.textContent).toContain('You no longer have access to this data');
    const names = [...row.querySelectorAll('button')].map(
      (button) => button.getAttribute('aria-label') ?? button.textContent,
    );
    expect(names).toEqual(['Delete Old sessions']);
  });

  it('shows the empty state', async () => {
    stub({ [SETUPS]: setupList() });
    await open();
    expect(section().textContent).toContain(
      "You don't have any saved setups yet.",
    );
  });

  it('every action button is at least 44 px tall on a phone', async () => {
    stub();
    await open();
    for (const button of rowOf(SETUP_OWN).querySelectorAll('button')) {
      expect(button.className).toContain('min-h-11');
    }
  });
});

describe('run (D2)', () => {
  it('posts the setup parameters unchanged and announces the export', async () => {
    const api = stub({
      [CREATE]: () => jsonResponse(202, QUEUED),
    });
    await open();
    await click(buttonNamed(rowOf(SETUP_OWN), 'Run Monthly expenses'));
    await settleUntil(() => liveText());
    expect(api.calls(CREATE).map((call) => call.body)).toEqual([
      SETUP_OWN.parameters,
    ]);
    expect(liveText()).toBe(
      "Expenses export requested. We'll let you know when it's ready.",
    );
  });

  it('a personal dataset asks for the password and resends once', async () => {
    const api = stub({
      [CREATE]: [
        () => problem(403, 'reauth_required'),
        () => jsonResponse(202, QUEUED),
      ],
      [REAUTH]: () =>
        jsonResponse(200, { reauthenticated_until: '2026-11-05T18:05:00Z' }),
    });
    await open();
    await click(buttonNamed(rowOf(SETUP_SHARED), 'Run Active students'));
    const dialog = openDialog();
    expect(dialog).not.toBeNull();
    typeInto(fieldByLabel(dialog!, 'Password'), 'my-password');
    await click(buttonNamed(dialog!, 'Confirm'));
    await settleUntil(() => liveText());
    expect(api.calls(CREATE).map((call) => call.body)).toEqual([
      SETUP_SHARED.parameters,
      SETUP_SHARED.parameters,
    ]);
  });

  it('409 export_limit_reached shows the request limit', async () => {
    stub({
      [CREATE]: () =>
        problemWith(409, 'export_limit_reached', { details: { max: 3 } }),
    });
    await open();
    await click(buttonNamed(rowOf(SETUP_OWN), 'Run Monthly expenses'));
    await settleUntil(() => alerts(section()).length > 0);
    expect(alerts(section())).toContain(
      'You already have 3 exports in progress. Wait for one to finish.',
    );
  });
});

describe('load into the form (D2, D3)', () => {
  it('fills the form, moves the focus to it, and an unchanged submit posts what Run posts', async () => {
    const api = stub({ [CREATE]: () => jsonResponse(202, QUEUED) });
    await open();
    await click(
      buttonNamed(rowOf(SETUP_OWN), 'Load into the form Monthly expenses'),
    );
    await settle();
    expect(fieldByLabel(main(), 'Expenses').checked).toBe(true);
    expect(fieldByLabel(main(), 'Excel').checked).toBe(true);
    expect(columnOrder().slice(0, 4)).toEqual([
      'vendor',
      'amount',
      'incurred_on',
      'status',
    ]);
    expect(fieldByLabel(main(), 'Description').checked).toBe(false);
    expect(selectIn(main(), 'Group by').value).toBe('vendor');
    expect(fieldByLabel(main(), 'Expenses from').value).toBe('2026-10-01');
    expect(fieldByLabel(main(), 'Paid').checked).toBe(true);
    expect(document.activeElement?.textContent).toBe('New export');
    expect(liveText()).toBe('Setup “Monthly expenses” loaded into the form.');

    await click(buttonNamed(main(), 'Request export'));
    await click(buttonNamed(rowOf(SETUP_OWN), 'Run Monthly expenses'));
    const [bySubmit, byRun] = api.calls(CREATE).map((call) => call.body);
    expect(bySubmit).toEqual(SETUP_OWN.parameters);
    expect(JSON.stringify(bySubmit)).toBe(JSON.stringify(byRun));
  });
});

describe('save (D3, D4)', () => {
  async function openSave() {
    await click(buttonNamed(main(), 'Save setup'));
    const dialog = openDialog();
    expect(dialog).not.toBeNull();
    return dialog!;
  }

  it('saves the body the request would send, shared with a role', async () => {
    const saved = { ...SETUP_OWN, id: 'new-setup', name: 'Class list' };
    const api = stub({
      [SAVE]: () => jsonResponse(201, saved),
      [CREATE]: () => jsonResponse(202, QUEUED),
      [SETUPS]: [setupList(), setupList(saved)],
    });
    await open('/exports?dataset=expenses');
    await click(fieldByLabel(main(), 'Unpaid'));
    const dialog = await openSave();
    expect(dialog.textContent).not.toContain('Update');
    typeInto(fieldByLabel(dialog, 'Name'), '  Class list ');
    setFieldValue(selectIn(dialog, 'Share with'), 'academic_coordinator');
    await click(buttonNamed(dialog, 'Save as new'));
    await settleUntil(() => !openDialog());
    await click(buttonNamed(main(), 'Request export'));

    const [request] = api.calls(CREATE).map((call) => call.body);
    expect(api.calls(SAVE).map((call) => call.body)).toEqual([
      {
        name: 'Class list',
        parameters: request,
        shared_role: 'academic_coordinator',
      },
    ]);
    expect(request).toEqual({
      dataset: 'expenses',
      format: 'csv',
      filters: { status: ['unpaid'] },
    });
    expect(liveText()).toContain('Expenses export requested.');
    await settleUntil(() => api.calls(SETUPS).length === 2);
  });

  it('the client checks the form and the name before sending', async () => {
    const api = stub();
    await open('/exports?dataset=class_sessions');
    typeInto(fieldByLabel(main(), 'From'), '');
    await click(buttonNamed(main(), 'Save setup'));
    expect(openDialog()).toBeNull();
    typeInto(fieldByLabel(main(), 'From'), '2026-01-01');
    const dialog = await openSave();
    await click(buttonNamed(dialog, 'Save as new'));
    const name = fieldByLabel(dialog, 'Name');
    expect(name.getAttribute('aria-invalid')).toBe('true');
    expect(
      document.getElementById(name.getAttribute('aria-describedby')!)
        ?.textContent,
    ).toBe('Enter a name.');
    typeInto(name, 'x'.repeat(81));
    await click(buttonNamed(dialog, 'Save as new'));
    expect(
      document.getElementById(name.getAttribute('aria-describedby')!)
        ?.textContent,
    ).toBe('Use 80 characters at most.');
    expect(api.calls(SAVE)).toHaveLength(0);
  });

  it('maps the name taken and the limit', async () => {
    stub({
      [SAVE]: [
        () => problemWith(409, 'report_setup_name_taken'),
        () =>
          problemWith(409, 'report_setup_limit_reached', {
            details: { max: 50 },
          }),
      ],
    });
    await open('/exports?dataset=expenses');
    const dialog = await openSave();
    const name = fieldByLabel(dialog, 'Name');
    typeInto(name, 'Monthly expenses');
    await click(buttonNamed(dialog, 'Save as new'));
    await settleUntil(() => name.getAttribute('aria-invalid') === 'true');
    expect(
      document.getElementById(name.getAttribute('aria-describedby')!)
        ?.textContent,
    ).toBe('You already have a setup with this name.');
    expect(document.activeElement).toBe(name);
    await click(buttonNamed(dialog, 'Save as new'));
    await settleUntil(() => alerts(dialog).length > 0);
    expect(alerts(dialog)).toContain(
      'You already have 50 setups; delete one first.',
    );
    expect(dialog.textContent).not.toContain('Server detail');
  });

  it('a server range problem closes the dialog and shows at the date field', async () => {
    stub({
      [SAVE]: () =>
        problemWith(422, 'export_range_invalid', {
          errors: [
            {
              path: ['body', 'parameters', 'filters', 'incurred_to'],
              code: 'export_range_invalid',
              message: 'Server message',
            },
          ],
        }),
    });
    await open('/exports?dataset=expenses');
    const dialog = await openSave();
    typeInto(fieldByLabel(dialog, 'Name'), 'Range');
    await click(buttonNamed(dialog, 'Save as new'));
    await settleUntil(() => !openDialog());
    const to = fieldByLabel(main(), 'Expenses to');
    expect(to.getAttribute('aria-invalid')).toBe('true');
    expect(
      document.getElementById(to.getAttribute('aria-describedby')!)
        ?.textContent,
    ).toContain("The date range isn't valid");
    expect(main().textContent).not.toContain('Server message');
  });

  it('a loaded setup of the caller may be updated with its version', async () => {
    const updated = { ...SETUP_OWN, version: 4 };
    const api = stub({
      [setupRoute('PATCH', SETUP_OWN)]: () => jsonResponse(200, updated),
    });
    await open();
    await click(
      buttonNamed(rowOf(SETUP_OWN), 'Load into the form Monthly expenses'),
    );
    await settle();
    await click(fieldByLabel(main(), 'Description'));
    const dialog = await openSave();
    expect(fieldByLabel(dialog, 'Name').value).toBe('Monthly expenses');
    expect(selectIn(dialog, 'Share with').value).toBe('finance_staff');
    await click(buttonNamed(dialog, 'Update “Monthly expenses”'));
    await settleUntil(() => !openDialog());
    expect(
      api.calls(setupRoute('PATCH', SETUP_OWN)).map((c) => c.body),
    ).toEqual([
      {
        version: 3,
        name: 'Monthly expenses',
        shared_role: 'finance_staff',
        parameters: {
          ...SETUP_OWN.parameters,
          columns: ['vendor', 'amount', 'incurred_on', 'status', 'description'],
        },
      },
    ]);
    expect(liveText()).toBe('Setup “Monthly expenses” updated.');
  });

  it("another user's loaded setup offers no update", async () => {
    stub();
    await open();
    await click(
      buttonNamed(rowOf(SETUP_SHARED), 'Load into the form Active students'),
    );
    await settle();
    const dialog = await openSave();
    expect(dialog.textContent).not.toContain('Update');
    expect(fieldByLabel(dialog, 'Name').value).toBe('');
  });
});

describe('edit and delete (D2, D4)', () => {
  it('edits the name and sharing with the version', async () => {
    const api = stub({
      [setupRoute('PATCH', SETUP_OWN)]: () =>
        jsonResponse(200, { ...SETUP_OWN, name: 'Renamed', shared_role: null }),
    });
    await open();
    const opener = buttonNamed(rowOf(SETUP_OWN), 'Edit Monthly expenses');
    opener.focus();
    await click(opener);
    const dialog = openDialog()!;
    typeInto(fieldByLabel(dialog, 'Name'), 'Renamed');
    setFieldValue(selectIn(dialog, 'Share with'), '');
    await click(buttonNamed(dialog, 'Save changes'));
    await settleUntil(() => !openDialog());
    expect(
      api.calls(setupRoute('PATCH', SETUP_OWN)).map((c) => c.body),
    ).toEqual([{ version: 3, name: 'Renamed', shared_role: null }]);
    expect(liveText()).toBe('Setup “Renamed” updated.');
    expect(document.activeElement).toBe(opener);
  });

  it('a version conflict reloads the list with a message', async () => {
    const api = stub({
      [setupRoute('PATCH', SETUP_OWN)]: () => problem(409, 'version_conflict'),
    });
    await open();
    const before = api.calls(SETUPS).length;
    await click(buttonNamed(rowOf(SETUP_OWN), 'Edit Monthly expenses'));
    const dialog = openDialog()!;
    await click(buttonNamed(dialog, 'Save changes'));
    await settleUntil(() => !openDialog());
    await settleUntil(() => api.calls(SETUPS).length > before);
    expect(alerts(section())).toContain(
      'This setup changed in another tab. We reloaded the list; try again.',
    );
  });

  it('deletes after a confirmation and announces it', async () => {
    const api = stub({
      [setupRoute('DELETE', SETUP_OWN)]: () =>
        new Response(null, { status: 204 }),
      [SETUPS]: [setupList(SETUP_OWN, SETUP_SHARED), setupList(SETUP_SHARED)],
    });
    await open();
    await click(buttonNamed(rowOf(SETUP_OWN), 'Delete Monthly expenses'));
    const dialog = openDialog()!;
    expect(dialog.textContent).toContain('Delete “Monthly expenses”?');
    await click(buttonNamed(dialog, 'Delete'));
    await settleUntil(() => !openDialog());
    expect(api.calls(setupRoute('DELETE', SETUP_OWN))).toHaveLength(1);
    expect(liveText()).toBe('Setup “Monthly expenses” deleted.');
    await settleUntil(
      () => !main().querySelector(`li[data-setup-id="${SETUP_OWN.id}"]`),
    );
  });
});

describe('the rules', () => {
  const today = '2026-11-05' as LocalDate;

  it.each([
    ['expenses', EXPENSES_DATASET],
    ['students', STUDENTS_DATASET],
    ['class_sessions', SESSIONS_DATASET],
  ] as const)(
    '%s: the form values of the builder output build it again',
    (code, dataset) => {
      const values = defaultValues(code, dataset.columns, today, 'xlsx');
      values.columns = [...values.columns].reverse();
      values.columns[1] = { ...values.columns[1]!, checked: false };
      values.groupBy = dataset.columns.find((c) => c.groupable)!.code;
      values.sessions.tutor = { id: 'tutor-1', name: 'Tina' };
      values.sessions.includeCancelled = true;
      values.expenses.statuses = ['paid'];
      values.expenses.incurredTo = '2026-10-31';
      values.students.status = 'all';
      const body = exportParameters(values, dataset.columns);

      const again = exportParameters(
        valuesFromParameters(body, dataset.columns, today, 'saved'),
        dataset.columns,
      );

      expect(JSON.stringify(again)).toBe(JSON.stringify(body));
    },
  );

  it('the default columns stay implicit', () => {
    const body: ExportParameters = {
      dataset: 'students',
      format: 'csv',
      filters: { status: 'active' },
    };
    const values = valuesFromParameters(
      body,
      STUDENTS_DATASET.columns,
      today,
      'saved',
    );
    expect(values.columns.every((choice) => choice.checked)).toBe(true);
    expect(exportParameters(values, STUDENTS_DATASET.columns)).toEqual(body);
  });

  it('maps the setup problems', () => {
    expect(setupProblem(new Error('x'))).toEqual({ kind: 'other' });
  });
});
